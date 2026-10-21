"""语义层命令行入口。

    python -m semantic validate            校验字典自洽性
    python -m semantic metrics             列出全部指标
    python -m semantic compile --file q.json
    python -m semantic compile --json '{"metrics":["buy_pv_rate"],"dimensions":["dt"]}'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compiler import compile_query
from .errors import SemanticError
from .registry import Registry


def _load(args: argparse.Namespace) -> Registry:
    return Registry.load(Path(args.semantic_dir) if args.semantic_dir else None)


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        reg = _load(args)
    except SemanticError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "datasets": len(reg.datasets),
                "dimensions": len(reg.dimensions),
                "metrics": len(reg.metrics),
                "ambiguous_synonyms": reg.ambiguous_synonyms(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _cmd_metrics(args: argparse.Namespace) -> int:
    reg = _load(args)
    rows = [
        {
            "name": m.name,
            "label": m.label,
            "kind": m.kind,
            "dataset": m.dataset,
            "unit": m.unit,
            "dimensions": m.dimensions,
        }
        for m in sorted(reg.metrics.values(), key=lambda x: (x.dataset, x.name))
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def _cmd_compile(args: argparse.Namespace) -> int:
    if args.file:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    elif args.json:
        raw = json.loads(args.json)
    else:
        raw = json.loads(sys.stdin.read())
    try:
        compiled = compile_query(raw, _load(args), engine=args.engine)
    except SemanticError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(compiled.to_dict(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="semantic", description="淘宝数仓指标语义层")
    parser.add_argument("--semantic-dir", default=None, help="字典目录（默认 semantic/）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="校验字典自洽性")
    p_validate.set_defaults(func=_cmd_validate)

    p_metrics = sub.add_parser("metrics", help="列出指标")
    p_metrics.set_defaults(func=_cmd_metrics)

    p_compile = sub.add_parser("compile", help="把 DSL 编译成 SQL")
    p_compile.add_argument("--file", help="DSL 文件路径")
    p_compile.add_argument("--json", help="DSL 字符串")
    p_compile.add_argument("--engine", default=None, choices=["hive", "pg", "duckdb"])
    p_compile.set_defaults(func=_cmd_compile)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
