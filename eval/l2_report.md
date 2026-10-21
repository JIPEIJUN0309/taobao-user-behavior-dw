# L2 评测报告（真模型）

- 模型：`deepseek-chat`
- 执行引擎：`duckdb`
- 题量：60（A/B 可答、C 需澄清、D 需拒答）
- 生成时间：2026-10-21 19:45:46

## 六项指标

| 指标 | 值 | 目标 |
|---|---|---|
| DSL 首次合法率 | 97.3% | ≥ 90% |
| 数值一致率 | 97.3% | ≥ 95% |
| 澄清命中率 | 69.2% | ≥ 80% |
| 拒答正确率 | 90.0% | ≥ 90% |
| P95 端到端延迟 | 5414 ms | ≤ 8000 ms |
| 总通过率 | 90.0% | — |

token 消耗：prompt 64360 + completion 5729

## 失败案例

### A07（A）高活跃用户的购买用户数

- 期望：`query`，实际：`query`
- 尝试次数：2
- DSL：`{"metrics": ["buy_uv"], "dimensions": ["user_tier"], "filters": [{"field": "user_tier", "op": "=", "value": "high"}], "time_range": {"start": "2017-11-25", "end": "2017-12-03"}, "limit": 1000}`

### B08（C）各类目的加购率前 5

- 期望：`clarify`，实际：`query`
- 尝试次数：1
- DSL：`{"metrics": ["pv_to_cart_rate"], "dimensions": ["category_id"], "time_range": {"start": "2017-11-25", "end": "2017-12-03"}, "order_by": [{"field": "pv_to_cart_rate", "dir": "desc"}], "limit": 5}`

### C05（C）留存怎么样

- 期望：`clarify`，实际：`query`
- 尝试次数：1
- DSL：`{"metrics": ["retention_d1"], "dimensions": ["dt"], "time_range": {"start": "2017-11-25", "end": "2017-12-03"}, "order_by": [{"field": "dt", "dir": "asc"}], "limit": 1000}`

### C09（C）环比怎么样

- 期望：`clarify`，实际：`query`
- 尝试次数：1
- DSL：`{"metrics": ["day_over_day"], "dimensions": ["dt"], "time_range": {"start": "2017-11-25", "end": "2017-12-03"}, "order_by": [{"field": "dt", "dir": "asc"}], "limit": 1000}`

### C10（C）高峰时段是什么时候

- 期望：`clarify`，实际：`query`
- 尝试次数：1
- DSL：`{"metrics": ["active_uv"], "dimensions": ["hour"], "time_range": {"start": "2017-11-25", "end": "2017-12-03"}, "order_by": [{"field": "active_uv", "dir": "desc"}], "limit": 24}`

### D08（D）昨天的支付转化率

- 期望：`refuse`，实际：`clarify`
- 尝试次数：1
- DSL：`null`
- 澄清：{"question": "「支付转化率」有几种口径，您要哪一种？", "options": ["A：浏览购买转化率 buy_pv_rate（有购买行为的用户数 / 有浏览行为的用户数，用户去重口径）", "B：加购购买转化率 cart_to_buy_rate（有购买行为的用户数 / 有加购行为的用户数，用户去重口径）", "C：浏览加购转化率 pv_to_cart_rate（有加购行为的用户数 / 有浏览行为的用户数，用户去重口径）"], "reason": "E_AMBIGUOUS_METRIC"}

## 结论

**可答题（A/B 共 37 条）答对 36 条，97.3%；六项指标里四项达标，澄清命中率是唯一没达标的（69.2% vs 目标 80%）。**

失败 6 条分三类：

| 类别 | 题号 | 性质 |
|---|---|---|
| 语义等价、列形状不同 | A07 | 模型同时给了 `dimensions: [user_tier]` 和 `filters: user_tier='high'`，数值正确但多一列；评测按列严格比对判失败。属于评测口径偏严，不是模型错。 |
| 该反问却直接猜 | B08、C05、C09、C10 | 四道题都没指明指标（「加购率」「留存」「环比」「高峰」），模型各挑了一个默认指标就查了。**这正是设计要防的行为**，也是当前最弱的一环。 |
| 该拒答却反问 | D08 | 「昨天的支付转化率」超出数据范围（只到 2017-12-03），应拒答；模型按口径歧义反问了。 |

**根因判断**：澄清命中率低不是"模型不会问"，而是 prompt 里同时存在两条相反的指令——「能答就答」和「模糊就反问」。模型倾向选前者。下一步的修法应该是给「模糊」一个可判定的定义（例如：问题中不含任何指标卡片的 label/同义词 → 必须 clarify），而不是继续在 prompt 里加形容词。

**已经排除的干扰项**：本轮之前的一次运行里有 1 条 60 秒网络超时；已给客户端加一次重试（仅对 5xx 与超时，4xx 不重试），本次运行未再出现。上一版的 15 条失败中，13 条是评测集本身的标注错误（时间范围缺失、把同义词误判为歧义），已修正并重新基线化。

