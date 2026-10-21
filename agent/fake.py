"""可脚本化的假 LLM：不联网也能把 pipeline 的状态机跑完。

它只做两件事：按顺序吐出预设回复、记录收到的 messages（断言 prompt 里确实回了错误码）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass
class FakeResponse:
    text: str
    model: str = "fake-model"
    latency_ms: float = 1.0
    prompt_tokens: int = 120
    completion_tokens: int = 40
    raw: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return json.loads(self.text)


class ScriptedLLM:
    def __init__(self, replies: Sequence[str | dict[str, Any]]) -> None:
        self.replies: list[str | dict[str, Any]] = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, **_: Any) -> FakeResponse:
        self.calls.append([dict(m) for m in messages])
        if not self.replies:
            raise AssertionError("脚本里没有更多回复了，说明 pipeline 多调了一次模型")
        reply = self.replies.pop(0)
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False, default=str)
        return FakeResponse(text=text)

    @property
    def last_user_message(self) -> str:
        return self.calls[-1][-1]["content"]


class FailingLLM:
    """模拟网络/鉴权失败。"""

    def __init__(self, message: str = "connection reset") -> None:
        self.message = message

    def chat(self, messages: list[dict[str, str]], **_: Any) -> FakeResponse:
        raise RuntimeError(self.message)
