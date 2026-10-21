"""三态 pipeline：问题 → 检索 → 模型 → 校验 → 编译 → 执行 → 结果。

这里刻意不做「让模型再写一段解释」的第二次调用：
解释由指标字典里的口径字段和结果本身拼出来，确定性、零额外 token。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from semantic.compiler import CompiledSQL, compile_query
from semantic.errors import SemanticError
from semantic.executor import Executor, QueryResult
from semantic.registry import Registry

from .prompt import build_messages, build_repair_messages
from .retriever import Retriever

AnswerKind = Literal["query", "clarify", "refuse", "error"]


class LLM(Protocol):
    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, **kwargs: Any) -> Any: ...


@dataclass
class AgentAnswer:
    kind: AnswerKind
    question: str
    dsl: dict[str, Any] | None = None
    sql: str | None = None
    engine: str | None = None
    result: QueryResult | None = None
    assumptions: list[str] = field(default_factory=list)
    explanation: str = ""
    clarify: dict[str, Any] | None = None
    refuse: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    attempts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "question": self.question,
            "attempts": self.attempts,
            "tokens": {"prompt": self.prompt_tokens, "completion": self.completion_tokens},
            "elapsed_ms": round(self.elapsed_ms, 1),
            "assumptions": self.assumptions,
            "explanation": self.explanation,
        }
        if self.dsl is not None:
            payload["dsl"] = self.dsl
        if self.sql is not None:
            payload["sql"] = self.sql
            payload["engine"] = self.engine
        if self.result is not None:
            payload["result"] = self.result.to_dict()
        if self.clarify is not None:
            payload["clarify"] = self.clarify
        if self.refuse is not None:
            payload["refuse"] = self.refuse
        if self.error is not None:
            payload["error"] = self.error
        return payload


class Agent:
    def __init__(
        self,
        registry: Registry,
        llm: LLM,
        *,
        retriever: Retriever | None = None,
        executor: Executor | None = None,
        max_repairs: int = 2,
        engine: str | None = None,
    ) -> None:
        self.registry = registry
        self.llm = llm
        self.retriever = retriever or Retriever(registry)
        self.executor = executor
        self.max_repairs = max_repairs
        self.engine = engine

    # ---------------- 主流程 ----------------
    def answer(self, question: str, *, execute: bool = True) -> AgentAnswer:
        started = time.perf_counter()
        cards = self.retriever.search(question)
        ambiguity = self.retriever.ambiguity(question)
        hints = self.retriever.dimension_hints(question)

        bounds = None
        for dataset in self.registry.datasets.values():
            if dataset.time_bounds:
                lo, hi = dataset.time_bounds
                bounds = (str(lo), str(hi))
                break

        messages = build_messages(
            question,
            cards,
            ambiguity=ambiguity,
            dimension_hints=hints,
            time_bounds=bounds,
        )

        prompt_tokens = completion_tokens = 0
        attempts = 0
        last_error: SemanticError | None = None

        while attempts <= self.max_repairs:
            attempts += 1
            try:
                response = self.llm.chat(messages, json_mode=True)
            except Exception as exc:  # 网络/鉴权问题：不再重试，直接报错
                return self._error(question, "E_LLM_CALL_FAILED", str(exc), attempts, started)

            prompt_tokens += getattr(response, "prompt_tokens", 0)
            completion_tokens += getattr(response, "completion_tokens", 0)

            try:
                data = response.as_json()
            except Exception as exc:
                last_error = SemanticError("E_INVALID_DSL", f"模型没有返回合法 JSON: {exc}")
                if attempts > self.max_repairs:
                    return self._refuse(question, "模型多次未返回合法 JSON", attempts, started)
                messages = build_repair_messages(
                    messages, str(getattr(response, "text", "")), "E_INVALID_DSL", last_error.message
                )
                continue

            kind = data.get("type")
            if kind == "clarify":
                return AgentAnswer(
                    kind="clarify",
                    question=question,
                    clarify={
                        "question": data.get("question", ""),
                        "options": data.get("options", []),
                        "reason": data.get("reason", ""),
                    },
                    attempts=attempts,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
            if kind == "refuse":
                return AgentAnswer(
                    kind="refuse",
                    question=question,
                    refuse={"reason": data.get("reason", ""), "closest": data.get("closest", [])},
                    attempts=attempts,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
            if kind != "query":
                last_error = SemanticError("E_INVALID_DSL", f"未知输出形态 {kind!r}")
                if attempts > self.max_repairs:
                    return self._refuse(question, last_error.message, attempts, started)
                messages = build_repair_messages(
                    messages, str(getattr(response, "text", "")), last_error.code, last_error.message
                )
                continue

            # 校验 + 编译
            dsl = data.get("dsl") or {}
            try:
                compiled = compile_query(dsl, self.registry, engine=self.engine)
            except SemanticError as exc:
                last_error = exc
                if exc.action == "clarify":
                    return AgentAnswer(
                        kind="clarify",
                        question=question,
                        dsl=dsl,
                        clarify={
                            "question": exc.message,
                            "options": exc.details.get("available")
                            or exc.details.get("candidates")
                            or [],
                            "reason": exc.code,
                        },
                        attempts=attempts,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                    )
                if exc.action != "repair" or attempts > self.max_repairs:
                    return AgentAnswer(
                        kind="refuse",
                        question=question,
                        dsl=dsl,
                        refuse={"reason": f"{exc.code}: {exc.message}", "closest": []},
                        error=exc.to_dict(),
                        attempts=attempts,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                    )
                messages = build_repair_messages(
                    messages, str(getattr(response, "text", "")), exc.code, exc.message, exc.details
                )
                continue

            return self._finish(
                question, dsl, compiled, data, execute, attempts, prompt_tokens, completion_tokens, started
            )

        return self._refuse(
            question,
            last_error.message if last_error else "超过最大修复次数",
            attempts,
            started,
            prompt_tokens,
            completion_tokens,
        )

    # ---------------- 结果组装 ----------------
    def _finish(
        self,
        question: str,
        dsl: dict[str, Any],
        compiled: CompiledSQL,
        data: dict[str, Any],
        execute: bool,
        attempts: int,
        prompt_tokens: int,
        completion_tokens: int,
        started: float,
    ) -> AgentAnswer:
        result = None
        error = None
        if execute and self.executor is not None:
            try:
                result = self.executor.run(compiled.sql)
            except Exception as exc:
                error = {"code": "E_EXECUTE_FAILED", "message": str(exc)}

        answer = AgentAnswer(
            kind="query",
            question=question,
            dsl=dsl,
            sql=compiled.sql,
            engine=compiled.engine,
            result=result,
            assumptions=list(compiled.assumptions) + list(data.get("assumptions") or []),
            attempts=attempts,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            error=error,
        )
        answer.explanation = self._explain(answer, compiled)
        return answer

    def _explain(self, answer: AgentAnswer, compiled: CompiledSQL) -> str:
        """解释由模板拼装，不让模型自由发挥 —— 口径脚注必须是字典里的原话。"""
        metrics = [self.registry.metrics[name] for name in compiled.metric_versions]
        parts = [f"指标：{'、'.join(f'{m.label}（{m.name}）' for m in metrics)}"]
        if metrics:
            parts.append("口径：" + "；".join(m.definition for m in metrics))
        if answer.result is not None:
            parts.append(
                f"结果：{answer.result.row_count} 行，引擎 {answer.result.engine}，"
                f"耗时 {answer.result.elapsed_ms:.0f}ms"
            )
        if answer.assumptions:
            parts.append("假设：" + "；".join(answer.assumptions))
        return "\n".join(parts)

    def _refuse(
        self,
        question: str,
        reason: str,
        attempts: int,
        started: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> AgentAnswer:
        return AgentAnswer(
            kind="refuse",
            question=question,
            refuse={"reason": reason, "closest": []},
            attempts=attempts,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    def _error(self, question: str, code: str, message: str, attempts: int, started: float) -> AgentAnswer:
        return AgentAnswer(
            kind="error",
            question=question,
            error={"code": code, "message": message},
            attempts=attempts,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
