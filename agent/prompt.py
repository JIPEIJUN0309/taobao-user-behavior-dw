"""Prompt 构造。

三条硬约束写在 system 里，且都不依赖模型的自觉：
  1. 只有三种输出形态（query / clarify / refuse），没有自由文本出口
  2. 指标只能从候选卡片里选，不能发明
  3. 不给 SQL 模板 —— 模型看不到 SQL，就编不出 SQL
"""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """你是淘宝用户行为数仓的问数助手。你不写 SQL，只输出一个 JSON 对象。

## 输出三种形态之一

1) 可以直接查：
{"type":"query","dsl":<DSL>,"assumptions":["时间范围未指定，默认最近 7 天"]}
2) 必须问清楚：
{"type":"clarify","question":"...","options":["A：...","B：..."],"reason":"E_AMBIGUOUS_METRIC"}
3) 数据和指标都答不了：
{"type":"refuse","reason":"数据集无价格字段，无法计算毛利率","closest":["category_conv_rate"]}

## DSL 规范

{
  "metrics": ["<指标名>"],             // 1-5 个，只能从候选卡片里选
  "dimensions": ["<维度名>"],          // 0-2 个，必须在该指标的可用维度里
  "filters": [{"field":"<维度>","op":"=","value":"<值>"}],  // op ∈ =, in, >=, <=, between
  "time_range": {"start":"2017-11-25","end":"2017-12-03"},  // 缺省=最近 7 天，必须在数据范围内
  "order_by": [{"field":"<维度或本次指标>","dir":"desc"}],
  "limit": 1000
}

## 规则

- 指标名只能用候选卡片里出现的，绝不发明新指标。
- 卡片标注「可按时间过滤：否」的是全期汇总表，dsl 里**不要写 time_range**，写了会被编译期拒绝。
- 只有当问题里的指标说法**本身对应多个口径**时才 clarify；如果候选卡片里有和问题说法完全一致的
  指标名或同义词（例如「加购转化率」「类目转化率」），直接用那个指标查，不要反问。
- 用户要的东西数据里没有（价格、毛利率、用户画像、未来预测）时，必须 refuse，并给出最接近的可查指标。
- 指标绑定哪张表、怎么 join 由语义层决定；你不需要、也不允许写表名。
- 只输出 JSON，不要 Markdown 代码块，不要解释文字。
"""


def _format_cards(cards: list[dict[str, Any]]) -> str:
    lines = []
    for card in cards:
        dims = ", ".join(card["dimensions"]) or "无"
        filterable = "是" if card.get("time_filterable", True) else "否（全期汇总）"
        lines.append(
            f"- {card['name']}（{card['label']}，单位 {card['unit']}）："
            f"{card['definition']}；可用维度：{dims}；可按时间过滤：{filterable}；"
            f"同义词：{'、'.join(card['synonyms']) or '无'}"
        )
    return "\n".join(lines)


def build_messages(
    question: str,
    cards: list[dict[str, Any]],
    *,
    ambiguity: list[dict[str, Any]] | None = None,
    dimension_hints: list[str] | None = None,
    time_bounds: tuple[str, str] | None = None,
    no_metric: bool = False,
) -> list[dict[str, str]]:
    sections = [f"## 用户问题\n{question}", f"## 候选指标\n{_format_cards(cards)}"]

    if no_metric:
        sections.append(
            "## 注意\n这个问题里没有出现任何指标名称。按下面顺序判断：\n"
            "1. 先看数据里到底有没有这个概念：如果指标和维度都不在候选卡片里"
            "（价格、品牌、城市、年龄、未来预测），输出 refuse，并说明缺什么；\n"
            "2. 只有概念存在、只是用户没指明是哪一个指标时，才输出 clarify，给出 2-3 个候选；\n"
            "3. 不要猜一个指标就直接查。"
        )

    if ambiguity:
        names = "、".join(f"{c['name']}（{c['label']}）" for c in ambiguity)
        sections.append(
            f"## 疑似歧义\n这个问题同时命中多个指标：{names}。"
            "若无法从问句中确定唯一口径，请输出 clarify。"
        )

    if dimension_hints:
        sections.append(f"## 可能相关的维度\n{'、'.join(dimension_hints)}")

    if time_bounds:
        sections.append(f"## 数据范围\n{time_bounds[0]} 至 {time_bounds[1]}（超出会被拒绝）")

    sections.append("## 输出\n只输出一个 JSON 对象。")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(sections)},
    ]


def build_repair_messages(
    messages: list[dict[str, str]],
    previous_reply: str,
    error_code: str,
    error_message: str,
    details: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """把结构化错误回喂给模型。错误码 + 哪里错 + 合法取值，三者缺一不可。"""
    payload = {
        "error_code": error_code,
        "message": error_message,
        "details": details or {},
    }
    repair = (
        "上一次输出没有通过校验。\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "请只输出修正后的 JSON：不要解释，不要加代码块。"
    )
    return [
        *messages,
        {"role": "assistant", "content": previous_reply},
        {"role": "user", "content": repair},
    ]
