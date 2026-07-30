# 数据倾斜专题 / Data Skew Study

本文档记录在 DWD 明细数据之上进行的数据倾斜受控实验。
This document records a controlled data-skew experiment run on top of the DWD detail layer.

---

## 为什么是"受控实验" / Why a Controlled Experiment

先探查再下结论:本数据集**并不倾斜**。
Profiling first: this dataset is **not skewed**.

| 指标 / Metric | 值 / Value |
|---|---|
| 中位数商品行数 / median rows per item (p50) | 3 |
| p99 | 359 |
| 最热商品 / hottest item (`812879`) | 32,230 |
| max / p50 | ≈ 10,743x |
| 最热 key 占全表 / share of total | **0.033%** |
| 最热 key / 平均分区 / vs average partition | **6.5%** |

原因是数据集本身的采集方式:天池 UserBehavior 为**随机抽样约 100 万用户**,且时间窗口 11-25 ~ 12-03 **不含大促**,随机抽样打散了商品维度的集中度。
The dataset randomly samples ~1M users over a 9-day window with no major sale event, which disperses concentration on the item dimension.

因此选择**构造受控倾斜场景**来验证解法,而不是虚构一个从未发生的问题。
So the skew was constructed deliberately — rather than claiming a problem that never occurred.

---

## 实验设计 / Experiment Design

抽取 30% 记录(固定种子 42)改写为单一爆款商品 `999999999`:
Rewrite 30% of rows (seed 42) to a single hot item:

| 指标 / Metric | 值 / Value |
|---|---|
| 总行数 / total rows | 128,584,176 |
| 热点 key 行数 / hot key rows | 29,669,692 |
| 热点 key 占比 / share | **23.07%** |
| 热点 key / 平均分区 / vs average partition | **46x** |

方法论沿用本项目引擎选型基准测试的做法:
Methodology mirrors the engine-benchmark approach used elsewhere in this project:

- 倾斜数据集**落盘固化**,各方案读同一份数据 / persisted so all strategies read identical data
- **关闭 AQE**,观察未经自动优化的真实表现 / AQE disabled to see raw behavior
- 用 `noop` 数据源触发计算,不产生磁盘 IO / `noop` sink isolates compute time
- 用 `exceptAll` **交叉验证**输出一致性 / `exceptAll` cross-checks correctness

---

## 实测结果 / Results

环境 / Environment: PySpark 4.0 · `local[4]` · driver 6g · `shuffle.partitions=200` · AQE off

| 方案 / Strategy | 耗时 / Runtime | 相对基线 / vs Baseline | 精确性 / Accuracy |
|---|---|---|---|
| `count` + `sum` 可加聚合 / additive | 12.3s | — (对照组 / control) | 精确 / exact |
| `countDistinct` 基线 / baseline | 76.9s | 1.00x | 精确 / exact |
| 两阶段去重 / two-phase dedup | 73.6s | 1.04x | 精确,`exceptAll` 零差异 / exact, 0 diff |
| **HLL 近似 / approximation** | **44.1s** | **1.74x** | 平均误差 0.60% / 0.60% mean error |

关键 Stage 指标(可加聚合,200 tasks)/ Key stage metrics (additive agg, 200 tasks):

| Metric | Median | Max | 倍数 / Ratio |
|---|---|---|---|
| Shuffle Read Records | 99,716 | 102,619 | **1.03x** |

Shuffle Read 总量 19,954,806 条 —— 输入 1.286 亿行被压缩 **6.4 倍**。
Total shuffle read: 19.95M records — input compressed **6.4x** before the shuffle.

---

## 三个结论 / Three Findings

### 1. 判断倾斜看绝对量,不看比值 / Judge skew by absolute volume, not ratio

真实数据 max/p50 高达 10,743 倍,听起来极端,但最热 key 仅占全表 0.033%、是平均分区的 6.5%,单个 task 处理毫无压力。
A 10,743x ratio sounds extreme, yet the hottest key is only 0.033% of the table — a single task handles it effortlessly.

> 判断依据是**单个 key 的绝对数据量是否超出单 task 的承载能力**,而非分布比值。

### 2. map 端预聚合会消解可加聚合的倾斜 / Map-side combine dissolves skew for additive aggregations

即便构造了占比 23%、达平均分区 46 倍的热点 key,`group by count` 仍毫无倾斜(Shuffle Read Max/Median 仅 **1.03x**)——Spark 在 shuffle 前完成本地合并,1.286 亿行压缩至 1995 万条,热点 key 到下游只剩每个 map task 一条。
Even with a 23% hot key, `group by count` showed no skew at all: Spark pre-aggregates before the shuffle, so the hot key arrives downstream as one row per map task.

换成不可加的 `countDistinct`,倾斜立刻出现,耗时 **6.3 倍**。
Switching to the non-combinable `countDistinct` made skew appear immediately — 6.3x slower.

> **倾斜的本质不是数据分布不均,而是不均的数据无法在 shuffle 前被压缩。**
> Skew isn't fundamentally about uneven distribution — it's about uneven data that *cannot be compressed before the shuffle*.

### 3. 优化本身有成本,必须实测 / Optimizations have costs — measure them

| 方案 / Strategy | shuffle 次数 / shuffles | shuffle key |
|---|---|---|
| `countDistinct` 基线 / baseline | 1 | `item_id`(倾斜 / skewed) |
| 两阶段去重 / two-phase dedup | **2** | `(item_id, user_id)` → `item_id` |

两阶段去重确实消除了倾斜、结果精确一致,但多付出一整次 shuffle,净收益仅 **4%**。
Two-phase dedup removed the skew and matched results exactly, but added a full extra shuffle — net gain only 4%.

HLL 之所以胜出,是因为草图可合并让 map 端预聚合重新生效,**倾斜与 shuffle 开销同时消失**,而非拆东墙补西墙。
HLL won because mergeable sketches restore map-side combine, eliminating both the skew and the shuffle overhead.

---

## 补充:HLL 的误差特征 / Note on HLL Error

平均误差 0.60%,但**最大误差达 66.7%** —— 出现在长尾小基数商品上(p50 仅 3 行,真实 UV=3 估成 5 即 66.7%),绝对误差其实只有 ±2。
Mean error 0.60%, but max error reaches 66.7% on long-tail items with tiny cardinality — where absolute error is merely ±2.

> HLL 的精度保证针对**大基数**。适合热门商品 UV、全站 UV 这类统计;若需对长尾商品精确计数,应走两阶段去重。
> HLL's accuracy guarantee targets large cardinalities; use two-phase dedup when exact long-tail counts matter.

---

## 为什么不用加盐 / Why Not Salting

`count(distinct)` **不可加** —— 加盐后各桶的局部去重数不能相加(同一 user 会落在多个桶里被重复计数)。
`count(distinct)` is not additive: partial distinct counts across salt buckets cannot be summed, since the same user may appear in several buckets.

加盐适用于 `count` / `sum` / `max` 这类**可加聚合**的倾斜场景;对去重类聚合,正确做法是换 shuffle key(两阶段去重)或换成可合并的草图(HLL)。
Salting fits additive aggregations; for distinct-type aggregations, either change the shuffle key or switch to a mergeable sketch.

---

## 运行方式 / How to Run

```bash
# 输入为 DWD 明细数据 (ORC 或 Parquet)
# Input: DWD detail data (ORC or Parquet)
python spark/skew_experiment.py \
  --input /path/to/dwd_user_behavior \
  --format parquet
```

脚本会依次完成:分布探查 → 构造倾斜 → 四组对照 → 正确性校验 → 误差评估。
The script runs: profiling → skew construction → four-way comparison → correctness check → error assessment.
