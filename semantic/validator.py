"""DSL 校验：把外部来的 JSON 变成一个已经验证过、可以编译的查询。

校验分两类：
  1. 形状（schema）：字段类型、取值范围、必填项
  2. 语义：指标/维度是否白名单、维度对该指标是否可用、枚举取值是否合法、
     时间是否越界、跨数据集是否被禁止
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .errors import SemanticError
from .registry import DatasetDef, MetricDef, Registry

MAX_METRICS = 5
MAX_DIMENSIONS = 2
MAX_LIMIT = 1000
DEFAULT_WINDOW_DAYS = 7


class Filter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    op: Literal["=", "in", ">=", "<=", "between"]
    value: Any


class TimeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> "TimeRange":
        if self.start > self.end:
            raise ValueError("time_range.start 必须不晚于 end")
        return self


class OrderBy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    dir: Literal["asc", "desc"] = "asc"


class QueryDSL(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[str] = Field(min_length=1, max_length=MAX_METRICS)
    dimensions: list[str] = Field(default_factory=list, max_length=MAX_DIMENSIONS)
    filters: list[Filter] = Field(default_factory=list)
    time_range: TimeRange | None = None
    order_by: list[OrderBy] = Field(default_factory=list)
    limit: int = Field(default=MAX_LIMIT, ge=1, le=MAX_LIMIT)


@dataclass
class ResolvedQuery:
    """校验通过后的查询上下文，编译器只消费它，不再做判断。"""

    dsl: QueryDSL
    metrics: list[MetricDef]
    dataset: DatasetDef
    effective_range: tuple[date, date] | None
    assumptions: list[str] = field(default_factory=list)

    @property
    def metric_names(self) -> list[str]:
        return [m.name for m in self.metrics]


def validate(raw: dict[str, Any], registry: Registry) -> ResolvedQuery:
    if not isinstance(raw, dict):
        raise SemanticError("E_INVALID_DSL", "DSL 必须是 JSON 对象")

    # limit 先单独判，给出更准的错误码
    limit = raw.get("limit", MAX_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= MAX_LIMIT):
        raise SemanticError("E_LIMIT_EXCEEDED", f"limit 必须是 1..{MAX_LIMIT} 的整数", limit=limit)

    names = raw.get("metrics") or []
    if not names:
        raise SemanticError(
            "E_EMPTY_METRICS",
            "至少需要一个指标",
            candidates=[m.name for m in sorted(registry.metrics.values(), key=lambda x: x.name)[:8]],
        )

    try:
        dsl = QueryDSL.model_validate(raw)
    except ValidationError as exc:
        raise SemanticError(
            "E_INVALID_DSL",
            "DSL 结构不合法",
            errors=[{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()],
        ) from exc

    assumptions: list[str] = []

    # 1) 指标存在
    metrics = [registry.metric(name) for name in dsl.metrics]

    # 2) 单数据集原则
    datasets = {m.dataset for m in metrics}
    if len(datasets) > 1:
        raise SemanticError(
            "E_MULTI_DATASET",
            "一次查询只能落在一个数据集上",
            datasets=sorted(datasets),
            metrics={m.name: m.dataset for m in metrics},
        )
    dataset = registry.dataset(datasets.pop())

    # 3) 派生/留存类指标独占，且补齐必需维度
    special = [m for m in metrics if m.kind in ("derived", "cohort")]
    if special and len(metrics) > 1:
        raise SemanticError(
            "E_MIXED_KINDS",
            "派生指标与留存指标暂不支持与其他指标同时查询",
            metric=special[0].name,
        )
    dims = list(dsl.dimensions)
    if special:
        metric = special[0]
        if metric.kind == "cohort":
            if not dims:
                dims = ["dt"]
                assumptions.append("留存指标默认按 dt 展示")
            elif dims != ["dt"]:
                raise SemanticError(
                    "E_DIM_NOT_AVAILABLE",
                    "留存指标只支持按 dt 维度查看",
                    metric=metric.name,
                    available=["dt"],
                )
        elif metric.op == "dod":
            if "dt" not in dims:
                raise SemanticError(
                    "E_DIM_REQUIRED",
                    "环比需要按 dt 维度查看",
                    metric=metric.name,
                    required="dt",
                )
        elif metric.op == "share":
            if metric.split_dimension not in dims:
                raise SemanticError(
                    "E_DIM_REQUIRED",
                    f"占比指标必须按 {metric.split_dimension} 拆分",
                    metric=metric.name,
                    required=metric.split_dimension,
                )

    # 4) 维度白名单 + 对该指标可用
    for dim_name in dims:
        if dim_name not in registry.dimensions:
            raise SemanticError(
                "E_UNKNOWN_DIMENSION",
                f"未知维度 {dim_name}",
                candidates=sorted(registry.dimensions),
            )
        for metric in metrics:
            if dim_name not in metric.dimensions:
                raise SemanticError(
                    "E_DIM_NOT_AVAILABLE",
                    f"指标 {metric.name} 不支持按 {dim_name} 拆维度",
                    metric=metric.name,
                    dimension=dim_name,
                    available=metric.dimensions,
                )

    # 5) 过滤条件：字段必须是该数据集的维度，枚举值必须在取值范围内
    for flt in dsl.filters:
        if flt.field not in dataset.dimensions:
            raise SemanticError(
                "E_UNKNOWN_FILTER_FIELD",
                f"数据集 {dataset.name} 没有可过滤字段 {flt.field}",
                field=flt.field,
                available=dataset.dimensions,
            )
        dim = registry.dimension(flt.field)
        if dim.values:
            values = flt.value if isinstance(flt.value, list) else [flt.value]
            invalid = [v for v in values if str(v) not in dim.values]
            if invalid:
                raise SemanticError(
                    "E_INVALID_ENUM",
                    f"字段 {flt.field} 不接受取值 {invalid}",
                    field=flt.field,
                    allowed=dim.values,
                )

    # 6) 时间范围
    effective_range: tuple[date, date] | None = None
    if dataset.partition is None:
        if dsl.time_range is not None:
            raise SemanticError(
                "E_TIME_NOT_SUPPORTED",
                f"数据集 {dataset.name} 是全期汇总表，不支持按时间过滤",
                dataset=dataset.name,
            )
    else:
        if dsl.time_range is not None:
            effective_range = (dsl.time_range.start, dsl.time_range.end)
            if dataset.time_bounds:
                lo, hi = dataset.time_bounds
                if effective_range[0] < lo or effective_range[1] > hi:
                    raise SemanticError(
                        "E_TIME_OUT_OF_RANGE",
                        f"数据范围是 {lo} 至 {hi}",
                        start=str(effective_range[0]),
                        end=str(effective_range[1]),
                        bounds=[str(lo), str(hi)],
                    )
        elif dataset.time_bounds:
            lo, hi = dataset.time_bounds
            start = max(lo, hi - timedelta(days=DEFAULT_WINDOW_DAYS - 1))
            effective_range = (start, hi)
            assumptions.append(
                f"未指定时间范围，默认最近 {DEFAULT_WINDOW_DAYS} 天（{start} 至 {hi}）"
            )

    # 7) 排序字段必须是维度或本次查询的指标
    for ob in dsl.order_by:
        if ob.field in dims or ob.field in dsl.metrics:
            continue
        raise SemanticError(
            "E_INVALID_DSL",
            f"排序字段 {ob.field} 既不是维度也不是本次查询的指标",
            field=ob.field,
            dimensions=dims,
            metrics=dsl.metrics,
        )

    normalized = dsl.model_copy(update={"dimensions": dims})
    return ResolvedQuery(
        dsl=normalized,
        metrics=metrics,
        dataset=dataset,
        effective_range=effective_range,
        assumptions=assumptions,
    )
