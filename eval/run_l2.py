"""L2 评测：真模型跑 60 条，产出六项指标与失败分析（需要 API Key）。

    python eval/run_l2.py                 # 全量 60 条
    python eval/run_l2.py --limit 10      # 冒烟
    python eval/run_l2.py --engine pg     # 走 PG 快路径

产出：
    eval/l2_results.json   逐题明细
    eval/l2_report.md      报告（模型与版本 → 六指标 → 失败案例 → 结论）
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from agent.config import ConfigError, load_llm_config  # noqa: E402
from agent.llm import DeepSeekClient  # noqa: E402
from agent.pipeline import Agent  # noqa: E402
from agent.retriever import Retriever  # noqa: E402
from semantic import Registry, compile_query  # noqa: E402
from semantic.executor import DuckDBExecutor, PostgresExecutor  # noqa: E402


def golden_reply(question: dict) -> dict:
    """stub 模式下的标准答案：用它验证评测器本身，而不是验证模型。"""
    if question["cls"] in ("A", "B"):
        return {"type": "query", "dsl": question["dsl"]}
    if question["cls"] == "C":
        return {
            "type": "clarify",
            "question": "需要先确认口径或时间范围",
            "options": ["次数口径", "用户口径"],
            "reason": "E_AMBIGUOUS_METRIC",
        }
    return {"type": "refuse", "reason": "数据里没有这个字段", "closest": []}


def _fingerprint(rows) -> str:
    normalized = sorted(tuple("" if v is None else str(v) for v in row) for row in rows)
    return json.dumps(normalized, ensure_ascii=False)


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


def run(limit: int | None, engine: str, stub: bool = False) -> int:
    config = None
    if not stub:
        try:
            config = load_llm_config()
        except ConfigError as exc:
            print(f"[配置缺失] {exc}", file=sys.stderr)
            return 2
        assert config is not None

    registry = Registry.load()
    questions = yaml.safe_load((HERE / "questions.yml").read_text(encoding="utf-8"))
    if limit:
        questions = questions[:limit]

    if engine == "pg":
        import os

        executor = PostgresExecutor(os.environ.get("DW_PG_DSN", "postgresql://dw:dwpass@localhost:55432/warehouse"))
        con = None
    else:
        from tools.duckdb_warehouse import connect as duckdb_connect

        con = duckdb_connect()
        executor = DuckDBExecutor(con)

    retriever = Retriever(registry)

    def make_agent(question: dict) -> Agent:
        """stub 模式下每题用只回答一次的假模型，验证评测器本身算得对。"""
        if stub:
            from agent.fake import ScriptedLLM

            llm = ScriptedLLM([golden_reply(question)])
        else:
            llm = DeepSeekClient(config)
        return Agent(registry, llm, retriever=retriever, executor=executor, engine=engine)

    details: list[dict] = []
    latencies: list[float] = []
    tokens = {"prompt": 0, "completion": 0}
    counters: Counter[str] = Counter()

    print(f"{'id':5s} {'cls':3s} {'kind':9s} {'ok':3s} {'ms':>7s}  question")
    print("-" * 78)

    for q in questions:
        expected = None
        if q["cls"] in ("A", "B"):
            compiled = compile_query(q["dsl"], registry, engine=engine)
            expected = _fingerprint(con.execute(compiled.sql).fetchall() if con else executor.run(compiled.sql).rows)

        answer = make_agent(q).answer(q["q"], execute=True)
        latencies.append(answer.elapsed_ms)
        tokens["prompt"] += answer.prompt_tokens
        tokens["completion"] += answer.completion_tokens

        if q["cls"] in ("A", "B"):
            ok = (
                answer.kind == "query"
                and answer.result is not None
                and _fingerprint(answer.result.rows) == expected
            )
            counters["answerable"] += 1
            counters["first_legal"] += int(ok and answer.attempts == 1)
            counters["value_match"] += int(ok)
        elif q["cls"] == "C":
            ok = answer.kind == "clarify"
            counters["clarify_total"] += 1
            counters["clarify_hit"] += int(ok)
        else:
            ok = answer.kind == "refuse"
            counters["refuse_total"] += 1
            counters["refuse_hit"] += int(ok)

        counters["ok"] += int(ok)
        details.append(
            {
                "id": q["id"],
                "cls": q["cls"],
                "question": q["q"],
                "expected": q.get("expect", "query"),
                "kind": answer.kind,
                "ok": ok,
                "attempts": answer.attempts,
                "dsl": answer.dsl,
                "sql": answer.sql,
                "explanation": answer.explanation,
                "clarify": answer.clarify,
                "refuse": answer.refuse,
                "error": answer.error,
                "elapsed_ms": round(answer.elapsed_ms, 1),
                "tokens": {"prompt": answer.prompt_tokens, "completion": answer.completion_tokens},
            }
        )
        print(f"{q['id']:5s} {q['cls']:3s} {answer.kind:9s} {'✓' if ok else '✗':3s} {answer.elapsed_ms:7.0f}  {q['q']}")

    n = len(questions)
    answerable = max(counters["answerable"], 1)
    metrics = {
        "dsl_first_legal_rate": round(counters["first_legal"] / answerable, 4),
        "value_match_rate": round(counters["value_match"] / answerable, 4),
        "clarify_rate": round(counters["clarify_hit"] / max(counters["clarify_total"], 1), 4),
        "refuse_rate": round(counters["refuse_hit"] / max(counters["refuse_total"], 1), 4),
        "p95_latency_ms": round(_p95(latencies), 1),
        "median_latency_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "tokens": tokens,
        "questions": n,
        "overall_ok": round(counters["ok"] / n, 4),
    }

    model_name = "stub-golden" if stub else config.model  # type: ignore[union-attr]
    suffix = "_stub" if stub else ""
    (HERE / f"l2_results{suffix}.json").write_text(
        json.dumps(
            {"model": model_name, "metrics": metrics, "details": details},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_report(model_name, metrics, details, engine, suffix=suffix)

    print()
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"\n报告：{HERE / f'l2_report{suffix}.md'}")
    return 0 if metrics["overall_ok"] >= 0.85 else 1


def _write_report(model: str, metrics: dict, details: list[dict], engine: str, suffix: str = "") -> None:
    failures = [d for d in details if not d["ok"]]
    lines = [
        "# L2 评测报告（真模型）",
        "",
        f"- 模型：`{model}`",
        f"- 执行引擎：`{engine}`",
        f"- 题量：{metrics['questions']}（A/B 可答、C 需澄清、D 需拒答）",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 六项指标",
        "",
        "| 指标 | 值 | 目标 |",
        "|---|---|---|",
        f"| DSL 首次合法率 | {metrics['dsl_first_legal_rate']:.1%} | ≥ 90% |",
        f"| 数值一致率 | {metrics['value_match_rate']:.1%} | ≥ 95% |",
        f"| 澄清命中率 | {metrics['clarify_rate']:.1%} | ≥ 80% |",
        f"| 拒答正确率 | {metrics['refuse_rate']:.1%} | ≥ 90% |",
        f"| P95 端到端延迟 | {metrics['p95_latency_ms']:.0f} ms | ≤ 8000 ms |",
        f"| 总通过率 | {metrics['overall_ok']:.1%} | — |",
        "",
        f"token 消耗：prompt {metrics['tokens']['prompt']} + completion {metrics['tokens']['completion']}",
        "",
        "## 失败案例",
        "",
    ]
    if not failures:
        lines.append("无。")
    else:
        for item in failures:
            lines.extend(
                [
                    f"### {item['id']}（{item['cls']}）{item['question']}",
                    "",
                    f"- 期望：`{item['expected']}`，实际：`{item['kind']}`",
                    f"- 尝试次数：{item['attempts']}",
                    f"- DSL：`{json.dumps(item['dsl'], ensure_ascii=False)}`",
                ]
            )
            if item.get("clarify"):
                lines.append(f"- 澄清：{json.dumps(item['clarify'], ensure_ascii=False)}")
            if item.get("refuse"):
                lines.append(f"- 拒答：{json.dumps(item['refuse'], ensure_ascii=False)}")
            if item.get("error"):
                lines.append(f"- 错误：{json.dumps(item['error'], ensure_ascii=False)}")
            lines.append("")

    lines.extend(
        [
            "## 结论",
            "",
            "（人工补写：哪些失败是提示词问题、哪些是字典缺指标、哪些是模型能力边界）",
            "",
        ]
    )
    (HERE / f"l2_report{suffix}.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="L2 评测（真模型，需要 API Key）")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--engine", default="duckdb", choices=["duckdb", "pg"])
    parser.add_argument(
        "--stub",
        action="store_true",
        help="用标准答案替身跑一遍，验证评测器本身（不需要 Key）",
    )
    args = parser.parse_args()
    return run(args.limit, args.engine, stub=args.stub)


if __name__ == "__main__":
    sys.exit(main())
