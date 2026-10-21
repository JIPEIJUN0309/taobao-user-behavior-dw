"""把服务层表同步到 PostgreSQL（双路由里的「快路径」）。

为什么要同步而不是让 PG 直接读 Hive：
    指标类问题都落在 DWS/ADS 这种小结果集上，放进 PG 后是毫秒级；
    明细下钻仍然回 Hive，几十秒但没人天天点。服务层与即席查询分离，
    这就是真实数仓的做法，不是 demo 的取巧。

写入方式：先灌 staging 表，再在一个事务里换名 —— 查询永远看不到半成品。

    python tools/sync_to_pg.py
    python tools/sync_to_pg.py --dsn postgresql://dw:dwpass@localhost:55432/warehouse
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.duckdb_warehouse import DEFAULT_PARQUET, connect as duckdb_connect  # noqa: E402

DEFAULT_DSN = os.environ.get("DW_PG_DSN", "postgresql://dw:dwpass@localhost:55432/warehouse")

# 表名 → (列, 主键列, 来源查询)
TABLES: dict[str, dict] = {
    "dws_user_summary": {
        "columns": ["user_id", "pv_cnt", "cart_cnt", "fav_cnt", "buy_cnt", "active_days", "item_cnt"],
        "key": "user_id",
        "types": ["bigint", "bigint", "bigint", "bigint", "bigint", "bigint", "bigint"],
    },
    "dws_item_summary": {
        "columns": ["item_id", "category_id", "pv_cnt", "cart_cnt", "fav_cnt", "buy_cnt", "user_cnt"],
        "key": "item_id",
        "types": ["bigint", "bigint", "bigint", "bigint", "bigint", "bigint", "bigint"],
    },
    "dim_user_tier": {
        "columns": ["user_id", "user_tier"],
        "key": "user_id",
        "types": ["bigint", "text"],
    },
    "dim_item_tier": {
        "columns": ["item_id", "item_tier"],
        "key": "item_id",
        "types": ["bigint", "text"],
    },
}

BASE_DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.dw_sync_audit (
    table_name text,
    row_count  bigint,
    source     text,
    synced_at  timestamptz
);
"""

BATCH_ROWS = 100_000
BUFFER_CHARS = 8_000_000


def _table_ddl(schema: str, table: str, spec: dict) -> str:
    cols = ",\n    ".join(f"{c} {t}" for c, t in zip(spec["columns"], spec["types"]))
    return f"CREATE TABLE IF NOT EXISTS {schema}.{table} (\n    {cols}\n);"


def _copy_rows(src_cursor, dst_cursor, schema: str, table: str, spec: dict, source: str) -> int:
    columns = spec["columns"]
    staging = f"{table}__staging"
    col_list = ", ".join(columns)

    dst_cursor.execute(f"DROP TABLE IF EXISTS {schema}.{staging}")
    dst_cursor.execute(f"CREATE TABLE {schema}.{staging} (LIKE {schema}.{table} INCLUDING DEFAULTS)")
    dst_cursor.connection.commit()

    src_cursor.execute(f"SELECT {col_list} FROM taobao_dws.{table}")

    copied = 0
    buffer = io.StringIO()
    while True:
        batch = src_cursor.fetchmany(BATCH_ROWS)
        if not batch:
            break
        for row in batch:
            buffer.write(",".join("" if v is None else str(v) for v in row))
            buffer.write("\n")
        copied += len(batch)
        if buffer.tell() >= BUFFER_CHARS:
            buffer.seek(0)
            dst_cursor.copy_expert(
                f"COPY {schema}.{staging} ({col_list}) FROM STDIN WITH (FORMAT csv, NULL '')", buffer
            )
            buffer = io.StringIO()

    if buffer.tell():
        buffer.seek(0)
        dst_cursor.copy_expert(
            f"COPY {schema}.{staging} ({col_list}) FROM STDIN WITH (FORMAT csv, NULL '')", buffer
        )

    # 换名放在一个事务里：要么整张新表可见，要么旧表还在
    dst_cursor.execute(f"DROP TABLE IF EXISTS {schema}.{table}")
    dst_cursor.execute(f"ALTER TABLE {schema}.{staging} RENAME TO {table}")
    dst_cursor.execute(
        f"CREATE INDEX IF NOT EXISTS {table}_key_idx ON {schema}.{table} ({spec['key']})"
    )
    dst_cursor.execute(
        f"INSERT INTO {schema}.dw_sync_audit (table_name, row_count, source, synced_at) "
        f"VALUES (%s, %s, %s, %s)",
        (table, copied, source, datetime.now(timezone.utc)),
    )
    return copied


def run(dsn: str, parquet: str, schema: str, only: list[str] | None) -> int:
    tables = {k: v for k, v in TABLES.items() if not only or k in only}
    if not tables:
        print(f"没有匹配的表：{only}", file=sys.stderr)
        return 2

    print(f"[1/3] 读源数据：{parquet}")
    src = duckdb_connect(parquet)
    src_cursor = src.cursor()

    print(f"[2/3] 连接 PostgreSQL：{dsn.split('@')[-1]}")
    dst = psycopg2.connect(dsn)
    dst.autocommit = False
    with dst.cursor() as dst_cursor:
        dst_cursor.execute(BASE_DDL.format(schema=schema))
        for name, spec in tables.items():
            dst_cursor.execute(_table_ddl(schema, name, spec))
        dst.commit()

    print("[3/3] 同步")
    total = 0
    started = time.perf_counter()
    for name, spec in tables.items():
        t0 = time.perf_counter()
        with dst.cursor() as dst_cursor:
            copied = _copy_rows(src_cursor, dst_cursor, schema, name, spec, source=f"duckdb:{parquet}")
            dst.commit()
        total += copied
        print(f"  {name:20s} {copied:>10,d} 行  {time.perf_counter() - t0:5.1f}s")

    dst.close()
    print(f"完成：{len(tables)} 张表 / {total:,} 行 / {time.perf_counter() - started:.1f}s")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="同步服务层表到 PostgreSQL")
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--parquet", default=DEFAULT_PARQUET)
    parser.add_argument("--schema", default="taobao_dws")
    parser.add_argument("--tables", default=None, help="逗号分隔，默认全部")
    args = parser.parse_args()
    only = [t.strip() for t in args.tables.split(",")] if args.tables else None
    return run(args.dsn, args.parquet, args.schema, only)


if __name__ == "__main__":
    sys.exit(main())
