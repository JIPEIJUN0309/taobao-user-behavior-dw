"""pipeline 状态机的回归测试（不联网、不连库）。"""

from __future__ import annotations

import pytest

from agent.fake import FailingLLM, ScriptedLLM
from agent.pipeline import Agent
from semantic import Registry

REGISTRY = Registry.load()

GOOD_DSL = {
    "metrics": ["buy_pv_rate"],
    "dimensions": ["dt"],
    "time_range": {"start": "2017-11-25", "end": "2017-12-03"},
    "order_by": [{"field": "dt", "dir": "asc"}],
}


def make_agent(replies: list) -> tuple[Agent, ScriptedLLM]:
    llm = ScriptedLLM(replies)
    return Agent(REGISTRY, llm), llm


def test_happy_path() -> None:
    agent, llm = make_agent([{"type": "query", "dsl": GOOD_DSL}])
    answer = agent.answer("每天的浏览购买转化率", execute=False)

    assert answer.kind == "query"
    assert answer.attempts == 1
    assert "GROUP BY t.dt" in answer.sql
    assert "NULLIF(" in answer.sql
    assert "口径：" in answer.explanation
    assert "浏览购买转化率" in answer.explanation
    assert len(llm.calls) == 1


def test_repair_after_unknown_metric() -> None:
    agent, llm = make_agent(
        [
            {"type": "query", "dsl": {"metrics": ["gmv"], "dimensions": ["dt"]}},
            {"type": "query", "dsl": GOOD_DSL},
        ]
    )
    answer = agent.answer("每天的浏览购买转化率", execute=False)

    assert answer.kind == "query"
    assert answer.attempts == 2
    assert len(llm.calls) == 2
    # 第二次请求必须把错误码和合法候选带回去，否则等于让模型瞎猜
    repair_message = llm.calls[1][-1]["content"]
    assert "E_UNKNOWN_METRIC" in repair_message
    assert "candidates" in repair_message


def test_repair_then_give_up() -> None:
    bad = {"type": "query", "dsl": {"metrics": ["gmv"], "dimensions": ["dt"]}}
    agent, llm = make_agent([bad, bad, bad])
    answer = agent.answer("每天的浏览购买转化率", execute=False)

    assert answer.kind == "refuse"
    assert answer.attempts == 3  # 首次 + 两次修复
    assert len(llm.calls) == 3


def test_fatal_error_is_not_retried() -> None:
    """跨数据集的错误编译期修不了，重试只是浪费 token。"""
    agent, llm = make_agent([{"type": "query", "dsl": {"metrics": ["pv_cnt", "repurchase_uv"]}}])
    answer = agent.answer("对比浏览量和复购用户", execute=False)

    assert answer.kind == "refuse"
    assert answer.attempts == 1
    assert len(llm.calls) == 1
    assert "E_MULTI_DATASET" in answer.refuse["reason"]


def test_clarify_preempts_llm() -> None:
    """口径歧义在调模型之前就该判定：省下这一次调用，行为也可复现。"""
    agent, llm = make_agent([{"type": "query", "dsl": GOOD_DSL}])
    answer = agent.answer("转化率是多少", execute=False)

    assert answer.kind == "clarify"
    assert answer.attempts == 0
    assert len(answer.clarify["options"]) >= 2
    assert answer.clarify["reason"] == "E_AMBIGUOUS_METRIC"
    assert llm.calls == []


def test_specific_question_is_not_preempted() -> None:
    agent, llm = make_agent([{"type": "query", "dsl": GOOD_DSL}])
    answer = agent.answer("每天的浏览购买转化率", execute=False)

    assert answer.kind == "query"
    assert len(llm.calls) == 1


def test_no_metric_question_gets_a_directive() -> None:
    """没有指标词的题（毛利/画像/预测）不再猜，提示里要求 clarification 或 refusal。"""
    agent, llm = make_agent([{"type": "refuse", "reason": "数据里没有价格字段", "closest": []}])
    answer = agent.answer("毛利率是多少", execute=False)

    assert answer.kind == "refuse"
    user_message = llm.calls[0][1]["content"]
    assert "没有出现任何指标名称" in user_message


def test_refuse_passthrough() -> None:
    agent, _ = make_agent(
        [{"type": "refuse", "reason": "数据集无价格字段", "closest": ["category_conv_rate"]}]
    )
    answer = agent.answer("毛利率是多少", execute=False)

    assert answer.kind == "refuse"
    assert "价格" in answer.refuse["reason"]
    assert answer.refuse["closest"] == ["category_conv_rate"]


def test_clarify_from_semantic_error() -> None:
    """模型给了 share 指标但没给拆分维度，校验器判为 clarify，pipeline 要转成追问。"""
    agent, _ = make_agent(
        [{"type": "query", "dsl": {"metrics": ["behavior_share"], "dimensions": ["dt"]}}]
    )
    answer = agent.answer("每天的行为占比", execute=False)

    assert answer.kind == "clarify"
    assert answer.clarify["reason"] == "E_DIM_REQUIRED"
    assert "behavior_type" in answer.clarify["question"]


def test_invalid_json_then_repair() -> None:
    agent, llm = make_agent(["这不是 JSON", {"type": "query", "dsl": GOOD_DSL}])
    answer = agent.answer("每天的浏览购买转化率", execute=False)

    assert answer.kind == "query"
    assert answer.attempts == 2
    assert "E_INVALID_DSL" in llm.calls[1][-1]["content"]


def test_invalid_json_exhausted() -> None:
    agent, _ = make_agent(["不是 JSON", "还不是", "仍然不是"])
    answer = agent.answer("每天的浏览购买转化率", execute=False)
    assert answer.kind == "refuse"
    assert answer.attempts == 3


def test_llm_failure_returns_error() -> None:
    agent = Agent(REGISTRY, FailingLLM("auth failed"))
    answer = agent.answer("每天的浏览购买转化率", execute=False)

    assert answer.kind == "error"
    assert answer.error["code"] == "E_LLM_CALL_FAILED"
    assert "auth failed" in answer.error["message"]


def test_prompt_contains_cards_and_bounds() -> None:
    agent, llm = make_agent([{"type": "query", "dsl": GOOD_DSL}])
    agent.answer("每天的浏览购买转化率", execute=False)

    system = llm.calls[0][0]["content"]
    user = llm.calls[0][1]["content"]
    assert "只输出一个 JSON 对象" in system
    assert "select" not in system.lower()  # prompt 里不能出现 SQL 模板
    assert "buy_pv_rate" in user
    assert "2017-11-25 至 2017-12-03" in user
