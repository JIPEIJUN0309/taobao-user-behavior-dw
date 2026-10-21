"""配置加载：环境变量优先，其次仓库根目录的 .env。

Key 只放在这两个地方，绝不进代码、绝不进 git 历史：
    D:\\repos\\taobao-user-behavior-dw\\.env        （本地文件，已在 .gitignore）
    用户级环境变量 DEEPSEEK_API_KEY                 （setx 一次，全局生效）
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class ConfigError(RuntimeError):
    pass


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，# 开头为注释。已存在的环境变量优先。"""
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        loaded[key] = value
        os.environ.setdefault(key, value)
    return loaded


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: float = 90.0
    temperature: float = 0.0

    @property
    def masked_key(self) -> str:
        key = self.api_key
        if len(key) <= 12:
            return key[:2] + "***"
        return f"{key[:6]}...{key[-4:]}"


def load_llm_config(required: bool = True) -> LLMConfig | None:
    load_env_file()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        if not required:
            return None
        raise ConfigError(
            "没有找到 DEEPSEEK_API_KEY。两种放法任选其一：\n"
            f"  1) 写进 {ENV_FILE}（该文件已被 .gitignore 忽略）\n"
            "  2) 设为用户环境变量：setx DEEPSEEK_API_KEY \"sk-...\" 然后重开终端"
        )
    return LLMConfig(
        api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL),
        timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "60")),
        temperature=float(os.environ.get("DEEPSEEK_TEMPERATURE", "0")),
    )
