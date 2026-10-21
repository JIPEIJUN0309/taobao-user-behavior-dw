"""字典加载与一致性校验（C1–C5）。

语义层的全部知识都在三个 YAML 里：数据集（粒度与 join 路径）、维度（怎么切）、
指标（口径）。这里负责把它们读成对象，并在启动时证明它们自洽。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import SemanticError

SEMANTIC_DIR = Path(__file__).resolve().parent

MetricKind = Literal["additive", "ratio", "cohort", "derived"]
MetricOp = Literal["dod", "share"]


class ColumnDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str | None = None


class JoinDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    table: str
    alias: str
    # 注意：YAML 1.1 会把裸 on 解析成布尔 True，所以这里叫 join_on
    join_on: dict[str, str]
    type: Literal["left", "inner"] = "left"


class PartitionDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    format: str = "%Y-%m-%d"


class DatasetDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str
    table: str
    grain: list[str] = Field(default_factory=list)
    description: str | None = None
    columns: list[ColumnDef]
    partition: PartitionDef | None = None
    time_bounds: tuple[date, date] | None = None
    dimensions: list[str] = Field(default_factory=list)
    joins: list[JoinDef] = Field(default_factory=list)
    cost: Literal["low", "medium", "high"] = "medium"
    engine: Literal["hive", "pg", "duckdb"] = "hive"

    @property
    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}


class DimensionDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str
    column: str
    kind: Literal["date", "int", "enum"]
    format: str | None = None
    min: int | None = None
    max: int | None = None
    values: list[str] = Field(default_factory=list)
    value_labels: dict[str, str] = Field(default_factory=dict)
    source_table: str | None = None
    description: str | None = None


class MetricDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str
    synonyms: list[str] = Field(default_factory=list)
    definition: str
    kind: MetricKind
    dataset: str
    columns: list[str] = Field(default_factory=list)
    # additive
    expr: str | None = None
    # ratio
    numerator: str | None = None
    denominator: str | None = None
    # derived
    op: MetricOp | None = None
    base: str | None = None
    split_dimension: str | None = None
    # common
    multiplier: float = 1.0
    decimals: int = 4
    unit: str = "count"
    dimensions: list[str] = Field(default_factory=list)
    version: int = 1
    owner: str | None = None
    sample_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_shape(self) -> "MetricDef":
        if self.kind == "additive" and not self.expr:
            raise ValueError(f"指标 {self.name}: additive 必须提供 expr")
        if self.kind == "ratio":
            if not self.numerator or not self.denominator:
                raise ValueError(f"指标 {self.name}: ratio 必须提供 numerator 与 denominator")
        if self.kind == "derived":
            if not self.op or not self.base:
                raise ValueError(f"指标 {self.name}: derived 必须提供 op 与 base")
            if self.op == "share" and not self.split_dimension:
                raise ValueError(f"指标 {self.name}: share 必须提供 split_dimension")
        if self.kind == "cohort" and not self.columns:
            raise ValueError(f"指标 {self.name}: cohort 必须声明 columns")
        return self


class _DatasetFile(BaseModel):
    version: int = 1
    datasets: list[DatasetDef]


class _DimensionFile(BaseModel):
    version: int = 1
    dimensions: list[DimensionDef]


class _MetricFile(BaseModel):
    version: int = 1
    owner: str | None = None
    metrics: list[MetricDef]


class Registry:
    """全部指标的只读视图。加载时做一致性校验，有错就直接拒绝启动。"""

    def __init__(
        self,
        datasets: dict[str, DatasetDef],
        dimensions: dict[str, DimensionDef],
        metrics: dict[str, MetricDef],
        synonym_index: dict[str, list[str]],
        version: int = 1,
    ) -> None:
        self.datasets = datasets
        self.dimensions = dimensions
        self.metrics = metrics
        self.synonym_index = synonym_index
        self.version = version

    # ---------------- 加载 ----------------
    @classmethod
    def load(cls, directory: Path | str | None = None) -> "Registry":
        directory = Path(directory) if directory else SEMANTIC_DIR
        try:
            ds_raw = yaml.safe_load((directory / "datasets.yml").read_text(encoding="utf-8"))
            dim_raw = yaml.safe_load((directory / "dimensions.yml").read_text(encoding="utf-8"))
            mt_raw = yaml.safe_load((directory / "metrics.yml").read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SemanticError("E_DICT_INVALID", f"字典文件缺失: {exc.filename}") from exc
        except yaml.YAMLError as exc:
            raise SemanticError("E_DICT_INVALID", f"YAML 解析失败: {exc}") from exc

        try:
            ds_file = _DatasetFile.model_validate(ds_raw)
            dim_file = _DimensionFile.model_validate(dim_raw)
            mt_file = _MetricFile.model_validate(mt_raw)
        except Exception as exc:  # pydantic.ValidationError
            raise SemanticError("E_DICT_INVALID", f"字典结构不合法: {exc}") from exc

        datasets = {d.name: d for d in ds_file.datasets}
        dimensions = {d.name: d for d in dim_file.dimensions}
        metrics = {m.name: m for m in mt_file.metrics}

        if len(datasets) != len(ds_file.datasets):
            raise SemanticError("E_DICT_INVALID", "数据集名称重复")
        if len(metrics) != len(mt_file.metrics):
            raise SemanticError("E_DICT_INVALID", "指标名称重复")

        synonym_index: dict[str, list[str]] = {}
        for metric in metrics.values():
            for word in [metric.label, metric.name, *metric.synonyms]:
                key = word.strip().lower()
                if not key:
                    continue
                bucket = synonym_index.setdefault(key, [])
                if metric.name not in bucket:
                    bucket.append(metric.name)

        registry = cls(datasets, dimensions, metrics, synonym_index, version=ds_file.version)
        registry._check_consistency()
        return registry

    # ---------------- C1–C5 ----------------
    def _check_consistency(self) -> None:
        problems: list[str] = []

        # 维度必须存在；数据集声明的维度与 join 路径必须对得上
        for ds in self.datasets.values():
            for dim_name in ds.dimensions:
                if dim_name not in self.dimensions:
                    problems.append(f"[C0] 数据集 {ds.name} 引用了未定义的维度 {dim_name}")
            for join in ds.joins:
                if join.dimension not in self.dimensions:
                    problems.append(f"[C0] 数据集 {ds.name} 的 join 引用了未定义维度 {join.dimension}")
                if join.dimension not in ds.dimensions:
                    problems.append(f"[C0] 数据集 {ds.name} 的 join 维度 {join.dimension} 未在 dimensions 声明")
                for dim_col, base_col in join.join_on.items():
                    if base_col not in ds.column_names:
                        problems.append(
                            f"[C0] 数据集 {ds.name} 的 join 条件引用了不存在的列 {base_col}"
                        )

        for metric in self.metrics.values():
            ds = self.datasets.get(metric.dataset)
            # C1
            if ds is None:
                problems.append(f"[C1] 指标 {metric.name} 绑定了不存在的数据集 {metric.dataset}")
                continue
            # C2
            for dim_name in metric.dimensions:
                if dim_name not in ds.dimensions:
                    problems.append(
                        f"[C2] 指标 {metric.name} 声明的维度 {dim_name} 不在数据集 {ds.name} 的可用维度里"
                    )
            # C3
            if metric.kind == "ratio":
                for label, expr in (("numerator", metric.numerator), ("denominator", metric.denominator)):
                    if expr and "from" in expr.lower():
                        problems.append(f"[C3] 指标 {metric.name} 的 {label} 不允许自带 FROM")
            if metric.kind == "derived":
                base = self.metrics.get(metric.base or "")
                if base is None:
                    problems.append(f"[C3] 指标 {metric.name} 的派生基指标 {metric.base} 不存在")
                elif base.dataset != metric.dataset:
                    problems.append(
                        f"[C3] 指标 {metric.name} 与基指标 {base.name} 不在同一数据集"
                    )
                if metric.op == "share" and metric.split_dimension not in metric.dimensions:
                    problems.append(
                        f"[C2] 指标 {metric.name} 的 split_dimension {metric.split_dimension} 不在其可用维度里"
                    )
            # C4
            for col in metric.columns:
                if col not in ds.column_names:
                    problems.append(f"[C4] 指标 {metric.name} 引用了数据集 {ds.name} 不存在的列 {col}")

        if problems:
            raise SemanticError(
                "E_DICT_INVALID",
                "语义层字典不自洽，已拒绝启动",
                problems=problems,
            )

    # ---------------- 查询辅助 ----------------
    def metric(self, name: str) -> MetricDef:
        try:
            return self.metrics[name]
        except KeyError as exc:
            raise SemanticError(
                "E_UNKNOWN_METRIC",
                f"未知指标 {name}",
                candidates=sorted(self.metrics)[:5],
            ) from exc

    def dimension(self, name: str) -> DimensionDef:
        try:
            return self.dimensions[name]
        except KeyError as exc:
            raise SemanticError(
                "E_UNKNOWN_DIMENSION",
                f"未知维度 {name}",
                candidates=sorted(self.dimensions),
            ) from exc

    def dataset(self, name: str) -> DatasetDef:
        try:
            return self.datasets[name]
        except KeyError as exc:
            raise SemanticError("E_DICT_INVALID", f"未知数据集 {name}") from exc

    def resolve_metric_word(self, word: str) -> list[str]:
        """把一个中文词解析成候选指标；返回多个说明有歧义。"""
        return list(self.synonym_index.get(word.strip().lower(), []))

    def ambiguous_synonyms(self) -> dict[str, list[str]]:
        return {word: names for word, names in self.synonym_index.items() if len(names) > 1}

    def describe_metric(self, name: str) -> dict[str, Any]:
        m = self.metric(name)
        return {
            "name": m.name,
            "label": m.label,
            "definition": m.definition,
            "unit": m.unit,
            "dimensions": m.dimensions,
            "version": m.version,
            "synonyms": m.synonyms,
            "sample_questions": m.sample_questions,
        }
