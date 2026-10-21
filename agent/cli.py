"""命令行：

    python -m agent check                         检查 Key 与连通性
    python -m agent ask "每天的转化率"              问一句（默认走 DuckDB 本地替身）
    python -m agent ask "复购用户有多少" --engine pg
    python -m agent ask "..." --json               输出完整 JSON（含 DSL/SQL/耗时）
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .config import ENV_FILE, ConfigError, load_llm_config
from .llm import DeepSeekClient, LLMError
from .pipeline import Agent, AgentAnswer

DEFAULT_PG_DSN = os.environ.get("DW_PG_DSN", "postgresql://dw:dwpass@localhost:55432/warehouse")


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
    print("\nKey 可用，可以开始问数了。")
    return 0


def _make_executor(engine: str):
    if engine == "pg":
        from semantic.executor import PostgresExecutor

        return PostgresExecutor(DEFAULT_PG_DSN)
    from semantic.executor import DuckDBExecutor
    from tools.duckdb_warehouse import connect

    return DuckDBExecutor(connect())


def _render(answer: AgentAnswer) -> None:
    print(f"[{answer.kind}] {answer.question}")
    if answer.kind == "clarify":
        clarify = answer.clarify or {}
        print(f"  {clarify.get('question', '')}")
        for option in clarify.get("options", []):
            print(f"    - {option}")
        print(f"  原因：{clarify.get('reason', '')}")
    elif answer.kind == "refuse":
        refuse = answer.refuse or {}
        print(f"  {refuse.get('reason', '')}")
        if refuse.get("closest"):
            print(f"  可以试试：{', '.join(refuse['closest'])}")
    elif answer.kind == "error":
        error = answer.error or {}
        print(f"  {error.get('code')}: {error.get('message')}")
    else:
        print(answer.explanation)
        if answer.result is not None:
            result = answer.result
            print("  " + " | ".join(result.columns))
            for row in result.rows[:10]:
                print("  " + " | ".join("" if v is None else str(v) for v in row))
            if result.row_count > 10:
                print(f"  ...（共 {result.row_count} 行）")
    print()
    print(f"  尝试 {answer.attempts} 次 ｜ tokens {answer.prompt_tokens}+{answer.completion_tokens} "
          f"｜ 端到端 {answer.elapsed_ms:.0f}ms")


def _cmd_ask(args: argparse.Namespace) -> int:
    from semantic import Registry

    try:
        config = load_llm_config()
    except ConfigError as exc:
        print(f"[配置缺失] {exc}", file=sys.stderr)
        return 2

    assert config is not None
    registry = Registry.load()
    agent = Agent(
        registry,
        DeepSeekClient(config),
        executor=None if args.no_exec else _make_executor(args.engine),
        engine=args.engine,
    )
    answer = agent.answer(args.question, execute=not args.no_exec)

    if args.json:
        print(json.dumps(answer.to_dict(), ensure_ascii=False, indent=2))
    else:
        _render(answer)
    return 0 if answer.kind in ("query", "clarify") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description="问数 Agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="检查 API Key 与连通性")
    p_check.add_argument("--offline", action="store_true", help="只检查配置，不发请求")
    p_check.set_defaults(func=_check)

    p_ask = sub.add_parser("ask", help="用自然语言问一个指标")
    p_ask.add_argument("question")
    p_ask.add_argument("--engine", default="duckdb", choices=["duckdb", "pg"])
    p_ask.add_argument("--no-exec", action="store_true", help="只编译不执行")
    p_ask.add_argument("--json", action="store_true", help="输出完整 JSON")
    p_ask.set_defaults(func=_cmd_ask)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
