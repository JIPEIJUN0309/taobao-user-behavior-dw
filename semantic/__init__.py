"""指标语义层：字典 + 校验 + 编译器。

对外只用三个入口：
    Registry.load()     加载并校验字典（不自洽会拒绝启动）
    validate(raw, reg)  DSL 校验，返回 ResolvedQuery
    compile_query(raw, reg)  DSL → SQL
"""

from .compiler import CompiledSQL, compile_query, compile_resolved
from .errors import SemanticError
from .registry import Registry
from .validator import QueryDSL, ResolvedQuery, validate

__all__ = [
    "CompiledSQL",
    "QueryDSL",
    "Registry",
    "ResolvedQuery",
    "SemanticError",
    "compile_query",
    "compile_resolved",
    "validate",
]
