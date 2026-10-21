"""问数 Agent：把自然语言变成 DSL。

分层约定（与 docs/semantic_layer_design.md 一致）：
    retriever  检索 top-k 指标卡片
    llm        只产出三态 JSON：query / clarify / refuse
    pipeline   校验 → 修复 → 编译 → 执行 → 解释

模型永远拿不到 SQL 模板，也就编不出 SQL。
"""
