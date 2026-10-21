"""编译器回归测试。

两类断言：
  1. 快照：编译结果与 golden/*.sql 逐字节一致 —— 口径不会悄悄变
  2. 性质：分区裁剪、NULLIF 防护、默认窗口披露等硬约束，防止快照被"顺手更新"后失效
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from semantic import Registry, SemanticError, compile_query
from semantic.tests.fixtures import base_dsl_for, golden_name

HERE = Path(__file__).resolve().parent
CASES = yaml.safe_load((HERE / "cases.yml").read_text(encoding="utf-8"))
REGISTRY = Registry.load()


@pytest.mark.parametrize("case", CASES["compile"], ids=[c["name"] for c in CASES["compile"]])
def test_compile_snapshot(case: dict) -> None:
    compiled = compile_query(case["dsl"], REGISTRY)
    golden = (HERE / "golden" / f"{case['name']}.sql").read_text(encoding="utf-8")
    assert compiled.sql + "\n" == golden


@pytest.mark.parametrize("metric", sorted(REGISTRY.metrics), ids=sorted(REGISTRY.metrics))
def test_every_metric_snapshot(metric: str) -> None:
    """20 个指标每个都至少编译一次：字典里改错一个表达式，快照必变。"""
    dsl = base_dsl_for(REGISTRY.metric(metric), REGISTRY)
    compiled = compile_query(dsl, REGISTRY)
    golden = (HERE / "golden" / golden_name(metric)).read_text(encoding="utf-8")
    assert compiled.sql + "\n" == golden


@pytest.mark.parametrize("case", CASES["errors"], ids=[c["name"] for c in CASES["errors"]])
def test_error_codes(case: dict) -> None:
    with pytest.raises(SemanticError) as excinfo:
        compile_query(case["dsl"], REGISTRY)
    assert excinfo.value.code == case["error"]


def test_dictionary_is_healthy() -> None:
    assert len(REGISTRY.metrics) == 20
    assert len(REGISTRY.dimensions) == 6
    assert len(REGISTRY.datasets) == 3


def test_every_metric_is_covered_by_a_snapshot_case() -> None:
    covered = set()
    for case in CASES["compile"]:
        covered.update(case["dsl"]["metrics"])
    assert covered <= set(REGISTRY.metrics)
    assert len(covered) >= 9


def test_window_default_is_disclosed() -> None:
    compiled = compile_query({"metrics": ["pv_cnt"]}, REGISTRY)
    assert "BETWEEN '2017-11-27' AND '2017-12-03'" in compiled.sql
    assert any("默认最近 7 天" in item for item in compiled.assumptions)


def test_ratio_is_guarded_by_nullif() -> None:
    sql = compile_query({"metrics": ["buy_pv_rate"]}, REGISTRY).sql
    assert "NULLIF(" in sql
    assert "100.0 *" in sql
    # 分子分母必须在同一层聚合，不能先除后聚合
    assert "COUNT(DISTINCT CASE WHEN t.behavior_type = 'buy'" in sql


def test_partition_pruning_is_always_present() -> None:
    sql = compile_query(
        {"metrics": ["pv_cnt"], "time_range": {"start": "2017-11-25", "end": "2017-11-30"}},
        REGISTRY,
    ).sql
    assert "t.dt BETWEEN '2017-11-25' AND '2017-11-30'" in sql


def test_joined_dimension_is_qualified() -> None:
    sql = compile_query(
        {"metrics": ["active_uv"], "dimensions": ["user_tier"]}, REGISTRY
    ).sql
    assert "LEFT JOIN taobao_dws.dim_user_tier AS d_user_tier" in sql
    assert "d_user_tier.user_tier" in sql


def test_cohort_follows_engine_dialect() -> None:
    hive_sql = compile_query({"metrics": ["retention_d1"]}, REGISTRY).sql
    pg_sql = compile_query({"metrics": ["retention_d1"]}, REGISTRY, engine="pg").sql
    assert "date_add(t0.dt, 1)" in hive_sql
    assert "to_char(t0.dt::date + 1" in pg_sql


def test_limit_without_order_becomes_deterministic() -> None:
    """有 limit 却没排序时，LIMIT 取到哪几行是随机的 —— 必须补一个确定的顺序。"""
    compiled = compile_query(
        {"metrics": ["pv_cnt"], "dimensions": ["category_id"], "limit": 20}, REGISTRY
    )
    assert "ORDER BY pv_cnt DESC NULLS LAST, t.category_id ASC" in compiled.sql
    assert any("默认按 pv_cnt 降序" in item for item in compiled.assumptions)


def test_grouped_without_order_gets_dimension_order() -> None:
    """编译器永远会拼 LIMIT，所以分组查询必须有确定顺序，否则截断结果不可复现。"""
    compiled = compile_query(
        {"metrics": ["pv_cnt"], "dimensions": ["category_id"]}, REGISTRY
    )
    assert "ORDER BY t.category_id ASC" in compiled.sql


def test_no_limit_no_order_stays_unordered() -> None:
    sql = compile_query({"metrics": ["pv_cnt"]}, REGISTRY).sql
    assert "ORDER BY" not in sql


def test_share_ordered_by_itself_uses_bare_alias() -> None:
    """回归：占比指标按自身排序时，别名在本层，不能带 s. 前缀（否则外层找不到列）。"""
    sql = compile_query(
        {"metrics": ["behavior_share"], "dimensions": ["behavior_type"],
         "order_by": [{"field": "behavior_share", "dir": "desc"}]},
        REGISTRY,
    ).sql
    assert "ORDER BY behavior_share DESC" in sql
    assert "s.behavior_share" not in sql


def test_dod_filters_after_window() -> None:
    """回归：LAG 必须在过滤之内层计算，否则首日环比永远是 NULL。"""
    sql = compile_query(
        {"metrics": ["day_over_day"], "dimensions": ["dt"],
         "time_range": {"start": "2017-11-27", "end": "2017-12-03"}}, REGISTRY
    ).sql
    lag_pos = sql.index("LAG(")
    where_pos = sql.index("WHERE w.dt BETWEEN")
    assert lag_pos < where_pos, "WHERE 出现在窗口计算之前，回溯会失效"
    assert "BETWEEN '2017-11-26'" in sql  # 内层确实向前扩了一天
