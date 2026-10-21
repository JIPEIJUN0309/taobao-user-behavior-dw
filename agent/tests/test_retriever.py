"""检索与歧义检测的回归测试。"""

from __future__ import annotations

from agent.retriever import Retriever
from semantic import Registry

REGISTRY = Registry.load()
RETRIEVER = Retriever(REGISTRY)


def _names(cards: list[dict]) -> list[str]:
    return [c["name"] for c in cards]


def test_rate_question_hits_the_right_metric_first() -> None:
    cards = RETRIEVER.search("转化率是多少")
    assert "buy_pv_rate" in _names(cards)[:3]


def test_cart_rate_question_surfaces_cart_metrics() -> None:
    cards = RETRIEVER.search("加购转化率")
    names = _names(cards)
    assert "pv_to_cart_rate" in names[:3]
    assert "buy_pv_rate" in names[:5]


def test_repurchase_question() -> None:
    cards = RETRIEVER.search("复购用户有多少")
    assert "repurchase_uv" in _names(cards)[:3]


def test_retention_question() -> None:
    cards = RETRIEVER.search("次日留存率")
    assert "retention_d1" in _names(cards)[:3]


def test_top_k_is_bounded() -> None:
    cards = RETRIEVER.search("随便问点什么", top_k=8)
    assert len(cards) == 8
    assert {"name", "label", "definition", "dimensions"} <= set(cards[0])


def test_ambiguity_detects_overlapping_wording() -> None:
    # 「加购转化率」同时包含「转化率」和「加购转化率」两种说法
    candidates = RETRIEVER.ambiguity("加购转化率是多少")
    names = _names(candidates)
    assert len(candidates) >= 2
    assert "buy_pv_rate" in names
    assert "pv_to_cart_rate" in names


def test_no_ambiguity_for_specific_question() -> None:
    assert RETRIEVER.ambiguity("次日的留存率") == []


def test_dimension_hints() -> None:
    hints = RETRIEVER.dimension_hints("高活跃用户的转化率，按天看")
    assert "user_tier" in hints
    assert "dt" in hints
