"""编译器：DSL → SQL。

这是整个项目里唯一"生成 SQL"的地方，而且它不做任何推断：
指标绑定哪个数据集、join 走哪条路径、比率怎么算，全部来自字典。
模型永远看不到这里，也就编不出 SQL。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .errors import SemanticError
from .registry import DatasetDef, JoinDef, MetricDef, Registry
from .validator import ResolvedQuery, validate

# 日期加一天的方言写法（只有留存指标用到）
_DATE_ADD = {
    "hive": "date_add(t0.{col}, 1)",
    "pg": "to_char(t0.{col}::date + 1, 'YYYY-MM-DD')",
    "duckdb": "strftime(t0.{col}::date + 1, '%Y-%m-%d')",
}


@dataclass(frozen=True)
class CompiledSQL:
    sql: str
    engine: str
    dataset: str
    cost: str
    assumptions: tuple[str, ...]
    metric_versions: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "engine": self.engine,
            "dataset": self.dataset,
            "cost": self.cost,
            "assumptions": list(self.assumptions),
            "metric_versions": dict(self.metric_versions),
        }


# ---------------------------------------------------------------- 公共入口


def compile_query(
    raw: dict[str, Any],
    registry: Registry,
    engine: str | None = None,
) -> CompiledSQL:
    return compile_resolved(validate(raw, registry), registry, engine=engine)


def compile_resolved(
    res: ResolvedQuery,
    registry: Registry,
    engine: str | None = None,
) -> CompiledSQL:
    metric = res.metrics[0]
    if metric.kind == "cohort":
        sql, extra = _compile_cohort(res, registry, engine)
    elif metric.kind == "derived":
        sql, extra = _compile_derived(res, registry)
    else:
        sql, extra = _compile_simple(res, registry)

    return CompiledSQL(
        sql=sql,
        engine=engine or res.dataset.engine,
        dataset=res.dataset.name,
        cost=res.dataset.cost,
        assumptions=tuple(res.assumptions + extra),
        metric_versions={m.name: m.version for m in res.metrics},
    )


# ---------------------------------------------------------------- 基础查询


def _compile_simple(res: ResolvedQuery, registry: Registry) -> tuple[str, list[str]]:
    ds = res.dataset
    dims = res.dsl.dimensions
    dim_refs = [_dim_ref(registry, ds, d) for d in dims]

    selects = [f"{ref} AS {name}" for ref, name in zip(dim_refs, dims)]
    selects += [f"{_metric_expr(m)} AS {m.name}" for m in res.metrics]

    order_by, extra = _order(registry, ds, res)
    sql = _assemble(
        ds=ds,
        joins=_joins_for(ds, dims, res.dsl.filters),
        selects=selects,
        where=_where(registry, ds, res),
        group_by=dim_refs,
        order_by=order_by or None,
        limit=res.dsl.limit,
    )
    return sql, extra


def _metric_expr(metric: MetricDef) -> str:
    if metric.kind == "additive":
        assert metric.expr is not None
        return metric.expr
    if metric.kind == "ratio":
        assert metric.numerator and metric.denominator
        ratio = f"{metric.numerator} / NULLIF({metric.denominator}, 0)"
        return _scaled(ratio, metric)
    raise SemanticError(
        "E_METRIC_NOT_IMPLEMENTED",
        f"指标 {metric.name} 的类型 {metric.kind} 不能按普通聚合编译",
        metric=metric.name,
    )


def _scaled(expr: str, metric: MetricDef) -> str:
    if metric.multiplier == 1:
        return f"ROUND({expr}, {metric.decimals})"
    return f"ROUND({_num(metric.multiplier)} * {expr}, {metric.decimals})"


# ---------------------------------------------------------------- 派生指标


def _compile_derived(res: ResolvedQuery, registry: Registry) -> tuple[str, list[str]]:
    metric = res.metrics[0]
    base = registry.metric(metric.base or "")
    ds = res.dataset
    dims = res.dsl.dimensions
    dim_refs = [_dim_ref(registry, ds, d) for d in dims]
    joins = _joins_for(ds, dims, res.dsl.filters)
    extra: list[str] = []

    inner_range = res.effective_range
    if metric.op == "dod" and inner_range is not None:
        # 首日环比需要前一天的数据，内层范围向前扩一天
        inner_range = (inner_range[0] - timedelta(days=1), inner_range[1])
        extra.append("已自动回溯前一日数据用于计算首日环比")

    inner_where = _where(registry, ds, res, override_range=inner_range)
    inner_selects = [f"{ref} AS {name}" for ref, name in zip(dim_refs, dims)]
    inner_selects.append(f"{_metric_expr(base)} AS __base")
    inner = _assemble(
        ds=ds,
        joins=joins,
        selects=inner_selects,
        where=inner_where,
        group_by=dim_refs,
        order_by=None,
        limit=None,
    )

    if metric.op == "dod":
        others = [d for d in dims if d != "dt"]
        partition = f"PARTITION BY {', '.join('s.' + d for d in others)} " if others else ""
        window = f"{partition}ORDER BY s.dt ASC"
        prev = f"LAG(s.__base, 1) OVER ({window})"
        expr = _scaled(f"(s.__base - {prev}) / NULLIF({prev}, 0)", metric)
    elif metric.op == "share":
        others = [d for d in dims if d != metric.split_dimension]
        if others:
            window = f"PARTITION BY {', '.join('s.' + d for d in others)}"
        else:
            window = ""
        expr = _scaled(f"s.__base / NULLIF(SUM(s.__base) OVER ({window}), 0)", metric)
    else:  # pragma: no cover - 字典校验已挡住
        raise SemanticError("E_METRIC_NOT_IMPLEMENTED", f"未知派生算子 {metric.op}")

    # 第二层：窗口计算。注意这一层不带 WHERE ——
    # 如果先过滤再算 LAG，首日就永远没有前一日可比，回溯的数据等于白算。
    mid_selects = [f"s.{d} AS {d}" for d in dims]
    mid_selects.append(f"{expr} AS {metric.name}")
    mid = ["SELECT", ",\n".join(f"    {item}" for item in mid_selects)]
    mid.append("FROM (")
    mid.append(_indent(inner, 4))
    mid.append(") AS s")

    if metric.op == "dod":
        # 第三层：把窗口结果裁回用户要的时间范围
        lines = ["SELECT", f"    w.dt AS dt,", f"    w.{metric.name} AS {metric.name}"]
        lines.append("FROM (")
        lines.append(_indent("\n".join(mid), 4))
        lines.append(") AS w")
        if res.effective_range is not None:
            start, end = res.effective_range
            lines.append(f"WHERE w.dt BETWEEN '{start}' AND '{end}'")
        if res.dsl.order_by:
            lines.append(
                "ORDER BY "
                + ", ".join(_order_item(registry, ds, res, ob, prefix="w.") for ob in res.dsl.order_by)
            )
        else:
            lines.append("ORDER BY w.dt ASC")
        lines.append(f"LIMIT {res.dsl.limit}")
        return "\n".join(lines), extra

    if res.dsl.order_by:
        ordered: list[str] = []
        for ob in res.dsl.order_by:
            if ob.field in res.metric_names:
                # 这个别名就在本层 SELECT 里定义，不能带 s. 前缀，否则外层找不到列
                ordered.append(f"{ob.field} {ob.dir.upper()} NULLS LAST")
            else:
                ordered.append(f"s.{ob.field} {ob.dir.upper()}")
        used = {ob.field for ob in res.dsl.order_by}
        ordered += [f"s.{d} ASC" for d in dims if d not in used]
        mid.append("ORDER BY " + ", ".join(ordered))
    elif dims:
        mid.append("ORDER BY " + ", ".join(f"s.{d}" for d in dims))
    mid.append(f"LIMIT {res.dsl.limit}")
    return "\n".join(mid), extra


# ---------------------------------------------------------------- 留存指标


def _compile_cohort(
    res: ResolvedQuery,
    registry: Registry,
    engine: str | None,
) -> tuple[str, list[str]]:
    metric = res.metrics[0]
    ds = res.dataset
    dialect = engine or ds.engine
    if dialect not in _DATE_ADD:  # pragma: no cover
        raise SemanticError("E_METRIC_NOT_IMPLEMENTED", f"不支持的执行引擎 {dialect}")

    entity, date_col = metric.columns[0], metric.columns[1]
    assert res.effective_range is not None
    start, end = res.effective_range
    end_plus = end + timedelta(days=1)

    def base_block(range_start: date, range_end: date) -> str:
        return (
            f"SELECT DISTINCT {date_col}, {entity}\n"
            f"FROM {ds.table}\n"
            f"WHERE {date_col} BETWEEN '{range_start}' AND '{range_end}'"
        )

    add_expr = _DATE_ADD[dialect].format(col=date_col)
    expr = _scaled(
        f"COUNT(DISTINCT t1.{entity}) / NULLIF(COUNT(DISTINCT t0.{entity}), 0)", metric
    )

    lines = [
        "SELECT",
        f"    t0.{date_col} AS {date_col},",
        f"    {expr} AS {metric.name}",
        "FROM (",
        _indent(base_block(start, end), 4),
        ") AS t0",
        "LEFT JOIN (",
        _indent(base_block(start, end_plus), 4),
        ") AS t1",
        f"    ON t1.{entity} = t0.{entity}",
        f"   AND t1.{date_col} = {add_expr}",
        f"GROUP BY t0.{date_col}",
        f"ORDER BY t0.{date_col} ASC",
        f"LIMIT {res.dsl.limit}",
    ]
    return "\n".join(lines), []


# ---------------------------------------------------------------- 组装


def _assemble(
    *,
    ds: DatasetDef,
    joins: list[JoinDef],
    selects: list[str],
    where: list[str],
    group_by: list[str],
    order_by: list[str] | None,
    limit: int | None,
) -> str:
    lines = ["SELECT", ",\n".join(f"    {item}" for item in selects)]
    lines.append(f"FROM {ds.table} AS t")
    for join in joins:
        keyword = "LEFT JOIN" if join.type == "left" else "INNER JOIN"
        condition = " AND ".join(
            f"{join.alias}.{dim_col} = t.{base_col}" for dim_col, base_col in join.join_on.items()
        )
        lines.append(f"{keyword} {join.table} AS {join.alias}")
        lines.append(f"    ON {condition}")
    if where:
        lines.append("WHERE " + "\n  AND ".join(where))
    if group_by:
        lines.append("GROUP BY " + ", ".join(group_by))
    if order_by:
        lines.append("ORDER BY " + ", ".join(order_by))
    if limit is not None:
        lines.append(f"LIMIT {limit}")
    return "\n".join(lines)


def _where(
    registry: Registry,
    ds: DatasetDef,
    res: ResolvedQuery,
    override_range: tuple[date, date] | None = None,
) -> list[str]:
    parts: list[str] = []
    rng = override_range or res.effective_range
    if rng is not None and ds.partition is not None:
        parts.append(f"t.{ds.partition.column} BETWEEN '{rng[0]}' AND '{rng[1]}'")
    for flt in res.dsl.filters:
        parts.append(_filter_sql(registry, ds, flt.field, flt.op, flt.value))
    return parts


def _filter_sql(registry: Registry, ds: DatasetDef, field: str, op: str, value: Any) -> str:
    ref = _dim_ref(registry, ds, field)
    if op == "in":
        return f"{ref} IN ({', '.join(_lit(v) for v in value)})"
    if op == "between":
        return f"{ref} BETWEEN {_lit(value[0])} AND {_lit(value[1])}"
    return f"{ref} {op} {_lit(value)}"


def _order(registry: Registry, ds: DatasetDef, res: ResolvedQuery) -> tuple[list[str], list[str]]:
    """返回 (排序子句, 需要披露的假设)。

    两种情况要特殊处理：
      1. 显式排序但只按指标排 -> 补分组维度做 tiebreaker，否则并列值取 TopN 不可复现
      2. 有 limit 却没排序 -> 默认按第一个指标降序，否则 LIMIT 取到哪几行是随机的
    """
    from .validator import MAX_LIMIT

    dims = res.dsl.dimensions
    if res.dsl.order_by:
        parts = [_order_item(registry, ds, res, ob) for ob in res.dsl.order_by]
        used = {ob.field for ob in res.dsl.order_by}
        parts += [f"{_dim_ref(registry, ds, dim)} ASC" for dim in dims if dim not in used]
        return parts, []

    if res.dsl.limit < MAX_LIMIT and res.metrics:
        primary = res.metrics[0]
        parts = [f"{primary.name} DESC NULLS LAST"]
        parts += [f"{_dim_ref(registry, ds, dim)} ASC" for dim in dims]
        note = f"未指定排序，默认按 {primary.name} 降序取前 {res.dsl.limit} 行"
        return parts, [note]

    # 编译器永远会拼 LIMIT，所以只要有分组维度就必须给确定的顺序：
    # 否则「截断后剩下哪几行」由引擎决定，换引擎或换线程数结果就变。
    if dims:
        return [f"{_dim_ref(registry, ds, dim)} ASC" for dim in dims], []

    return [], []


def _order_item(registry: Registry, ds: DatasetDef, res: ResolvedQuery, ob: Any, prefix: str = "") -> str:
    if ob.field in res.metric_names:
        # NULLS LAST 不是装饰：PostgreSQL 在 DESC 下把 NULL 排在最前，
        # DuckDB/Hive 排在最后。不写死这个，TopN 换个引擎就是另一批行。
        return f"{prefix}{ob.field} {ob.dir.upper()} NULLS LAST"
    if prefix:
        # 子查询外层：维度已在内层按名字别名化
        return f"{prefix}{ob.field} {ob.dir.upper()}"
    return f"{_dim_ref(registry, ds, ob.field)} {ob.dir.upper()}"


def _joins_for(ds: DatasetDef, dims: list[str], filters: list[Any] | None = None) -> list[JoinDef]:
    """join 不仅由 SELECT 的维度决定，过滤条件里用到维表列也必须 join。"""
    needed = set(dims)
    for flt in filters or []:
        needed.add(getattr(flt, "field", flt))
    return [j for j in ds.joins if j.dimension in needed]


def _dim_ref(registry: Registry, ds: DatasetDef, name: str) -> str:
    dim = registry.dimension(name)
    for join in ds.joins:
        if join.dimension == name:
            return f"{join.alias}.{dim.column}"
    return f"t.{dim.column}"


def _lit(value: Any) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, date):
        return f"'{value}'"
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _num(value: float) -> str:
    return f"{value:.1f}" if float(value).is_integer() else repr(value)


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in text.splitlines())
