"""DuckDB 版的数仓替身。

读 DWD 的 Parquet，按 `sql/03_dws/*.sql` 同样的口径重建 `dws_user_summary`、
`dws_item_summary` 与两张 tier 维表，供回归测试和本地演示使用。

这不是生产路径 —— 生产路径是 Hive（明细）+ PostgreSQL（服务层）。
它的价值在于：一条 DWD 扫描在 Hive 上要几十秒，在 DuckDB 上只要几秒，
所以「每改一次字典就回归」这件事才做得起。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb

DEFAULT_PARQUET = "D:/data/taobao_data/dwd_user_behavior/*/*.parquet"

WAREHOUSE_SQL = """
CREATE SCHEMA IF NOT EXISTS taobao_dwd;
CREATE SCHEMA IF NOT EXISTS taobao_dws;

CREATE OR REPLACE VIEW taobao_dwd.dwd_user_behavior AS
SELECT
    user_id,
    item_id,
    category_id,
    behavior_type,
    ts,
    strftime(dt, '%Y-%m-%d') AS dt,
    hour(to_timestamp(ts))    AS hour
FROM read_parquet('{parquet}', hive_partitioning = true);

CREATE OR REPLACE TABLE taobao_dws.dws_user_summary AS
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

CREATE OR REPLACE TABLE taobao_dws.dws_item_summary AS
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

CREATE OR REPLACE TABLE taobao_dws.dim_user_tier AS
SELECT user_id,
       CASE WHEN pv_cnt >= 50 THEN 'high'
            WHEN pv_cnt >= 10 THEN 'mid'
            ELSE 'low' END AS user_tier
FROM taobao_dws.dws_user_summary;

CREATE OR REPLACE TABLE taobao_dws.dim_item_tier AS
SELECT item_id,
       CASE WHEN buy_cnt >= 10 THEN 'high'
            WHEN buy_cnt >= 1  THEN 'mid'
            ELSE 'none' END AS item_tier
FROM taobao_dws.dws_item_summary;
"""


def connect(
    parquet: str = DEFAULT_PARQUET,
    memory_limit: str = "4GB",
    threads: int = 4,
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(config={"memory_limit": memory_limit, "threads": threads})
    # DuckDB 默认把溢写文件放在进程工作目录下的 .tmp/，一跑就是几个 GB。
    # 指到系统临时目录，避免污染仓库（这个目录曾被误生成 1.9GB 并出现在 git status 里）。
    spill_dir = Path(tempfile.gettempdir()) / "taobao_dw_duckdb"
    spill_dir.mkdir(parents=True, exist_ok=True)
    con.execute(f"SET temp_directory = '{spill_dir.as_posix()}'")
    con.execute("SET enable_progress_bar = false")
    con.execute(WAREHOUSE_SQL.format(parquet=parquet))
    return con
