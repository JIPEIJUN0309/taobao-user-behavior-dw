# 指标语义层 / Semantic Layer

把「转化率」这类业务词，翻译成**唯一口径、可回归、可审计**的 SQL。

- 设计文档：[`docs/semantic_layer_design.md`](../docs/semantic_layer_design.md)
- 当前状态：D1–D5 已完成（字典 + 校验 + 编译器 + 快照测试 + CLI + 双路由 + 真数据核对），D6 起为 Agent 三态闭环

**核心约定：模型不写 SQL。** 自然语言只会被翻译成一个受限 DSL，SQL 由这里的模板确定性地编译出来。

---

## 快速开始

```bash
pip install -r requirements-semantic.txt

python -m semantic validate      # 校验字典自洽（不自洽会拒绝启动）
python -m semantic metrics       # 列出 20 个指标
python -m semantic compile --json '{"metrics":["buy_pv_rate"],"dimensions":["dt"],"time_range":{"start":"2017-11-25","end":"2017-12-03"}}'

python -m pytest semantic/tests -q       # 48 条：快照 + 错误码 + 硬约束
python tools/verify_metrics.py           # 拿真实数据跑一遍，与手写黄金 SQL 逐行比对
```

## 双路由（D5）

| 路由 | 引擎 | 覆盖 | 延迟 |
|---|---|---|---|
| 快路径 | PostgreSQL | `dws_user_summary` / `dws_item_summary` / 两张 tier 维表，共 1026 万行 | 毫秒级 |
| 明细 | Hive on Tez | `dwd_user_behavior` 9891 万行 | 20–40s |

```bash
# 1) 把服务层表同步到 PG（staging 表 + 事务换名，查询看不到半成品）
python tools/sync_to_pg.py --dsn postgresql://dw:dwpass@localhost:55432/warehouse

# 2) 核对双路由：同一指标在 PG 与 DuckDB 上必须给出同样的结果
python tools/verify_metrics.py --pg-dsn postgresql://dw:dwpass@localhost:55432/warehouse
```

执行层在 `semantic/executor.py`：`DuckDBExecutor` / `PostgresExecutor`，只认 SQL，
不认识指标——口径判定全部发生在编译期。跨引擎的三种坑已经写进测试：
`LAG` 必须先于过滤、`DESC` 需显式 `NULLS LAST`、`numeric` 统一收敛成 `float`。

## 上层：Agent / 评测 / 演示页

| 目录 | 作用 | 需要 Key |
|---|---|---|
| `agent/` | 检索 + 三态（query/clarify/refuse）+ 结构化修复 | 是（`python -m agent ask`） |
| `eval/run_l1.py` | 黄金 DSL 回放，锁住 60 条问题的可编译性与结果指纹 | **否** |
| `eval/run_l2.py` | 真模型六项指标 + 失败分析 → `l2_report.md` | 是 |
| `app/streamlit_app.py` | 演示页：自然语言 / DSL 直编两个页签 | 自然语言页签才需要 |

```bash
python eval/run_l1.py                 # 40/40，约 70 秒
streamlit run app/streamlit_app.py    # http://localhost:8501
```

Key 放 `.env`（已被 gitignore）或环境变量，先用 `python -m agent check` 验通。

---

## 三个字典文件

| 文件 | 回答的问题 | 关键字段 |
|---|---|---|
| `datasets.yml` | 数据在哪、什么粒度、怎么 join | `grain` / `partition` / `joins` / `cost` / `engine` |
| `dimensions.yml` | 能按什么切 | `column` / `kind` / `values`（枚举穷举） |
| `metrics.yml` | 业务口径到底是什么 | `kind` / `expr` 或 `numerator+denominator` / `dimensions` / `version` |

`kind` 只有四种：

| kind | 用于 | 编译成 |
|---|---|---|
| `additive` | 计数、求和、去重计数 | 一个聚合表达式 |
| `ratio` | 转化率、人均值 | `ROUND(100.0 * 分子 / NULLIF(分母, 0), 4)` |
| `cohort` | 次日留存 | 跨日期自连接（方言相关的日期加一天） |
| `derived` | 环比 `dod`、占比 `share` | 子查询 + 窗口函数 |

---

## 新增一个指标（5 步）

1. 在 `metrics.yml` 里加一条：写清 `definition`（中文口径）、`dataset`、`kind`、表达式、`dimensions`、`columns`。
2. `python -m semantic validate` —— C1–C5 会告诉你数据集/维度/列是否对得上。
3. `python -m semantic.tests.regen` —— 生成快照，**`git diff semantic/tests/golden` 人工看一遍**。
4. `python tools/verify_metrics.py` —— 在真数据上核对（若该指标有对应案例）。
5. 在 `CHANGELOG.md` 记一行，口径变化必须升 `version`。

---

## 三个口径陷阱（都写进了代码注释和测试）

1. **`dws_item_summary.user_cnt` 不可跨商品相加。** 它是每商品的去重用户数，按类目求和会把同一个人算多次。所以「类目转化率」定义为**次数口径**（`SUM(buy)/SUM(pv)`），用户口径必须回 `dwd` 做 `COUNT(DISTINCT)`，两个指标分开定义、各自绑定数据集。
2. **次数口径与用户口径差 30 倍。** 前者 2.26%，后者 68.12%，都是合法口径。所以「转化率」三个字不能推断，必须由 `metrics` 显式指定。
3. **默认时间范围必须披露。** 未给时间时默认最近 7 天，并写进 `assumptions` 返回给上层。静默假设是最坏的一种正确。

---

## 已知边界（v1）

- 一次查询只落在一个数据集上（`E_MULTI_DATASET`），不支持跨数据集的比率
- 派生指标与留存指标只能单独查（`E_MIXED_KINDS`）
- `dws_*` 是全期汇总表，不接受时间过滤（`E_TIME_NOT_SUPPORTED`），否则会静默算错
- 环比会自动回溯前一日数据；这一步写在窗口层，**不能在过滤之后再算 LAG**（`test_dod_filters_after_window` 守着这条）

---

## 目录

```
semantic/
├── datasets.yml / dimensions.yml / metrics.yml   # 唯一口径来源
├── registry.py     # 加载 + C1–C5 一致性校验
├── validator.py    # DSL 校验（形状 + 语义）
├── compiler.py     # DSL → SQL（唯一生成 SQL 的地方）
├── errors.py       # 错误码 + action(repair/clarify/refuse)
├── cli.py          # validate / metrics / compile
└── tests/
    ├── cases.yml   # 8 条编译用例 + 12 条报错用例
    ├── fixtures.py # 每个指标的最小 DSL
    ├── golden/     # 28 份 SQL 快照
    └── test_compiler.py
```
