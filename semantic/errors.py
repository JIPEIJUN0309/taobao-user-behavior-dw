"""语义层错误码与异常。

错误码是 Agent 修复循环的接口：每个错误都带一个 action，决定下一步是
重试（repair）、反问（clarify）还是拒答（refuse）。
"""

from __future__ import annotations

from typing import Any

# 可以让模型重写 DSL 修好的
REPAIRABLE = {
    "E_INVALID_DSL",
    "E_UNKNOWN_METRIC",
    "E_UNKNOWN_DIMENSION",
    "E_UNKNOWN_FILTER_FIELD",
    "E_DIM_NOT_AVAILABLE",
    "E_INVALID_ENUM",
    "E_TIME_OUT_OF_RANGE",
    "E_LIMIT_EXCEEDED",
    "E_EMPTY_METRICS",
}

# 只能反问用户才能解决的
CLARIFY = {
    "E_AMBIGUOUS_METRIC",
    "E_DIM_REQUIRED",
}

# 编译期不可修复，直接拒答
REFUSE = {
    "E_MULTI_DATASET",
    "E_MIXED_KINDS",
    "E_TIME_NOT_SUPPORTED",
    "E_RATIO_CROSS_DATASET",
    "E_DICT_INVALID",
    "E_METRIC_NOT_IMPLEMENTED",
}


class SemanticError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details

    @property
    def action(self) -> str:
        if self.code in REPAIRABLE:
            return "repair"
        if self.code in CLARIFY:
            return "clarify"
        return "refuse"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "action": self.action,
            "details": self.details,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"SemanticError({self.code!r}, {self.message!r}, {self.details!r})"
