"""用真实数据核对语义层编译出的 SQL。

思路：本机的 Hive 跑一条 DWD 扫描要几十秒，做「每次改字典都回归」不现实。
这里用 DuckDB 直接读 DWD 的 Parquet，按数仓同样的口径重建 dws_user_summary、
dws_item_summary 与两张 tier 维表，然后把「编译器产出的 SQL」原样跑一遍，
和手写黄金 SQL 的结果逐行比对。

    python tools/verify_metrics.py
    python tools/verify_metrics.py --parquet "D:/data/taobao_data/dwd_user_behavior/*/*.parquet"

注意：这是口径验证，不是性能验证 —— 引擎不同，耗时没有可比性。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import decimal
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from semantic import Registry, SemanticError, compile_query  # noqa: E402
from semantic.executor import PostgresExecutor  # noqa: E402

DEFAULT_PARQUET = "D:/data/taobao_data/dwd_user_behavior/*/*.parquet"

# 数仓里 DWS / DIM 的等价定义（与 sql/03_dws/*.sql、sql/04_ads 同口径）。
# 这里刻意不 import tools/duckdb_warehouse.py：验证脚本要独立重实现一遍，
# 两边同时跑偏才不至于一起掩盖错误。
WAREHOUSE_DDL = """
CREATE SCHEMA IF NOT EXISTS taobao_dwd;
CREATE SCHEMA IF NOT EXISTS taobao_dws;

CREATE VIEW taobao_dwd.dwd_user_behavior AS
SELECT
    user_id,
    item_id,
    category_id,
    behavior_type,
    ts,
    strftime(dt, '%Y-%m-%d') AS dt,
    hour(to_timestamp(ts))    AS hour
FROM read_parquet('{parquet}', hive_partitioning = true);

CREATE TABLE taobao_dws.dws_user_summary AS
SELECT
    user_id,
    SUM(CASE WHEN behavior_type = 'pv'   THEN 1 ELSE 0 END) AS pv_cnt,
    SUM(CASE WHEN behavior_type = 'cart' THEN 1 ELSE 0 END) AS cart_cnt,
    SUM(CASE WHEN behavior_type = 'fav'  THEN 1 ELSE 0 END) AS fav_cnt,
    SUM(CASE WHEN behavior_type = 'buy'  THEN 1 ELSE 0 END) AS buy_cnt,
    COUNT(DISTINCT dt)      AS active_days,
    COUNT(DISTINCT item_id) AS item_cnt
FROM taobao_dwd.dwd_user_behavior
GROUP BY user_id;

CREATE TABLE taobao_dws.dws_item_summary AS
SELECT
    item_id,
    MAX(category_id) AS category_id,
    SUM(CASE WHEN behavior_type = 'pv'   THEN 1 ELSE 0 END) AS pv_cnt,
    SUM(CASE WHEN behavior_type = 'cart' THEN 1 ELSE 0 END) AS cart_cnt,
    SUM(CASE WHEN behavior_type = 'fav'  THEN 1 ELSE 0 END) AS fav_cnt,
    SUM(CASE WHEN behavior_type = 'buy'  THEN 1 ELSE 0 END) AS buy_cnt,
    COUNT(DISTINCT user_id) AS user_cnt
FROM taobao_dwd.dwd_user_behavior
GROUP BY item_id;

CREATE TABLE taobao_dws.dim_user_tier AS
SELECT user_id,
       CASE WHEN pv_cnt >= 50 THEN 'high'
            WHEN pv_cnt >= 10 THEN 'mid'
            ELSE 'low' END AS user_tier
FROM taobao_dws.dws_user_summary;

CREATE TABLE taobao_dws.dim_item_tier AS
SELECT item_id,
       CASE WHEN buy_cnt >= 10 THEN 'high'
            WHEN buy_cnt >= 1  THEN 'mid'
            ELSE 'none' END AS item_tier
FROM taobao_dws.dws_item_summary;
"""

ALL_TIME = {"start": "2017-11-25", "end": "2017-12-03"}

# 每个案例：DSL + 独立手写的黄金 SQL。expected 是 README 里已经核实过的数字，
# 用来防止「编译器和黄金 SQL 一起错」。
CASES: list[dict[str, Any]] = [
    {
        "name": "pv_cnt 全期",
        "dsl": {"metrics": ["pv_cnt"], "time_range": ALL_TIME},
        "gold": "SELECT SUM(CASE WHEN behavior_type = 'pv' THEN 1 ELSE 0 END) AS pv_cnt "
                "FROM taobao_dwd.dwd_user_behavior WHERE dt BETWEEN '2017-11-25' AND '2017-12-03'",
        "expected": {"pv_cnt": 88596886},
    },
    {
        "name": "active_uv 全期",
        "dsl": {"metrics": ["active_uv"], "time_range": ALL_TIME},
        "gold": "SELECT COUNT(DISTINCT user_id) AS active_uv FROM taobao_dwd.dwd_user_behavior "
                "WHERE dt BETWEEN '2017-11-25' AND '2017-12-03'",
        "expected": {"active_uv": 987984},
    },
    {
        "name": "buy_pv_rate 全期（用户口径）",
        "dsl": {"metrics": ["buy_pv_rate"], "time_range": ALL_TIME},
        "gold": """
            SELECT ROUND(100.0 * COUNT(DISTINCT CASE WHEN behavior_type = 'buy' THEN user_id END)
                       / NULLIF(COUNT(DISTINCT CASE WHEN behavior_type = 'pv' THEN user_id END), 0), 4) AS buy_pv_rate
            FROM taobao_dwd.dwd_user_behavior WHERE dt BETWEEN '2017-11-25' AND '2017-12-03'
        """,
        "expected": {"buy_pv_rate": 68.12},
    },
    {
        "name": "buy_pv_rate 按天（9 行趋势）",
        "dsl": {"metrics": ["buy_pv_rate"], "dimensions": ["dt"], "time_range": ALL_TIME,
                "order_by": [{"field": "dt", "dir": "asc"}]},
        "gold": """
            SELECT dt,
                   ROUND(100.0 * COUNT(DISTINCT CASE WHEN behavior_type = 'buy' THEN user_id END)
                       / NULLIF(COUNT(DISTINCT CASE WHEN behavior_type = 'pv' THEN user_id END), 0), 4) AS buy_pv_rate
            FROM taobao_dwd.dwd_user_behavior WHERE dt BETWEEN '2017-11-25' AND '2017-12-03'
            GROUP BY dt ORDER BY dt
        """,
        "rows": 9,
    },
    {
        "name": "pv_to_cart_rate 全期",
        "dsl": {"metrics": ["pv_to_cart_rate"], "time_range": ALL_TIME},
        "gold": """
            SELECT ROUND(100.0 * COUNT(DISTINCT CASE WHEN behavior_type = 'cart' THEN user_id END)
                       / NULLIF(COUNT(DISTINCT CASE WHEN behavior_type = 'pv' THEN user_id END), 0), 4) AS pv_to_cart_rate
            FROM taobao_dwd.dwd_user_behavior WHERE dt BETWEEN '2017-11-25' AND '2017-12-03'
        """,
        "expected": {"pv_to_cart_rate": 74.93},
    },
    {
        "name": "behavior_share 按行为类型（占比）",
        "dsl": {"metrics": ["behavior_share"], "dimensions": ["behavior_type"],
                "time_range": ALL_TIME, "order_by": [{"field": "behavior_type", "dir": "asc"}]},
        "gold": """
            SELECT behavior_type,
                   ROUND(100.0 * COUNT(1) / NULLIF(SUM(COUNT(1)) OVER (), 0), 4) AS behavior_share
            FROM taobao_dwd.dwd_user_behavior WHERE dt BETWEEN '2017-11-25' AND '2017-12-03'
            GROUP BY behavior_type ORDER BY behavior_type
        """,
    },
    {
        "name": "retention_d1 次日留存",
        "dsl": {"metrics": ["retention_d1"], "time_range": {"start": "2017-11-25", "end": "2017-12-02"}},
        "gold": """
            WITH a AS (SELECT DISTINCT dt, user_id FROM taobao_dwd.dwd_user_behavior
                       WHERE dt BETWEEN '2017-11-25' AND '2017-12-02'),
                 b AS (SELECT DISTINCT dt, user_id FROM taobao_dwd.dwd_user_behavior
                       WHERE dt BETWEEN '2017-11-25' AND '2017-12-03')
            SELECT a.dt,
                   ROUND(100.0 * COUNT(DISTINCT b.user_id) / NULLIF(COUNT(DISTINCT a.user_id), 0), 4) AS retention_d1
            FROM a LEFT JOIN b
              ON b.user_id = a.user_id
             AND b.dt = strftime(a.dt::date + 1, '%Y-%m-%d')
            GROUP BY a.dt ORDER BY a.dt
        """,
        "rows": 8,
    },
    {
        "name": "repurchase_uv 按用户分层（全期汇总表 + join 维表）",
        "dsl": {"metrics": ["repurchase_uv"], "dimensions": ["user_tier"],
                "order_by": [{"field": "user_tier", "dir": "asc"}]},
        "gold": """
            SELECT d.user_tier,
                   SUM(CASE WHEN s.buy_cnt >= 2 THEN 1 ELSE 0 END) AS repurchase_uv
            FROM taobao_dws.dws_user_summary AS s
            LEFT JOIN taobao_dws.dim_user_tier AS d ON d.user_id = s.user_id
            GROUP BY d.user_tier ORDER BY d.user_tier
        """,
        "rows": 3,
    },
    {
        "name": "category_conv_rate 类目转化（次数口径）",
        "dsl": {"metrics": ["category_conv_rate"], "dimensions": ["category_id"],
                "order_by": [{"field": "category_conv_rate", "dir": "desc"}], "limit": 5},
        "gold": """
            SELECT category_id,
                   ROUND(100.0 * SUM(buy_cnt) / NULLIF(SUM(pv_cnt), 0), 4) AS category_conv_rate
            FROM taobao_dws.dws_item_summary
            GROUP BY category_id
            ORDER BY 2 DESC LIMIT 5
        """,
        "rows": 5,
    },
    {
        "name": "day_over_day 环比（含回溯前一日）",
        "dsl": {"metrics": ["day_over_day"], "dimensions": ["dt"],
                "time_range": {"start": "2017-11-27", "end": "2017-12-03"},
                "order_by": [{"field": "dt", "dir": "asc"}]},
        "gold": """
            WITH daily AS (
                SELECT dt,
                       ROUND(100.0 * COUNT(DISTINCT CASE WHEN behavior_type = 'buy' THEN user_id END)
                           / NULLIF(COUNT(DISTINCT CASE WHEN behavior_type = 'pv' THEN user_id END), 0), 4) AS r
                FROM taobao_dwd.dwd_user_behavior WHERE dt BETWEEN '2017-11-26' AND '2017-12-03'
                GROUP BY dt
            ), win AS (
                SELECT dt,
                       ROUND(100.0 * (r - LAG(r) OVER (ORDER BY dt)) / NULLIF(LAG(r) OVER (ORDER BY dt), 0), 4) AS day_over_day
                FROM daily
            )
            SELECT dt, day_over_day FROM win
            WHERE dt BETWEEN '2017-11-27' AND '2017-12-03' ORDER BY dt
        """,
        "rows": 7,
        # 回溯生效的硬证据：首日 11-27 必须有值，不能是 NULL
        "expect_not_null": {"day_over_day": 7},
    },
]


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)  # NaN
    except Exception:  # pragma: no cover - 非标量
        return False


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, decimal.Decimal)):
        return float(value)
    if hasattr(value, "item") and not isinstance(value, str):  # numpy 标量
        try:
            return float(value.item())
        except Exception:
            return None
    return None


def _close(left: Any, right: Any, tol: float = 1e-4) -> bool:
    if _is_null(left) or _is_null(right):
        return _is_null(left) and _is_null(right)
    a, b = _as_number(left), _as_number(right)
    if a is not None and b is not None:
        return abs(a - b) <= max(tol, abs(b) * 1e-6)
    return str(left) == str(right)


def run(parquet: str, pg_dsn: str | None = None) -> int:
    registry = Registry.load()
    con = duckdb.connect(config={"memory_limit": "4GB", "threads": 4})
    con.execute("SET enable_progress_bar = false")
    print(f"[1/3] 建视图与汇总表：{parquet}")
    con.execute(WAREHOUSE_DDL.format(parquet=parquet))

    failures = 0
    print(f"[2/3] 逐条比对 {len(CASES)} 个案例\n")
    header = f"{'case':46s} {'rows':>5s}  {'status':6s}  detail"
    print(header)
    print("-" * len(header))
    for case in CASES:
        try:
            compiled = compile_query(case["dsl"], registry, engine="duckdb")
        except SemanticError as exc:
            print(f"{case['name']:46s} {'-':>5s}  {'FAIL':6s}  编译失败 {exc.code}: {exc.message}")
            failures += 1
            continue

        produced = con.execute(compiled.sql).fetch_df()
        gold = con.execute(case["gold"]).fetch_df()

        detail = ""
        ok = produced.shape == gold.shape
        if ok:
            for col in gold.columns:
                if col not in produced.columns:
                    ok, detail = False, f"缺少列 {col}"
                    break
                for got, want in zip(produced[col], gold[col]):
                    if not _close(got, want):
                        ok = False
                        detail = f"{col}: 编译={_fmt(got)} 黄金={_fmt(want)}"
                        break
                if not ok:
                    break
        else:
            detail = f"行数 编译={produced.shape[0]} 黄金={gold.shape[0]}"

        expected = case.get("expected")
        if ok and expected:
            for col, want in expected.items():
                got = produced[col].iloc[0]
                if not _close(got, want, tol=0.01):
                    ok, detail = False, f"{col}: 实际={_fmt(got)} 期望={_fmt(want)}"
        if ok and "rows" in case and len(produced) != case["rows"]:
            ok, detail = False, f"期望 {case['rows']} 行，实际 {len(produced)} 行"
        for col, non_null in case.get("expect_not_null", {}).items():
            if not ok:
                break
            actual = int(produced[col].notna().sum())
            if actual != non_null:
                ok, detail = False, f"{col} 非空 {actual} 行，期望 {non_null} 行（回溯未生效）"

        # 双路由一致性：绑定 PG 的指标，快路径必须和 DuckDB 算出同样的数
        route = ""
        if ok and pg_dsn and registry.dataset(compiled.dataset).engine == "pg":
            pg_compiled = compile_query(case["dsl"], registry, engine="pg")
            pg_rows = PostgresExecutor(pg_dsn).run(pg_compiled.sql).rows
            pg_ok = len(pg_rows) == len(produced) and all(
                all(_close(a, b) for a, b in zip(pg_row, duck_row))
                for pg_row, duck_row in zip(pg_rows, produced.itertuples(index=False))
            )
            if not pg_ok:
                ok, detail = False, "PG 快路径与 DuckDB 结果不一致"
                route = "  pg=不一致"
            else:
                route = "  pg=一致"

        failures += 0 if ok else 1
        print(f"{case['name']:46s} {len(produced):5d}  {'PASS' if ok else 'FAIL':6s}  {detail}{route}")

    print()
    print(f"[3/3] {len(CASES) - failures}/{len(CASES)} 通过")
    if failures:
        print("失败案例的 SQL 可在 --print-sql 模式下打印", file=sys.stderr)
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实数据核对语义层指标口径")
    parser.add_argument("--parquet", default=DEFAULT_PARQUET)
    parser.add_argument("--pg-dsn", default=None, help="传入后额外核对 PG 快路径（需先跑 sync_to_pg.py）")
    parser.add_argument("--print-sql", action="store_true", help="打印每条编译出的 SQL")
    args = parser.parse_args()
    if args.print_sql:
        registry = Registry.load()
        for case in CASES:
            compiled = compile_query(case["dsl"], registry, engine="duckdb")
            print(f"-- {case['name']}\n{compiled.sql}\n")
    return run(args.parquet, args.pg_dsn)


if __name__ == "__main__":
    sys.exit(main())
