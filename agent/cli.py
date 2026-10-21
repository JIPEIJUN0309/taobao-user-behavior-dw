"""命令行：python -m agent check

只做一件事 —— 确认 Key 放对了、模型通、还能不能返回 JSON。
先把这一步跑通，再谈 Agent。
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import ENV_FILE, ConfigError, load_llm_config
from .llm import DeepSeekClient, LLMError


def _check(args: argparse.Namespace) -> int:
    try:
        config = load_llm_config(required=not args.offline)
    except ConfigError as exc:
        print(f"[配置缺失] {exc}", file=sys.stderr)
        return 2

    if config is None:
        print(f"未找到 DEEPSEEK_API_KEY；应放在 {ENV_FILE} 或环境变量里")
        return 2

    print(f"env file : {'存在' if ENV_FILE.exists() else '不存在'}  ({ENV_FILE})")
    print(f"api key  : {config.masked_key}")
    print(f"base_url : {config.base_url}")
    print(f"model    : {config.model}")
    if args.offline:
        print("offline 模式：只检查配置，不发起请求")
        return 0

    client = DeepSeekClient(config)
    try:
        response = client.chat(
            [
                {"role": "system", "content": "你是一个只输出 JSON 的助手。"},
                {"role": "user", "content": '返回 {"ok": true, "echo": "pong"} 这个 JSON，不要别的内容。'},
            ],
            json_mode=True,
            max_tokens=64,
        )
    except LLMError as exc:
        print(f"[调用失败] {exc}", file=sys.stderr)
        if exc.body:
            print(exc.body[:800], file=sys.stderr)
        return 1

    print(f"latency  : {response.latency_ms:.0f} ms")
    print(f"tokens   : prompt={response.prompt_tokens} completion={response.completion_tokens}")
    print(f"content  : {response.text.strip()}")
    try:
        print(f"parsed   : {json.dumps(response.as_json(), ensure_ascii=False)}")
    except LLMError as exc:
        print(f"[JSON 解析失败] {exc}", file=sys.stderr)
        return 1
    print("\nKey 可用，可以开始接 Agent 了。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="问数 Agent 工具")
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="检查 API Key 与连通性")
    p_check.add_argument("--offline", action="store_true", help="只检查配置，不发请求")
    p_check.set_defaults(func=_check)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
