"""澄清策略：在调用模型之前，先判断「这个问题到底有没有唯一的指标可答」。

为什么放在代码里而不是提示词里：
    「请谨慎，不确定就反问」这种指令，模型会稳定地理解成「尽量别反问」。
    把它变成一条可判定的规则（问题里找不到指标说法 -> 必须澄清），
    行为才可复现、可测试、可回归。

判定顺序：
    R1 问题里没有任何指标说法      -> 不抢先拦截，改为在提示里要求「澄清或拒答」
    R2 维度提示能唯一锁定指标      -> 不澄清，直接答（「各商品分层的加购率」只有次数口径支持 item_tier）
    R3 命中比率类泛词（转化率/加购率）-> 澄清（不同口径数值差 30 倍）
    R4 命中 dod 但没提基指标        -> 澄清（哪个指标的环比）
"""

from __future__ import annotations

from dataclasses import dataclass, field

from semantic.registry import MetricDef, Registry


@dataclass(frozen=True)
class ClarifyDecision:
    reason: str
    question: str
    options: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"question": self.question, "options": self.options, "reason": self.reason}


def _terms(metric: MetricDef) -> list[str]:
    return [t for t in [metric.label, *metric.synonyms] if len(t) >= 2]


class ClarifyPolicy:
    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.metrics: list[MetricDef] = sorted(registry.metrics.values(), key=lambda m: m.name)
        self._terms = {m.name: _terms(m) for m in self.metrics}

    # ---------------- 工具 ----------------
    def has_metric_term(self, question: str) -> bool:
        return any(t in question for terms in self._terms.values() for t in terms)

    def _raw_hits(self, question: str) -> list[tuple[MetricDef, str]]:
        hits: list[tuple[MetricDef, str]] = []
        for metric in self.metrics:
            matched = [t for t in self._terms[metric.name] if t in question]
            if matched:
                hits.append((metric, max(matched, key=len)))
        return hits

    def _siblings(self, term: str, metric: MetricDef) -> list[MetricDef]:
        """还有哪些比率指标的说法「包含着」这个词 —— 它们才是歧义的来源。"""
        out = []
        for other in self.metrics:
            if other.name == metric.name or other.kind != "ratio":
                continue
            if any(term != t and term in t for t in self._terms[other.name]):
                out.append(other)
        return out

    def _is_generic(self, metric: MetricDef, term: str) -> bool:
        # 只有比率类指标的泛词才算歧义：「购买次数」虽然也出现在「人均购买次数」里，
        # 但它本身没有口径之争，不该被拦下来。
        return metric.kind == "ratio" and bool(self._siblings(term, metric))

    def _base_mentioned(self, metric: MetricDef, question: str) -> bool:
        if not metric.base:
            return True
        base = self.registry.metrics.get(metric.base)
        if base is None:
            return True
        return any(t in question for t in self._terms[base.name])

    @staticmethod
    def _option(metric: MetricDef) -> str:
        return f"{metric.label}（{metric.name}）：{metric.definition}"

    def _resolve_by_dimensions(
        self, metric: MetricDef, term: str, hinted_dims: list[str]
    ) -> MetricDef | None:
        """维度提示能唯一确定口径时，用它消歧。

        「各商品分层的加购率」：用户口径不支持 item_tier，只有次数口径能答，不需要反问。
        「各类目的加购率前 5」：两种口径都支持 category_id，无法消歧，必须反问。
        """
        if not hinted_dims:
            return None
        pool = [metric, *self._siblings(term, metric)] if self._is_generic(metric, term) else [metric]
        supported = [m for m in pool if any(d in m.dimensions for d in hinted_dims)]
        return supported[0] if len(supported) == 1 else None

    # ---------------- 主判定 ----------------
    def check(self, question: str, hinted_dims: list[str] | None = None) -> ClarifyDecision | None:
        hinted_dims = hinted_dims or []
        hits = self._raw_hits(question)
        if not hits:
            return None  # R1：交给模型在 clarify/refuse 之间选，见 prompt 的 no_metric 提示

        # 去掉被更长命中词覆盖的说法（「人均浏览次数」里不再单独算「浏览次数」）
        hits = [(m, t) for m, t in hits if not any(t != t2 and t in t2 for _, t2 in hits)]

        # R2 维度消歧：命中词是泛词但维度能唯一锁定 -> 直接答
        resolved_names: set[str] = set()
        for metric, term in hits:
            if self._is_generic(metric, term):
                picked = self._resolve_by_dimensions(metric, term, hinted_dims)
                if picked is not None:
                    resolved_names.add(picked.name)
                    # 移除触发消歧的泛词命中，再补上被选中的指标（不能把原指标拼回来）
                    hits = [(m, t) for m, t in hits if (m.name, t) != (metric.name, term)]
                    hits.append((picked, term))
                    break

        # 派生指标已经「吃掉」它的基指标：「转化率的日环比」里那个「转化率」就是基指标，
        # 不能再按口径歧义拦下来。
        derived_bases = {
            m.base for m, _ in hits if m.kind == "derived" and m.base
        }
        hits = [(m, t) for m, t in hits if not (m.name in derived_bases and m.kind != "derived")]

        generic = [
            (m, t) for m, t in hits if m.name not in resolved_names and self._is_generic(m, t)
        ]
        specific = [
            (m, t) for m, t in hits if m.name not in resolved_names and not self._is_generic(m, t)
        ]

        # 泛词与具体词同时命中时看具体词的性质：
        # 「加购到购买的转化率」里的「加购到购买」是修饰语 -> 落到 cart_to_buy_rate；
        # 「高活跃用户的转化率」里的「活跃用户」只是维度限定，指标仍是口径不明的「转化率」。
        if generic and specific:
            qualifier_uses_hint = any(
                any(d in m.dimensions for d in hinted_dims) for m, _ in specific
            )
            if qualifier_uses_hint or len(specific) > 1:
                specific = []
            else:
                generic = []
                resolved_names.update(m.name for m, _ in specific)

        # R3 只要还剩下口径不明的比率泛词，就必须反问 —— 哪怕同时命中了别的计数指标
        if generic:
            options: list[str] = []
            for metric, term in generic:
                options.append(self._option(metric))
                options.extend(self._option(s) for s in self._siblings(term, metric))
            return ClarifyDecision(
                reason="E_AMBIGUOUS_METRIC",
                question="这个指标有几种口径，数值差很多，你要哪一种？",
                options=options[:4],
                candidates=[m.name for m, _ in generic],
            )

        if not specific:
            return None

        # R4 派生指标（只针对环比：占比的口径由维度决定，不需要问基指标）
        for metric, _ in specific:
            if metric.kind == "derived" and metric.op == "dod" and not self._base_mentioned(metric, question):
                base = self.registry.metrics.get(metric.base or "")
                hint = f"（它基于 {base.label}）" if base else ""
                return ClarifyDecision(
                    reason="E_DERIVED_BASE_MISSING",
                    question=f"「{metric.label}」要基于哪个指标算{hint}？",
                    options=[self._option(metric)],
                    candidates=[metric.name],
                )

        return None
