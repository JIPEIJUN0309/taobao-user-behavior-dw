# 指标语义层与问数 Agent · 改进方案

> 在现有淘宝用户行为数仓之上，增加一层「受约束的指标语言」，让自然语言问数变成**确定性的 SQL 编译**，而不是让模型自由发挥。

| 项 | 内容 |
|---|---|
| 文档版本 | v0.1（草案，待评审） |
| 日期 | 2026-10-21 |
| 变更范围 | **非破坏性**：不改动现有 ODS/DWD/DWS/ADS 脚本，全部为新增 |
| 预计工时 | 30–40 小时（10 个工作日） |
| 新增依赖 | Trino（可选）、PostgreSQL 复用现有、Streamlit |
| 前置条件 | 现有四层数仓已跑通，ADS 三张表已产出 |

---

## 实现进度（2026-10-21）

| 阶段 | 状态 | 证据 |
|---|---|---|
| D1 字典 | ✅ | 20 指标 / 6 维度 / 3 数据集 |
| D2 校验 | ✅ | C0–C5 启动校验；9 个错误码，每个带 `repair/clarify/refuse` |
| D3 编译器 | ✅ | 4 种 kind；28 份 SQL 快照逐字节锁定 |
| D4 测试与 CLI | ✅ | 50 条语义层测试；`python -m semantic validate / metrics / compile` |
| D5 双路由 | ✅ | 同步 4 张服务层表 1026 万行；PG 与 DuckDB 结果 10/10 一致 |
| D6–D7 Agent | ✅ | 三态输出 + 结构化修复（≤2 次）；19 条离线测试 |
| D8 评测集 | ✅ | 60 条（A25/B15/C10/D10）；L1 40/40 可编译可执行 |
| D9 演示页 | ✅ | Streamlit 单页 + `AppTest` 冒烟测试 |
| D10 真模型评测 | ⏸ 待 Key | 需 `DEEPSEEK_API_KEY`，跑 `eval/run_l2.py` |

---

## 目录

1. [背景与问题](#1-背景与问题)
2. [目标与非目标](#2-目标与非目标)
3. [总体架构](#3-总体架构)
4. [语义层数据模型](#4-语义层数据模型)
5. [DSL 契约](#5-dsl-契约)
6. [编译器设计](#6-编译器设计)
7. [Agent 设计](#7-agent-设计)
8. [执行层与双路由](#8-执行层与双路由)
9. [评测方案](#9-评测方案)
10. [工程实施](#10-工程实施)
11. [排期](#11-排期)
12. [验收标准](#12-验收标准)
13. [风险与应对](#13-风险与应对)
14. [附录](#14-附录)

---

## 1. 背景与问题

### 1.1 现状

仓库已完成 ODS → DWD → DWS → ADS 四层建模，产出三张 ADS 结果表：

| 表 | 行数 | 内容 |
|---|---|---|
| `taobao_ads.ads_conversion_funnel` | 2 | 双口径转化漏斗（次数 / 用户） |
| `taobao_ads.ads_cart_not_buy` | 4,981,518 | 加购未购名单（反连接） |
| `taobao_ads.ads_daily_conversion` | 9 | 每日转化趋势 |

数据规模：3.41 GB CSV，DWD 约 9891 万行 / 9 个分区，时间范围 2017-11-25 至 2017-12-03。

### 1.2 三个具体痛点

1. **口径散落在 SQL 注释里。** 「购买转化率」在 `ads_conversion_funnel` 里有两种口径（次数 2.26% / 用户 68.12%），差 30 倍。这个知识目前只存在于 SQL 注释和 README，没有机器可读的表达。任何人（包括未来的我）新增一个查询，都可能挑错口径。
2. **每问一个新问题就要写一段 SQL。** 需求方（或业务方）问「11 月 25 日到 12 月 3 日每天各品类的加购率」，从理解口径到写完 SQL 需要十几分钟，而其中真正有信息量的只有三个词：加购率、每天、品类。
3. **业务方问到「语义库 / 问数 Agent」，现有仓库没有可指的东西。** 直接接一个 LLM 写 SQL 的 demo，反而会被追问口径与幻觉，答不好是减分项。

### 1.3 为什么不直接让 LLM 写 SQL

这是本方案的核心判断。自由生成 SQL 有四个失败模式，且都不是靠换更大的模型能解决的：

| 失败模式 | 表现 | 根因 |
|---|---|---|
| **口径漂移** | 同一句「转化率」，两次生成可能一个走次数口径、一个走用户口径 | 口径没有唯一表示 |
| **join 幻觉** | 编造不存在的列、把 `dws_item_summary.user_cnt` 跨商品相加 | 模型不知道每张表的粒度 |
| **不可审计** | 生成的 SQL 无法 diff、无法回归、无法追责 | SQL 是自由文本 |
| **不可回归** | 每次改 prompt 都要重跑，无法判断是变好还是变坏 | 没有固定输入 |

**结论：模型只负责把自然语言翻译成受限 DSL，SQL 由语义层用模板确定性编译。** 把不确定性关进槽位填充，把正确性还给代码。

---

## 2. 目标与非目标

### 2.1 目标

| 编号 | 目标 | 可验证的判据 |
|---|---|---|
| G1 | 指标与维度有唯一、机器可读、带版本的定义 | `metrics.yml` 覆盖 ≥ 18 个指标，启动时 schema 校验通过 |
| G2 | DSL → SQL 的编译是确定性的 | 同一 DSL 编译结果字节一致；快照测试进 CI |
| G3 | 自然语言 → DSL 的正确率可度量 | 60 条问题集，DSL 首次合法率 ≥ 90% |
| G4 | 结果可核对 | 与人工黄金 SQL 比对，数值一致率 ≥ 95% |
| G5 | 该反问时反问、该拒答时拒答 | 澄清命中率 ≥ 80%，拒答正确率 ≥ 90% |

### 2.2 非目标（明确不做，避免范围膨胀）

- ✗ 不做通用 BI 平台 / 拖拽式报表
- ✗ 不做多轮对话式深度分析（第一版单轮 + 一次澄清）
- ✗ 不做行列级权限体系（只在 DSL 层预留 `row_filter` 扩展位）
- ✗ 不做 Text2SQL 端到端训练 / 微调
- ✗ 不追求接入 Trino 集群级能力（单机够用即可）

---

## 3. 总体架构

```
                     ┌─────────────────────────────┐
   自然语言问题  ───► │  ① 检索：指标卡片 top-k      │
                     │     (同义词 + BM25)          │
                     └──────────┬──────────────────┘
                                ▼
                     ┌─────────────────────────────┐
                     │  ② LLM 填槽 (结构化输出)     │
                     │     只产出 DSL / 澄清 / 拒答 │
                     └──────────┬──────────────────┘
                                ▼
                     ┌─────────────────────────────┐
                     │  ③ 校验：JSON Schema +       │
                     │     语义规则（维度可用性等）  │
                     └──────────┬──────────────────┘
                        失败 ──► │ 结构化错误回喂，≤2 次
                                ▼
                     ┌─────────────────────────────┐
   semantic/*.yml ──►│  ④ 编译器：DSL → SQL         │
                     │     路由 / 分区裁剪 / 比率同源│
                     └──────────┬──────────────────┘
                                ▼
                     ┌─────────────────────────────┐
                     │  ⑤ 双路由执行                │
                     │     PG 快路径 / Hive 明细     │
                     └──────────┬──────────────────┘
                                ▼
                     ┌─────────────────────────────┐
                     │  ⑥ 解释：结论 + 口径脚注      │
                     └─────────────────────────────┘
```

**分层职责（一句话版）**：

| 层 | 负责 | 绝不负责 |
|---|---|---|
| 语义层 | 什么是「转化率」、能按什么维度看 | 不执行、不调模型 |
| 编译器 | DSL → SQL，确定性 | 不猜意图 |
| Agent | 把中文变成 DSL 或反问 | **绝不拼 SQL 字符串** |
| 执行层 | 跑 SQL、返回结果与耗时 | 不理解口径 |

---

## 4. 语义层数据模型

三个 YAML 文件，git 管理，启动时加载并校验。

### 4.1 `semantic/datasets.yml`

```yaml
- name: dwd_user_behavior
  label: 用户行为明细
  table: taobao_dwd.dwd_user_behavior
  grain: [user_id, item_id, behavior_type, ts]
  partition: {column: dt, format: "yyyy-MM-dd"}
  time_column: dt
  dedup_keys: [user_id]
  dimensions: [dt, hour, behavior_type, category_id, user_tier]
  cost: high            # 98.9M 行全表扫描，20–40s
  engine: hive

- name: dws_user_summary
  label: 用户汇总
  table: taobao_dws.dws_user_summary
  grain: [user_id]
  dimensions: [user_tier]
  joins:                # 星型 join 路径：声明式，模型不可见、不可改
    - dimension: user_tier
      table: taobao_dws.dim_user_tier
      on: [user_id]
      type: left
  cost: medium          # 988K 行
  engine: pg

- name: dws_item_summary
  label: 商品汇总
  table: taobao_dws.dws_item_summary
  grain: [item_id]
  dimensions: [category_id, item_tier]
  joins:
    - dimension: item_tier
      table: taobao_dws.dim_item_tier
      on: [item_id]
      type: left
  cost: medium          # 4.14M 行
  engine: pg
```

> `cost` 与 `engine` 不是装饰：编译器靠它决定走 PG 快路径还是 Hive，并在结果里向用户披露耗时量级。
>
> `joins` 也不是装饰：`user_tier` / `item_tier` 来自独立的 DIM 表，**join 路径在这里一次性声明，编译器按需拼进去**。查询者无法指定 join，也就无法把它拼错。

### 4.2 `semantic/dimensions.yml`

| key | 中文名 | 来源列 | 类型 | 可用 dataset |
|---|---|---|---|---|
| `dt` | 日期 | `dt` | date(9) | 全部 |
| `hour` | 小时 | `hour` | int(0-23) | dwd |
| `behavior_type` | 行为类型 | `behavior_type` | enum(pv/cart/fav/buy) | dwd |
| `category_id` | 商品类目 | `category_id` | int | dwd / dws_item |
| `user_tier` | 用户活跃分层 | `dim_user_tier.user_tier` | enum(low/mid/high) | dwd / dws_user（经 join） |
| `item_tier` | 商品成交分层 | `dim_item_tier.item_tier` | enum(none/low/mid/high) | dws_item（经 join） |

**新增物化维表**（本方案唯一需要动的建模部分，2 张，成本很低）：

```sql
-- taobao_dws.dim_user_tier：按浏览次数分位分档
CREATE TABLE taobao_dws.dim_user_tier STORED AS ORC AS
SELECT user_id,
       CASE WHEN pv_cnt >= 50 THEN 'high'
            WHEN pv_cnt >= 10 THEN 'mid'
            ELSE 'low' END AS user_tier
FROM taobao_dws.dws_user_summary;

-- taobao_dws.dim_item_tier：按成交次数分档
CREATE TABLE taobao_dws.dim_item_tier STORED AS ORC AS
SELECT item_id,
       CASE WHEN buy_cnt >= 10 THEN 'high'
            WHEN buy_cnt >= 1  THEN 'mid'
            ELSE 'none' END AS item_tier
FROM taobao_dws.dws_item_summary;
```

> 分档阈值写死在 SQL 里是刻意的：阈值本身就是口径，必须和指标一起版本化，不能让模型自己编「高活跃 = 大于多少」。

### 4.3 `semantic/metrics.yml`

20 个指标（18 原子 + 2 派生）。字段规范：

```yaml
- name: buy_pv_rate
  label: 浏览购买转化率
  synonyms: [转化率, 购买转化率, 下单率, 成交率]
  definition: 购买用户数 / 浏览用户数（用户去重口径）
  dataset: dwd_user_behavior
  dedup: user
  kind: ratio            # additive | distinct | ratio | derived
  formula:
    numerator:   "COUNT(DISTINCT CASE WHEN behavior_type='buy' THEN user_id END)"
    denominator: "COUNT(DISTINCT CASE WHEN behavior_type='pv'  THEN user_id END)"
  dimensions: [dt, category_id, user_tier]
  unit: percent
  cost: high
  sample_questions:
    - 整体转化率是多少
    - 每天的购买转化率
  version: 1
  owner: jipeijun
```

**首批指标清单**：

| 分组 | 指标 key | 中文名 | 口径要点 | dataset | 成本 |
|---|---|---|---|---|---|
| 规模 | `pv_cnt` | 浏览次数 | 事件计数 | dwd | 高 |
| 规模 | `cart_cnt` | 加购次数 | 事件计数 | dwd | 高 |
| 规模 | `fav_cnt` | 收藏次数 | 事件计数 | dwd | 高 |
| 规模 | `buy_cnt` | 购买次数 | 事件计数 | dwd | 高 |
| 规模 | `active_uv` | 活跃用户数 | `COUNT(DISTINCT user_id)` | dwd | 高 |
| 转化 | `pv_to_cart_rate` | 浏览加购转化率 | 用户去重口径 | dwd | 高 |
| 转化 | `cart_to_buy_rate` | 加购购买转化率 | 用户去重口径 | dwd | 高 |
| 转化 | `buy_pv_rate` | 浏览购买转化率 | 用户去重口径（68.12%） | dwd | 高 |
| 转化 | `category_conv_rate` | 类目转化率 | **次数口径**，`SUM(buy)/SUM(pv)` | dws_item | 中 |
| 转化 | `behavior_share` | 行为结构占比 | 各类型 / 总行为数 | dwd | 高 |
| 用户 | `buy_uv` | 购买用户数 | `COUNT(DISTINCT)` | dwd | 高 |
| 用户 | `repurchase_uv` | 复购用户数 | 人均 `buy_cnt >= 2` | dwd | 高 |
| 用户 | `cart_not_buy_uv` | 加购未购用户数 | 读 ADS 结果表 | ads | 低 |
| 用户 | `retention_d1` | 次日留存率 | 当日活跃 ∩ 次日活跃 | dwd | 高 |
| 强度 | `pv_per_user` | 人均浏览次数 | `pv_cnt / active_uv` | dwd | 高 |
| 强度 | `active_days_avg` | 人均活跃天数 | `AVG(active_days)` | dws_user | 低 |
| 强度 | `item_touch_avg` | 人均互动商品数 | `AVG(item_cnt)` | dws_user | 低 |
| 强度 | `category_penetration` | 类目渗透率 | 类目浏览用户 / 活跃用户 | dwd | 高 |
| 派生 | `day_over_day` | 日环比 | 当期 / 上期 − 1 | 依赖上游 | — |
| 派生 | `ma3` | 3 日移动均值 | 9 天数据，不用 7 日 | 依赖上游 | — |

### 4.4 版本与变更治理

- 每个指标带 `version` + `owner`，口径变化必须升版本并在 `semantic/CHANGELOG.md` 记一行。
- 查询结果返回时携带所用指标的 `version`，写进响应的 `meta`，做到**可追溯**。
- 启动时执行一致性校验，任一条失败则拒绝启动：

| 校验 | 规则 |
|---|---|
| C1 | 每个指标的 `dataset` 存在 |
| C2 | 指标声明的 `dimensions` ⊆ 该 dataset 的 `dimensions` |
| C3 | `ratio` 类指标的分子分母表达式必须引用同一 `dataset` |
| C4 | `formula` 里的列名必须能在 dataset 的 `columns` 里找到 |
| C5 | `synonyms` 全局唯一（冲突则强制走澄清，不许猜） |

---

## 5. DSL 契约

### 5.1 查询 DSL

```json
{
  "metrics": ["buy_pv_rate"],
  "dimensions": ["dt"],
  "filters": [
    {"field": "category_id", "op": "in", "value": [50008164, 50019382]}
  ],
  "time_range": {"start": "2017-11-25", "end": "2017-12-03"},
  "order_by": [{"field": "dt", "dir": "asc"}],
  "limit": 100
}
```

| 字段 | 必填 | 约束 |
|---|---|---|
| `metrics` | 是 | 1–5 个，必须在指标白名单内 |
| `dimensions` | 否 | 0–2 个，且必须对该指标可用 |
| `filters` | 否 | `op ∈ {=, in, >=, <=, between}`，枚举字段值必须合法 |
| `time_range` | 否 | 缺省时默认最近 7 天，并在响应 `assumptions` 中披露 |
| `order_by` | 否 | 只能按维度或指标排序 |
| `limit` | 否 | ≤ 1000 |

**为什么不支持 join 字段**：join 路径是数据集声明的一部分（`datasets.yml` 的星型关系），不是查询参数。让查询者指定 join，等于把口径错误外包给用户。

### 5.2 Agent 输出契约（三态 tagged union）

```jsonc
// ① 可答
{"type": "query", "dsl": {...}, "assumptions": ["时间范围未指定，默认最近 7 天"]}

// ② 需澄清
{"type": "clarify",
 "question": "转化率有两种口径，你要哪一个？",
 "options": ["次数口径：购买次数/浏览次数（2.26%）",
             "用户口径：购买用户/浏览用户（68.12%）"],
 "reason": "E_AMBIGUOUS_METRIC"}

// ③ 拒答
{"type": "refuse",
 "reason": "数据集无价格字段，无法计算毛利率",
 "closest": ["category_conv_rate", "behavior_share"]}
```

**这个三态设计是防幻觉的第一道闸**：模型没有「随便写点什么」的出口，只能从三个合法动作里选一个。

### 5.3 错误码

| 码 | 含义 | 处置 |
|---|---|---|
| `E_UNKNOWN_METRIC` | 指标不在白名单 | 修复重试（附 top-3 候选） |
| `E_UNKNOWN_DIMENSION` | 维度不在白名单 | 修复重试（附可用维度） |
| `E_DIM_NOT_AVAILABLE` | 维度对该指标不可用 | 修复重试 |
| `E_INVALID_ENUM` | 枚举值非法 | 修复重试 |
| `E_TIME_OUT_OF_RANGE` | 超出 2017-11-25～12-03 | 修复重试 + 披露范围 |
| `E_MULTI_DATASET` | 一组指标落在不同数据集，无法一次查询 | 拒绝 + 建议拆成两次提问 |
| `E_RATIO_CROSS_DATASET` | 比率分子分母跨数据集 | 直接拒绝（编译期不可能修复） |
| `E_LIMIT_EXCEEDED` | limit > 1000 | 修复重试 |
| `E_AMBIGUOUS_METRIC` | 同义词命中多个指标 | **澄清** |
| `E_TIME_MISSING` | 时间未指定且有歧义 | 澄清或默认 + 披露 |

---

## 6. 编译器设计

### 6.1 五条硬规则

| # | 规则 | 违反后果 |
|---|---|---|
| R1 | 指标 → dataset 由字典决定，编译器不做自动 join 发现 | 选错粒度，数值错 |
| R2 | 自动分区裁剪：注入 `dt BETWEEN start AND end` | 全表扫描，40s |
| R3 | 比率类：分子分母同 dataset、同粒度，先聚合再相除 | 比率被权重扭曲 |
| R4 | 去重类指标必须显式声明 `dedup: user`，不可从 DWS 近似 | 把每商品 UV 相加 |
| R5 | 枚举与维度可用性在编译期校验 | 运行时才报错 |

### 6.2 生成算法

```python
def compile(dsl: QueryDSL) -> CompiledSQL:
    metrics = [load_metric(m) for m in dsl.metrics]
    # R1: 单 dataset 原则 —— 一组指标必须落在同一 dataset
    datasets = {m.dataset for m in metrics}
    if len(datasets) > 1:
        raise CompileError("E_MULTI_DATASET", datasets)

    ds = load_dataset(datasets.pop())
    dims = resolve_dimensions(dsl.dimensions, ds)      # R5
    where = build_where(dsl, ds)                       # R2 + 过滤条件
    selects = [metric_sql(m) for m in metrics]         # R3 / R4
    sql = render_template(ds, dims, selects, where, dsl.order_by, dsl.limit)
    return CompiledSQL(sql=sql, engine=ds.engine, estimated_cost=ds.cost)
```

### 6.3 三个必须写进代码注释的口径陷阱

**陷阱 1：`dws_item_summary.user_cnt` 不可跨商品相加。**

`user_cnt` 是每个商品的去重用户数。类目下把 10 个商品的 `user_cnt` 相加，同一个用户会被算 10 次。

- 「类目转化率（**次数口径**）」= `SUM(buy_cnt)/SUM(pv_cnt) GROUP BY category_id` → 从 `dws_item_summary` 算，**正确**。
- 「类目转化率（**用户口径**）」→ 必须回 `dwd` 做 `COUNT(DISTINCT user_id)`，**DWS 拿不到**。

语义层的做法：两个指标分开定义，各自绑定正确的 dataset。模型永远没有机会选错。

**陷阱 2：次数口径与用户口径差 30 倍。**

现有 README 已记录：次数口径 2.26%，用户口径 68.12%。这不是 bug，是两个合法口径。DSL 里必须由 `metrics` 显式指定，不能靠「转化率」三个字推断——这正是 `E_AMBIGUOUS_METRIC` 澄清场景的来源。

**陷阱 3：默认时间范围必须在结果里披露。**

用户问「转化率是多少」而没给时间，默认近 7 天可以，但必须写进 `assumptions` 并显示在结果卡片上。**静默假设是最坏的一种正确**。

### 6.4 快照测试

```
semantic/tests/
├── cases/                       # 每条：dsl.json + expected.sql
│   ├── simple_rate.json
│   ├── by_category.json
│   ├── dod_of_rate.json
│   └── ...
└── test_compiler.py             # pytest：编译结果与快照逐字节比对
```

要求：每个指标至少 2 条用例（基础 + 带维度/过滤），且至少被一条用例引用（指标覆盖率 100%）。

```bash
pytest semantic/tests -q          # 改字典必跑，CI 必过
pytest semantic/tests --update    # 确认口径变更后更新快照（需 review diff）
```

---

## 7. Agent 设计

### 7.1 检索：先把候选缩到 8 个

指标一多，把所有定义塞进 prompt 既贵又噪。做法：

1. 问题分词（中文用 jieba）+ 同义词归一（`转化率` → `buy_pv_rate` / `pv_to_cart_rate` / `cart_to_buy_rate`）
2. **BM25** 在「指标 label + synonyms + sample_questions」上检索，取 top-8 指标卡片
3. 维度按 dataset 与问题中的名词匹配，取 top-4
4. 命中多个指标同义词 → 标记 `E_AMBIGUOUS_METRIC`，走澄清而不进生成

> 20 个指标时 BM25 完全够用。扩到 500 个指标时才需要 embedding + 分层字典（见 Q9）。

### 7.2 生成：结构化输出，不写 SQL

- 模型：DeepSeek（成本可控），`response_format=json_object` + pydantic 二次校验
- prompt 只含：系统角色、DSL 的 JSON Schema、top-8 指标卡片、4 条 few-shot（含 1 条澄清、1 条拒答）
- prompt 明确禁止输出 SQL，也**不把 SQL 模板放进 prompt**——模型看不到 SQL，就编不出 SQL

### 7.3 校验与修复循环（状态机）

```
generate ──► validate ──ok──► compile ──► execute
                │                │
                │ fail(可修复)    │ CompileError(不可修复)
                ▼                ▼
           repair(≤2次)        refuse / clarify
                │
           仍失败 ──► 兜底：返回 top-3 候选指标 + 建议问法
```

- **可修复错误**（表 5.3 前 5 条 + limit）：把「错误码 + 错在哪 + 合法取值」拼成一条结构化消息回喂，最多 2 次
- **不可修复错误**（`E_MULTI_DATASET` / `E_RATIO_CROSS_DATASET`）：直接拒绝并建议拆成两次提问，不浪费重试
- 每次重试记录 token 与耗时，进评测报告

### 7.4 澄清策略：三类歧义，三种问法

| 类型 | 触发 | 问法 |
|---|---|---|
| 指标歧义 | 同义词命中多个 | 给 2–3 个选项 + 各自数值量级（帮用户判断） |
| 时间歧义 | 未给时间且无默认共识 | 「你要最近 7 天，还是指定日期区间？」 |
| 维度歧义 | 维度对该指标不可用 | 说明不可用 + 给可用维度 |

**能澄清就不要猜。** 猜错比问一句的代价大得多，而且评测的 C 类题目就是考这个。

### 7.5 解释与口径脚注

结果卡片固定包含：

```
结论：2017-11-25 至 12-03，浏览购买转化率（用户口径）为 68.12%
口径：购买用户数 / 浏览用户数，user_id 去重；数据范围 9 天
SQL ：（折叠展示，可复制）
耗时：1.2s ｜ engine: pg ｜ metric_version: 1
假设：时间范围由问题给定，无默认值
```

**把 SQL 摆在明面上**是信任设计：可解释、可纠错、可审计。用户能直接看到语义层干了什么。

---

## 8. 执行层与双路由

### 8.1 三层路由

| 路由 | 引擎 | 覆盖 | 延迟 |
|---|---|---|---|
| A 快路径 | PostgreSQL（同步来的 DWS/ADS） | 988K 用户 + 414 万商品 + ADS | 10–100ms |
| B 明细 | Hive on Tez | DWD 9891 万行 | 20–40s |
| C 可选 | Trino（同一 metastore） | 明细交互查询 | 3–8s |

**第一版只做 A + B，不做 Trino。** 理由：16GB 内存下再加一个 JVM 服务（约 2.5GB）会挤压 Hive，而 A 路径已经能覆盖 80% 的提问（指标类问题都能从 DWS 聚合回答），B 路径只用于真正的明细下钻，慢一点可接受。

### 8.2 同步脚本

`tools/sync_to_pg.py`：把 `dws_user_summary`、`dws_item_summary`、`dim_*_tier`、三张 ADS 表同步到 PostgreSQL。

- 用 `COPY` 批量写入，先写临时表再 `RENAME`，保证查询不读半成品
- 每次同步记录 `sync_batch` + 时间戳，查询结果带 `data_as_of`

> 这一步既是性能手段，也是叙事素材：**「服务层与即席查询分离」是真实数仓的标准做法**，不是 demo 的取巧。

### 8.3 延迟预算

| 环节 | 预算 |
|---|---|
| 检索 | 30ms |
| LLM 生成 | 1.5–3s |
| 校验 + 编译 | 20ms |
| 执行（PG） | 10–100ms |
| 解释生成 | 0.5–1s |
| **端到端 P95（PG 路径）** | **≤ 8s** |

---

## 9. 评测方案

> 读者最可能追问的是「你怎么知道它答对了」。这一章就是答案。

### 9.1 问题集：60 条，四类

| 类 | 条数 | 考察 | 样例 |
|---|---|---|---|
| A 直接可答 | 25 | 单指标 / 趋势 / 排行 | 「每天的浏览次数趋势」<br>「加购次数最多的前 10 个类目」 |
| B 多指标与对比 | 15 | 多指标同表、派生指标 | 「对比 11-25 和 12-02 的转化率」<br>「各行为类型的占比」 |
| C 必须澄清 | 10 | 歧义识别 | 「最近的转化率」（时间）<br>「卖得好的商品」（指标）<br>「转化率是多少」（口径） |
| D 必须拒答 | 10 | 边界意识 | 「预测下个月销量」（无未来数据）<br>「用户年龄分布」（无字段）<br>「毛利率」（无价格） |

每条标注：`question / class / expected_dsl 或 expected_action / gold_sql / gold_result`。

### 9.2 六个指标与目标值

| 指标 | 定义 | 目标 |
|---|---|---|
| DSL 首次合法率 | 第一次生成即通过 schema 的比例 | ≥ 90% |
| 执行成功率 | 编译并跑出结果的比例 | ≥ 95% |
| 数值一致率 | 与黄金 SQL 结果一致（相对误差 ≤ 1e-6） | ≥ 95% |
| 澄清命中率 | C 类中正确反问的比例 | ≥ 80% |
| 拒答正确率 | D 类中正确拒绝的比例 | ≥ 90% |
| P95 端到端延迟 | 见 8.3 | ≤ 8s |

### 9.3 两档评测（关键工程决策）

| 档 | 内容 | 频率 | 成本 |
|---|---|---|---|
| **L1 编译器回归** | 固定 DSL 回放 → SQL 快照 + 结果比对 | 每次 commit，CI | **零 token** |
| **L2 Agent 评测** | 真 LLM 跑 60 条 | 每周 / 每次改 prompt | 约 15 万 token |

L1 保证「口径不会悄悄变」，L2 保证「模型效果可比较」。分开跑，才能既省又准。

### 9.4 报告模板

`eval/report.md` 固定结构：模型与版本 → 六指标对比上次 → 失败案例逐条分析（按错误码归类）→ 结论与下一步。

**失败案例必须逐条写**，这是最有信息量的部分，也是复盘时最好的谈资。

---

## 10. 工程实施

### 10.1 目录结构（全部新增，不动现有目录）

```
semantic/
├── datasets.yml
├── dimensions.yml
├── metrics.yml
├── CHANGELOG.md
├── compiler.py            # DSL → SQL
├── validator.py           # schema + 语义校验
├── registry.py            # 加载/校验/检索指标卡片
└── tests/
    ├── cases/*.json
    └── test_compiler.py

agent/
├── retriever.py           # BM25 指标检索
├── prompt.py
├── llm.py                 # 结构化输出封装
└── pipeline.py            # 三态状态机 + 修复循环

tools/
├── sync_to_pg.py
└── build_dim_tier.sql

eval/
├── questions.jsonl        # 60 条
├── gold_sql/
├── run_l1.py              # 固定 DSL 回放（CI）
├── run_l2.py              # 真 LLM
└── report.md

app/
└── streamlit_app.py
```

### 10.2 依赖

| 包 | 用途 |
|---|---|
| `pyyaml` `pydantic` | 字典加载与校验 |
| `jieba` `rank-bm25` | 中文分词 + 检索 |
| `openai`（或 `httpx`） | DeepSeek 兼容 OpenAI 协议 |
| `psycopg2-binary` `pyhive` | PG / Hive 连接 |
| `streamlit` | 前端 |
| `pytest` `syrupy` | 快照测试 |

### 10.3 与现有环境集成

- **不改** `docker/docker-compose.yml`，新增 `docker/docker-compose.semantic.yml` 用 `profiles: [semantic]` 挂 Streamlit
- LLM Key 走 `.env`；**开工第一步先把它加进 `.gitignore`**（当前仓库尚未包含这一条），README 说明配置方式
- 两个新维表由 `tools/build_dim_tier.sql` 生成，属于 DWS 扩展，跑在现有 Hive 上

### 10.4 内存预算（16GB 约束）

| 组件 | 增量 |
|---|---|
| Streamlit | 0.3GB |
| Agent / 编译器进程 | 0.2GB |
| PostgreSQL 新增数据 | 0.5GB |
| **合计** | **约 1GB**（现有栈约 6GB，余量充足） |

不做 Trino 的决定，本质就是这 2.5GB 的取舍。

---

## 11. 排期

| 天 | 任务 | 交付物 |
|---|---|---|
| D1 | 指标口径梳理（对照现有 SQL/ADS），确定 20 个指标的定义 | `metrics.yml` 草稿 |
| D2 | 三个字典文件 + 一致性校验 + 两张维表 | 字典 v1 + `build_dim_tier.sql` |
| D3 | 编译器骨架（模板渲染、路由、裁剪） | `compiler.py` |
| D4 | 比率/去重/派生三类指标 + 快照测试 | 用例 ≥ 40 条，pytest 全绿 |
| D5 | PG 同步 + 双路由执行 | `sync_to_pg.py`，A 路径跑通 |
| D6 | 检索 + prompt + 结构化输出 | `agent/` 跑通单条 |
| D7 | 修复循环 + 澄清 + 拒答 | 三态闭环 |
| D8 | 60 条问题集 + 黄金 SQL | `eval/questions.jsonl` |
| D9 | Streamlit 页面 + README 增补 | 可演示 |
| D10 | L2 评测 + 报告 + 结项条目 | `eval/report.md` |

**关键路径**：D3–D4（编译器）。字典可以边做边改，编译器不行——它一旦有口径 bug，后面全错。

---

## 12. 验收标准

- [ ] 20 个指标、6 个维度全部有唯一机器可读定义，启动校验通过
- [ ] 40+ 条 DSL 快照测试通过，编译器覆盖率 100% 指标
- [ ] 60 条问题集跑完：DSL 合法率 ≥ 90%，数值一致率 ≥ 95%，澄清命中率 ≥ 80%，拒答正确率 ≥ 90%
- [ ] Streamlit 页面能展示 问题 → DSL（可编辑）→ SQL（折叠）→ 结果 + 口径脚注
- [ ] `eval/report.md` 含失败案例逐条分析
- [ ] README 增补一章，含架构图与一次完整演示截图
- [ ] 现有四层数仓的脚本与产物零改动（diff 可验证）

---

## 13. 风险与应对

| 风险 | 表现 | 应对 |
|---|---|---|
| Hive 太慢，演示卡顿 | 明细查询 40s | 指标绑定 DWS/ADS 走 PG；UI 显示 engine 与耗时，把慢变成「可解释」 |
| 指标只有 20 个，被质疑是玩具 | 「真实公司上千个指标」 | 以治理机制作答：版本化、口径冲突检测、评测集、白名单。数量是规模问题，机制是能力问题 |
| 模型输出不稳定 | 同一问题两次结果不同 | 结构化输出 + 白名单 + 快照测试；L2 评测锁定模型版本 |
| 澄清过多，体验差 | 简单问题也被反问 | 只有真正歧义才澄清；默认值场景（时间）给默认 + 披露 |
| 黄金 SQL 本身写错 | 评测结果不可信 | 黄金 SQL 由人工复核，且必须能通过现有 `verify_all.sql` 的一致性检查 |
| 内存不足 | 容器被 OOM kill | 只加 1GB；不做 Trino；Streamlit 与 Hive 不同时压测 |

---

## 14. 附录

### A. 现有资源复用清单

| 复用 | 用途 |
|---|---|
| `taobao_dwd.dwd_user_behavior` | 用户去重口径、留存、明细下钻 |
| `taobao_dws.dws_user_summary` | 人均活跃天数、互动商品数 |
| `taobao_dws.dws_item_summary` | 类目次数口径转化率、商品排行 |
| `taobao_ads.ads_cart_not_buy` | 加购未购用户数（直接读，不重算） |
| `sql/verify/verify_all.sql` | 黄金 SQL 的一致性基线 |

### B. 端口

| 服务 | 端口 | 状态 |
|---|---|---|
| Streamlit | 8501 | 新增 |
| PostgreSQL | 5432 | 复用（容器内） |
| HiveServer2 | 10000 | 复用 |
| HDFS UI | 9870 | 复用 |

### C. 待决事项

| # | 事项 | 建议 | 需要谁定 |
|---|---|---|---|
| 1 | 是否引入 Trino | 第一版不引入 | 我 |
| 2 | 用户分层阈值（50 / 10） | 先用分位数定，跑通后复核 | 我 |
| 3 | LLM 用 DeepSeek 还是本地模型 | DeepSeek（16GB 内存不划算跑本地） | 我 |
| 4 | 是否开源仓库 | 保持公开，Key 走 .env | 我 |

### D. 参考

- 指标定义方法：阿里 OneData（原子指标 + 时间周期 + 修饰词 = 派生指标）
- 语义层产品参考：dbt Semantic Layer、Cube.js、阿里 Quick BI 智能问数
