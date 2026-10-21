"""L1 评测：只用黄金 DSL，不调用模型，不需要 API Key。

它回答两个问题：
    1. 60 条问题集本身是否自洽（A/B 有可编译的 DSL，C/D 有明确的期望动作）
    2. 期望 DSL 是否都能编译、执行、并且结果稳定（与基线指纹一致）

    python eval/run_l1.py --update    # 首次生成基线（生成后要人工看一遍）
    python eval/run_l1.py             # 回归：与基线比对
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from semantic import Registry, SemanticError, compile_query  # noqa: E402
from tools.duckdb_warehouse import connect as duckdb_connect  # noqa: E402

BASELINE = HERE / "l1_baseline.json"


def digest(rows: list[tuple]) -> str:
    """对结果内容做指纹，忽略行顺序。

    没有 ORDER BY 的查询，DuckDB 多线程扫描的返回顺序本来就不保证稳定；
    行顺序由编译快照（golden/*.sql）负责锁定，这里只关心"数对不对"。
    """
    normalized = sorted(tuple("" if v is None else str(v) for v in row) for row in rows)
    payload = json.dumps(normalized, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def run(update: bool, only: str | None = None) -> int:
    registry = Registry.load()
    questions = yaml.safe_load((HERE / "questions.yml").read_text(encoding="utf-8"))
    if only:
        questions = [q for q in questions if q["id"] == only]
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}

    con = duckdb_connect()
    failures: list[str] = []
    records: dict[str, dict] = {}
    started = time.perf_counter()

    counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    compiled_rows = 0

    print(f"{'id':5s} {'cls':3s} {'rows':>6s} {'ms':>7s}  status")
    print("-" * 46)
    for q in questions:
        counts[q["cls"]] += 1
        qid = q["id"]

        if q["cls"] in ("A", "B"):
            t0 = time.perf_counter()
            try:
                compiled = compile_query(q["dsl"], registry, engine="duckdb")
                rows = con.execute(compiled.sql).fetchall()
            except SemanticError as exc:
                failures.append(f"{qid} 编译失败 {exc.code}: {exc.message}")
                print(f"{qid:5s} {q['cls']:3s} {'-':>6s} {'-':>7s}  FAIL 编译 {exc.code}")
                continue
            elapsed = (time.perf_counter() - t0) * 1000
            rec = {"rows": len(rows), "digest": digest(rows)}
            compiled_rows += 1

            status = "OK"
            if not rows:
                status = "FAIL 空结果"
                failures.append(f"{qid} 结果为空：{q['q']}")
            elif baseline and qid in baseline and baseline[qid] != rec:
                status = "FAIL 与基线不一致"
                failures.append(f"{qid} 结果漂移：{q['q']}（基线 {baseline[qid]}，现在 {rec}）")
            print(f"{qid:5s} {q['cls']:3s} {len(rows):6d} {elapsed:7.0f}  {status}")
            records[qid] = rec
        else:
            # C/D 类：问题集自洽性检查（有没有写清楚期望动作）
            ok = "expect" in q and "dsl" not in q and q["expect"] in ("clarify", "refuse")
            status = "OK" if ok else "FAIL 缺少 expect"
            if not ok:
                failures.append(f"{qid} 缺少 expect 或误写了 dsl")
            print(f"{qid:5s} {q['cls']:3s} {'-':>6s} {'-':>7s}  {status}")
            records[qid] = {"expect": q.get("expect")}

    total = time.perf_counter() - started
    print()
    print(f"题量 A/B/C/D = {counts['A']}/{counts['B']}/{counts['C']}/{counts['D']}（共 {len(questions)}）")
    print(f"可编译并执行：{compiled_rows}/{counts['A'] + counts['B']}   总耗时 {total:.1f}s")

    if update:
        BASELINE.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入基线 {BASELINE.relative_to(ROOT)}（提交前请人工检查 diff）")

    if failures:
        print(f"\n失败 {len(failures)} 条：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("\nL1 通过：黄金 DSL 全部可编译、可执行、结果稳定。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="L1 评测（无需 API Key）")
    parser.add_argument("--update", action="store_true", help="更新结果基线")
    parser.add_argument("--only", default=None, help="只跑某一条，如 A01")
    args = parser.parse_args()
    return run(args.update, args.only)


if __name__ == "__main__":
    sys.exit(main())
