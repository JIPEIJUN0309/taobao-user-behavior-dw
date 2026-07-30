#!/usr/bin/env python3
# =====================================================================
# 数据倾斜受控实验 / Data Skew Controlled Experiment
# 作用: 在 DWD 明细数据上构造热点 key, 对比不同聚合方式的倾斜表现与解法
# Purpose: build a hot key on DWD detail data, compare skew behavior
#          across aggregation strategies and their mitigations
# 核心技术: map 端预聚合 / map-side combine, 两阶段去重 / two-phase dedup,
#          HLL 近似 / HyperLogLog approximation
# 用法 / usage:
#   python spark/skew_experiment.py --input <DWD 路径 / DWD path>
# =====================================================================

import argparse
import time

from pyspark.sql import SparkSession, functions as F

# 实验参数 / experiment parameters
HOT_ITEM = 999999999   # 人造爆款商品 ID / synthetic hot item id
SKEW_FRAC = 0.3        # 改写比例 / fraction of rows rewritten to the hot key
SEED = 42              # 固定随机种子保证可复现 / fixed seed for reproducibility


def build_spark(shuffle_partitions: int) -> SparkSession:
    """构建 SparkSession, 关闭 AQE 以观察未经自动优化的真实表现。
    Build SparkSession with AQE disabled to observe raw, unoptimized behavior."""
    spark = (
        SparkSession.builder
        .appName("taobao-dw-skew-experiment")
        .config("spark.sql.shuffle.partitions", str(shuffle_partitions))
        .config("spark.sql.adaptive.enabled", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def timeit(df, label: str) -> float:
    """用 noop 数据源触发完整 shuffle, 不产生磁盘 IO, 只测计算耗时。
    Trigger a full shuffle via the noop sink - no disk IO, pure compute timing."""
    t0 = time.time()
    df.write.format("noop").mode("overwrite").save()
    cost = time.time() - t0
    print(f"[{label}] 耗时 / elapsed: {cost:.1f}s")
    return cost


def profile_distribution(df, label: str) -> None:
    """探查 key 分布, 判断是否天然倾斜。
    Profile key distribution to decide whether skew exists naturally."""
    dist = df.groupBy("item_id").agg(F.count("*").alias("cnt"))
    print(f"\n---- {label}: Top 5 keys ----")
    dist.orderBy(F.desc("cnt")).show(5, truncate=False)
    dist.selectExpr(
        "percentile_approx(cnt, 0.5)  AS p50",
        "percentile_approx(cnt, 0.99) AS p99",
        "max(cnt)                     AS max_cnt",
    ).show()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="数据倾斜受控实验 / data skew controlled experiment")
    parser.add_argument(
        "--input", required=True,
        help="DWD 明细数据路径 / path to DWD detail data (ORC or Parquet)")
    parser.add_argument(
        "--format", default="parquet", choices=["parquet", "orc"],
        help="输入格式 / input format")
    parser.add_argument(
        "--skew-path", default="./_skew_demo",
        help="倾斜数据集落盘路径 / where to persist the skewed dataset")
    parser.add_argument(
        "--shuffle-partitions", type=int, default=200,
        help="shuffle 分区数 / number of shuffle partitions")
    args = parser.parse_args()

    spark = build_spark(args.shuffle_partitions)

    # -----------------------------------------------------------------
    # 步骤 1: 探查真实分布 -- 先证明原始数据是否倾斜
    # Step 1: profile the real distribution - is the data skewed at all?
    # -----------------------------------------------------------------
    df_raw = spark.read.format(args.format).load(args.input)
    print(f"\nDWD 行数 / DWD rows: {df_raw.count():,}")
    profile_distribution(df_raw, "真实数据 / real data")

    # -----------------------------------------------------------------
    # 步骤 2: 构造受控倾斜 -- 抽样改写为单一爆款商品
    # Step 2: build controlled skew - rewrite sampled rows to one hot item
    # 落盘保证后续各方案读同一份数据, 排除重算干扰
    # Persisted so every strategy reads identical data (fair comparison)
    # -----------------------------------------------------------------
    hot = (df_raw
           .sample(fraction=SKEW_FRAC, seed=SEED)
           .withColumn("item_id", F.lit(HOT_ITEM).cast("bigint")))
    (df_raw.unionByName(hot)
           .write.mode("overwrite").parquet(args.skew_path))

    df = spark.read.parquet(args.skew_path)
    total = df.count()
    hot_cnt = df.filter(F.col("item_id") == HOT_ITEM).count()
    print(f"\n倾斜数据集 / skewed dataset: {total:,} rows")
    print(f"热点 key 占比 / hot key share: {hot_cnt / total * 100:.2f}% ({hot_cnt:,} rows)")
    print(f"热点 key / 平均分区 / vs average partition: "
          f"{hot_cnt / (total / args.shuffle_partitions):.0f}x")

    # -----------------------------------------------------------------
    # 步骤 3: 对照组 -- 可加聚合 (count / sum)
    # Step 3: control group - additive aggregation
    # 预期不倾斜: map 端预聚合会在 shuffle 前把热点 key 压扁
    # Expected: no skew - map-side combine flattens the hot key pre-shuffle
    # -----------------------------------------------------------------
    additive = df.groupBy("item_id").agg(
        F.count("*").alias("cnt"),
        F.sum(F.when(F.col("behavior_type") == "buy", 1).otherwise(0)).alias("buy_cnt"),
    )
    t_additive = timeit(additive, "对照组-可加聚合 / control-additive")

    # -----------------------------------------------------------------
    # 步骤 4: 基线 -- 不可加聚合 (count distinct), 倾斜在此显现
    # Step 4: baseline - non-additive aggregation, where skew actually bites
    # 业务含义: 每个商品的独立访客数 UV / business meaning: per-item UV
    # -----------------------------------------------------------------
    uv_naive = df.groupBy("item_id").agg(
        F.countDistinct("user_id").alias("uv"))
    t_naive = timeit(uv_naive, "基线-countDistinct / baseline")

    # -----------------------------------------------------------------
    # 步骤 5: 解法 A -- 两阶段去重 (精确)
    # Step 5: fix A - two-phase dedup (exact)
    # 先按 (item_id, user_id) 复合键 shuffle, 热点被 hash 打散;
    # 去重后数据量骤减, 再按 item_id 计数时已是可加聚合
    # Shuffle on the composite key first, spreading the hot key; the second
    # pass is a plain count, so map-side combine kicks back in
    # -----------------------------------------------------------------
    uv_2phase = (df.select("item_id", "user_id").distinct()
                   .groupBy("item_id").agg(F.count("*").alias("uv")))
    t_2phase = timeit(uv_2phase, "解法A-两阶段去重 / two-phase dedup")

    # -----------------------------------------------------------------
    # 步骤 6: 解法 B -- HLL 近似
    # Step 6: fix B - HyperLogLog approximation
    # HLL 草图可合并, 于是重新变回可加聚合, 倾斜与 shuffle 开销同时消失
    # Sketches are mergeable, restoring map-side combine: skew and shuffle
    # overhead vanish together
    # -----------------------------------------------------------------
    uv_hll = df.groupBy("item_id").agg(
        F.approx_count_distinct("user_id").alias("uv"))
    t_hll = timeit(uv_hll, "解法B-HLL近似 / HLL approximation")

    # -----------------------------------------------------------------
    # 步骤 7: 正确性交叉验证 + 误差评估
    # Step 7: correctness cross-check + error assessment
    # -----------------------------------------------------------------
    diff_a = uv_naive.exceptAll(uv_2phase).count()
    diff_b = uv_2phase.exceptAll(uv_naive).count()
    print(f"\n[正确性 / correctness] 两阶段去重差异行数 / diff rows: {diff_a} / {diff_b}")
    assert diff_a == 0 and diff_b == 0, "两阶段去重结果与基线不一致 / mismatch"

    err = (uv_naive.join(uv_hll.withColumnRenamed("uv", "uv_hll"), "item_id")
                   .selectExpr("avg(abs(uv_hll - uv) / uv) AS avg_err",
                               "max(abs(uv_hll - uv) / uv) AS max_err"))
    err.show()

    # -----------------------------------------------------------------
    # 汇总 / summary
    # -----------------------------------------------------------------
    print("\n================ 实验汇总 / summary ================")
    print(f"可加聚合(对照)    / additive (control) : {t_additive:6.1f}s")
    print(f"countDistinct基线 / baseline          : {t_naive:6.1f}s  1.00x")
    print(f"两阶段去重        / two-phase dedup    : {t_2phase:6.1f}s  "
          f"{t_naive / t_2phase:.2f}x  (精确 / exact)")
    print(f"HLL 近似          / HLL approximation  : {t_hll:6.1f}s  "
          f"{t_naive / t_hll:.2f}x  (近似 / approximate)")

    spark.stop()


if __name__ == "__main__":
    main()
