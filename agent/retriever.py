"""指标检索：先把 20 个指标缩到 8 张卡片，再交给模型。

为什么不能把全部指标塞进 prompt：
    现在 20 个还塞得下，几百个就不行了；而且塞得越多，模型越容易挑错口径。
    检索这一步同时也是「歧义检测」的地方 —— 一个问题命中多个指标时，
    要反问而不是猜。
"""

from __future__ import annotations

import logging

import jieba
from rank_bm25 import BM25Okapi

from semantic.registry import MetricDef, Registry

jieba.setLogLevel(logging.ERROR)

DEFAULT_TOP_K = 8

# 维度在中文里有很多叫法，靠 label 匹配会漏（比如「按天看」里没有「日期」）。
# 这是一份刻意保持很小的别名表，属于启发式，不追求覆盖全部说法。
DIMENSION_ALIASES: dict[str, tuple[str, ...]] = {
    "dt": ("每天", "每日", "按天", "当天", "哪天", "哪一天", "趋势", "逐日"),
    "hour": ("小时", "时段", "几点"),
    "behavior_type": ("行为类型", "行为种类"),
    "category_id": ("类目", "品类", "行业"),
    "user_tier": ("用户分层", "活跃分层", "高活跃", "中活跃", "低活跃"),
    "item_tier": ("商品分层", "成交分层", "动销"),
}


def _tokenize(text: str) -> list[str]:
    return [tok for tok in jieba.lcut(text) if tok.strip()]


class Retriever:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.metrics: list[MetricDef] = sorted(registry.metrics.values(), key=lambda m: m.name)
        self._docs = [self._document(m) for m in self.metrics]
        self._corpus = [_tokenize(doc) for doc in self._docs]
        self._bm25 = BM25Okapi(self._corpus)
        self._keys = [
            [k for k in (m.label, *m.synonyms) if len(k) >= 2] for m in self.metrics
        ]

    # ---------------- 文档与卡片 ----------------
    @staticmethod
    def _document(metric: MetricDef) -> str:
        parts = [metric.label, metric.definition, *metric.synonyms, *metric.sample_questions]
        return " ".join(p for p in parts if p)

    def card(self, metric: MetricDef) -> dict:
        return {
            "name": metric.name,
            "label": metric.label,
            "definition": metric.definition,
            "unit": metric.unit,
            "kind": metric.kind,
            "dimensions": metric.dimensions,
            "synonyms": metric.synonyms,
        }

    # ---------------- 检索 ----------------
    def search(self, question: str, top_k: int = DEFAULT_TOP_K) -> list[dict]:
        scores = list(self._bm25.get_scores(_tokenize(question)))
        # 精确命中（同义词/名字直接出现在问题里）要压过 BM25 的分词噪声
        for idx, keys in enumerate(self._keys):
            if any(key in question for key in keys):
                scores[idx] += 3.0
        ranked = sorted(
            range(len(self.metrics)),
            key=lambda i: (-scores[i], self.metrics[i].name),
        )
        return [self.card(self.metrics[i]) for i in ranked[:top_k]]

    # ---------------- 歧义 ----------------
    def ambiguity(self, question: str) -> list[dict]:
        """问题里出现了多个指标的说法时，把候选都摆出来让模型/用户选。

        v1 规则很朴素：指标名或同义词作为子串命中 >= 2 个指标即视为歧义。
        典型例子：「加购转化率」同时命中「转化率」和「加购转化率」。
        """
        hits: list[MetricDef] = []
        for metric, keys in zip(self.metrics, self._keys):
            if any(key in question for key in keys):
                hits.append(metric)
        if len(hits) < 2:
            return []
        return [self.card(m) for m in hits]

    # ---------------- 维度提示 ----------------
    def dimension_hints(self, question: str) -> list[str]:
        hints: list[str] = []
        for name, dim in self.registry.dimensions.items():
            if dim.label in question or name in question:
                hints.append(name)
            elif any(label in question for label in dim.value_labels.values()):
                hints.append(name)
            elif any(alias in question for alias in DIMENSION_ALIASES.get(name, ())):
                hints.append(name)
        return sorted(set(hints))
