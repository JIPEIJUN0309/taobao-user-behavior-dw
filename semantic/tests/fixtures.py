"""为每个指标构造一条「最小可用」DSL，用于生成快照和覆盖率检查。

目的不是覆盖所有组合，而是保证 20 个指标每一个都至少被编译过一次，
字典里写错一个表达式，快照就会变。
"""

from __future__ import annotations

from semantic.registry import MetricDef, Registry

ALL_TIME = {"start": "2017-11-25", "end": "2017-12-03"}


def base_dsl_for(metric: MetricDef, registry: Registry) -> dict:
    dataset = registry.dataset(metric.dataset)
    dims: list[str] = []

    if metric.kind == "cohort":
        dims = []
    elif metric.kind == "derived" and metric.op == "dod":
        dims = ["dt"]
    elif metric.kind == "derived" and metric.op == "share":
        dims = [metric.split_dimension] if metric.split_dimension else []
        if "dt" in metric.dimensions:
            dims = ["dt"] + dims
    else:
        dims = ["dt"] if "dt" in metric.dimensions else metric.dimensions[:1]

    dsl: dict = {"metrics": [metric.name], "dimensions": dims}
    if dataset.partition is not None:
        dsl["time_range"] = dict(ALL_TIME)
    return dsl


def golden_name(metric_name: str) -> str:
    return f"metric_{metric_name}.sql"
