"""Streamlit 演示页（D9）。

两种模式，刻意分开：
    自然语言  需要 DeepSeek Key，走完整 Agent（检索 → 三态 → 修复 → 编译 → 执行）
    DSL 直编  不需要 Key，直接验证语义层：改 DSL → 编译 → 看 SQL 和结果

把 SQL 和 DSL 摆在明面上不是装饰：可解释、可纠错、可审计，
演示时现场改一个维度重跑，比讲十分钟架构有用。

    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.config import ConfigError, load_llm_config  # noqa: E402
from agent.llm import DeepSeekClient  # noqa: E402
from agent.pipeline import Agent  # noqa: E402
from semantic import Registry, SemanticError, compile_query  # noqa: E402
from semantic.executor import DuckDBExecutor, PostgresExecutor  # noqa: E402

DEFAULT_DSN = os.environ.get("DW_PG_DSN", "postgresql://dw:dwpass@localhost:55432/warehouse")
EXAMPLE_DSL = json.dumps(
    {
        "metrics": ["buy_pv_rate"],
        "dimensions": ["dt"],
        "time_range": {"start": "2017-11-25", "end": "2017-12-03"},
    },
    ensure_ascii=False,
    indent=2,
)

st.set_page_config(page_title="淘宝数仓 · 指标语义层问数", page_icon="📊", layout="wide")


@st.cache_resource(show_spinner="首次运行：从 Parquet 重建 DuckDB 数仓替身（约 30 秒）")
def _duckdb():
    from tools.duckdb_warehouse import connect

    return connect()


@st.cache_resource
def _registry() -> Registry:
    return Registry.load()


@st.cache_resource
def _pg(dsn: str) -> PostgresExecutor:
    return PostgresExecutor(dsn)


def _executor(engine: str, enabled: bool):
    if not enabled:
        return None
    return DuckDBExecutor(_duckdb()) if engine == "duckdb" else _pg(DEFAULT_DSN)


def _table(result) -> None:
    if result is None:
        return
    payload = [dict(zip(result.columns, row)) for row in result.rows]
    st.dataframe(payload, width="stretch", hide_index=True)
    st.caption(f"{result.row_count} 行 · 引擎 {result.engine} · {result.elapsed_ms:.0f} ms")


def _render_query(answer, registry: Registry, engine: str, execute: bool) -> None:
    st.success(f"已编译（尝试 {answer.attempts} 次）")
    st.markdown(answer.explanation)

    if answer.assumptions:
        st.info("假设：" + "；".join(answer.assumptions))

    if answer.result is not None:
        _table(answer.result)
    elif execute:
        st.warning("已编译但未执行")

    if answer.error:
        st.error(json.dumps(answer.error, ensure_ascii=False))

    with st.expander("DSL（可编辑后重跑）"):
        st.session_state["dsl_text"] = json.dumps(answer.dsl, ensure_ascii=False, indent=2)
        st.session_state["dsl_readonly"] = False
    with st.expander("SQL"):
        st.code(answer.sql, language="sql")


def _render_agent(answer) -> None:
    if answer.kind == "clarify":
        clarify = answer.clarify or {}
        st.warning(clarify.get("question", "需要澄清"))
        for option in clarify.get("options", []):
            st.markdown(f"- {option}")
        st.caption(f"原因：{clarify.get('reason', '')}")
    elif answer.kind == "refuse":
        refuse = answer.refuse or {}
        st.error(refuse.get("reason", "无法回答"))
        if refuse.get("closest"):
            st.caption("可以试试：" + "、".join(refuse["closest"]))
    elif answer.kind == "error":
        st.error(json.dumps(answer.error, ensure_ascii=False))
    st.caption(
        f"尝试 {answer.attempts} 次 · tokens {answer.prompt_tokens}+{answer.completion_tokens}"
        f" · 端到端 {answer.elapsed_ms:.0f} ms"
    )


def main() -> None:
    registry = _registry()

    st.title("淘宝用户行为数仓 · 指标语义层问数")
    st.caption("模型不写 SQL：它只输出受限 DSL，SQL 由语义层确定性地编译出来。")

    with st.sidebar:
        st.header("设置")
        engine = st.radio("执行引擎", ["duckdb", "pg"], horizontal=True,
                          help="duckdb 是本地替身；pg 是同步过的服务层快路径")
        execute = st.toggle("执行 SQL", value=True)

        key_ready = True
        try:
            config = load_llm_config()
        except ConfigError:
            key_ready = False
            config = None
        st.divider()
        if key_ready and config is not None:
            st.success(f"模型已配置：{config.model}")
        else:
            st.warning("未配置 DEEPSEEK_API_KEY\n\n只能使用「DSL 直编」模式")

        st.divider()
        st.caption(f"指标 {len(registry.metrics)} · 维度 {len(registry.dimensions)} · 数据集 {len(registry.datasets)}")

    tab_nl, tab_dsl, tab_dict = st.tabs(["自然语言", "DSL 直编", "指标字典"])

    # ---------------- 自然语言 ----------------
    with tab_nl:
        question = st.text_input("问一句", value="每天的浏览购买转化率", key="question")
        if st.button("提问", type="primary", disabled=not key_ready):
            with st.spinner("检索 → 生成 → 校验 → 编译 → 执行"):
                agent = Agent(
                    registry,
                    DeepSeekClient(config),
                    executor=_executor(engine, execute),
                    engine=engine,
                )
                try:
                    answer = agent.answer(question, execute=execute)
                except Exception as exc:  # 演示页不吞异常，直接展示
                    st.exception(exc)
                    answer = None
            if answer is not None:
                st.session_state["last_answer"] = answer

        last = st.session_state.get("last_answer")
        if last is not None:
            st.divider()
            if last.kind == "query":
                _render_query(last, registry, engine, execute)
            _render_agent(last)

        if not key_ready:
            st.code(
                "Copy-Item .env.example .env\nnotepad .env   # 填 DEEPSEEK_API_KEY=sk-...\npython -m agent check",
                language="powershell",
            )

    # ---------------- DSL 直编 ----------------
    with tab_dsl:
        st.caption("不经过模型，直接验证语义层：分区裁剪、比率同源、维度可用性、排序确定性。")
        text = st.text_area("DSL", value=st.session_state.get("dsl_text", EXAMPLE_DSL), height=220)
        if st.button("编译并执行", key="run_dsl"):
            try:
                dsl = json.loads(text)
            except json.JSONDecodeError as exc:
                st.error(f"JSON 解析失败：{exc}")
                dsl = None

            if dsl is not None:
                try:
                    compiled = compile_query(dsl, registry, engine=engine)
                except SemanticError as exc:
                    st.error(f"{exc.code}（{exc.action}）：{exc.message}")
                    st.json(exc.details)
                else:
                    st.code(compiled.sql, language="sql")
                    if compiled.assumptions:
                        st.info("假设：" + "；".join(compiled.assumptions))
                    if execute:
                        try:
                            result = _executor(engine, True).run(compiled.sql)
                        except Exception as exc:
                            st.error(f"执行失败：{exc}")
                        else:
                            _table(result)

    # ---------------- 指标字典 ----------------
    with tab_dict:
        rows = [
            {
                "指标": m.name,
                "名称": m.label,
                "口径": m.definition,
                "单位": m.unit,
                "类型": m.kind,
                "可用维度": ", ".join(m.dimensions),
                "版本": m.version,
            }
            for m in sorted(registry.metrics.values(), key=lambda x: (x.dataset, x.name))
        ]
        st.dataframe(rows, width="stretch", hide_index=True)


main()
