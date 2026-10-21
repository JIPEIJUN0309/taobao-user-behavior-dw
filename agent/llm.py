"""DeepSeek 客户端（只用标准库，不引入 requests）。

只做一件事：发消息、拿回文本或 JSON。业务语义（三态、修复循环）在 pipeline 里，
这里保持"哑"—— 好测、好换模型。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import LLMConfig


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    raw: dict[str, Any]

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_json(self) -> dict[str, Any]:
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise LLMError(f"模型没有返回合法 JSON: {exc}", body=self.text[:500]) from exc


class DeepSeekClient:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise LLMError(f"HTTP {exc.code}", status=exc.code, body=body) from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"网络错误: {exc.reason}") from exc
        elapsed = (time.perf_counter() - started) * 1000

        usage = data.get("usage", {}) or {}
        return LLMResponse(
            text=data["choices"][0]["message"]["content"],
            model=data.get("model", self.config.model),
            latency_ms=elapsed,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            raw=data,
        )
