"""执行层：把编译好的 SQL 跑在某个引擎上。

双路由的分工：
    pg     服务层，毫秒级，覆盖 DWS/ADS 上的指标
    hive   明细层，几十秒，用于真正需要下钻到行为明细的问题
    duckdb 本地替身，回归测试与无 Docker 演示

执行层不认识指标，只认识 SQL —— 口径的判定全部发生在编译期。
"""

from __future__ import annotations

import datetime as _dt
import decimal
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .compiler import CompiledSQL


def _normalize(value: Any) -> Any:
    """把不同驱动的返回类型收敛成 JSON 友好的类型。

    PostgreSQL 的 numeric 会变成 Decimal，日期是 date 对象；DuckDB 直接给 float。
    同一份指标在两个引擎上必须给出同一种类型，否则上层（Agent/看板）要写两套分支。
    """
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    engine: str
    elapsed_ms: float
    sql: str

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": list(self.columns),
            "rows": [list(r) for r in self.rows],
            "engine": self.engine,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "row_count": self.row_count,
        }


class Executor(Protocol):
    engine: str

    def run(self, sql: str) -> QueryResult: ...


class DuckDBExecutor:
    engine = "duckdb"

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def run(self, sql: str) -> QueryResult:
        started = time.perf_counter()
        cursor = self.connection.execute(sql)
        columns = tuple(d[0] for d in cursor.description)
        rows = tuple(tuple(_normalize(v) for v in r) for r in cursor.fetchall())
        return QueryResult(columns, rows, self.engine, (time.perf_counter() - started) * 1000, sql)


class PostgresExecutor:
    engine = "pg"

    def __init__(self, dsn: str) -> None:
        import psycopg2  # 延迟导入：不用 PG 的机器不必装

        self.connection = psycopg2.connect(dsn)

    def run(self, sql: str) -> QueryResult:
        started = time.perf_counter()
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            columns = tuple(d[0] for d in cursor.description)
            rows = tuple(tuple(_normalize(v) for v in r) for r in cursor.fetchall())
        return QueryResult(columns, rows, self.engine, (time.perf_counter() - started) * 1000, sql)

    def close(self) -> None:
        self.connection.close()


def run_compiled(
    compiled: CompiledSQL,
    *,
    duckdb_con: Any | None = None,
    pg_dsn: str | None = None,
) -> QueryResult:
    """按 compiled.engine 选执行器；没有对应引擎时给出明确错误而不是静默降级。"""
    if compiled.engine == "duckdb":
        if duckdb_con is None:
            raise RuntimeError("需要 duckdb 连接：tools.duckdb_warehouse.connect()")
        return DuckDBExecutor(duckdb_con).run(compiled.sql)
    if compiled.engine == "pg":
        if pg_dsn is None:
            raise RuntimeError("需要 PostgreSQL DSN（环境变量 DW_PG_DSN 或参数传入）")
        return PostgresExecutor(pg_dsn).run(compiled.sql)
    if compiled.engine == "hive":
        raise RuntimeError("Hive 执行器需要 pyhive 与运行中的 HiveServer2（D5 暂未接入）")
    raise RuntimeError(f"未知引擎 {compiled.engine}")
