"""重新生成编译快照。

只在确认口径变更后运行，生成的 diff 必须人工看一遍再提交：

    python -m semantic.tests.regen
    git diff semantic/tests/golden
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from semantic import Registry, compile_query  # noqa: E402
from semantic.tests.fixtures import ALL_TIME, base_dsl_for, golden_name  # noqa: E402

HERE = Path(__file__).resolve().parent


def main() -> int:
    registry = Registry.load()
    golden_dir = HERE / "golden"
    golden_dir.mkdir(exist_ok=True)

    cases = yaml.safe_load((HERE / "cases.yml").read_text(encoding="utf-8"))
    written = 0
    for case in cases["compile"]:
        compiled = compile_query(case["dsl"], registry)
        (golden_dir / f"{case['name']}.sql").write_text(compiled.sql + "\n", encoding="utf-8")
        written += 1

    for metric in registry.metrics.values():
        compiled = compile_query(base_dsl_for(metric, registry), registry)
        (golden_dir / golden_name(metric.name)).write_text(compiled.sql + "\n", encoding="utf-8")
        written += 1

    print(f"wrote {written} snapshot files into semantic/tests/golden/")
    print(f"time range used for per-metric snapshots: {ALL_TIME['start']} .. {ALL_TIME['end']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
