# 数据字典 / Data Dictionary

本文档描述数仓各层表结构。/ Table schemas across all warehouse layers.

---

## ODS 层 / ODS Layer

### `taobao_ods.ods_user_behavior`
外部表,贴源。/ External table, raw source.

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | BIGINT | 用户 ID |
| item_id | BIGINT | 商品 ID |
| category_id | BIGINT | 类目 ID |
| behavior_type | STRING | pv/cart/fav/buy |
| ts | BIGINT | 时间戳(秒) |

---

## DWD 层 / DWD Layer

### `taobao_dwd.dwd_user_behavior`
清洗后的明细,按 `dt` 分区,ORC 存储。约 9891 万行,9 个分区。
Cleaned detail, partitioned by `dt`, ORC. ~98.9M rows, 9 partitions.

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | BIGINT | 用户 ID |
| item_id | BIGINT | 商品 ID |
| category_id | BIGINT | 类目 ID |
| behavior_type | STRING | pv/cart/fav/buy |
| ts | BIGINT | 时间戳 |
| behavior_time | STRING | yyyy-MM-dd HH:mm:ss |
| hour | INT | 小时 0-23 |
| **dt** (分区) | STRING | 日期分区 yyyy-MM-dd |

**清洗规则 / cleaning rules**: 过滤空 user_id/item_id、非法 behavior_type、日期范围外记录。

---

## DWS 层 / DWS Layer

### `taobao_dws.dws_user_summary`
用户维度宽表,每用户一行。约 99 万行。/ User grain, ~988K rows.

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | BIGINT | 用户 ID |
| pv_cnt | BIGINT | 浏览次数 |
| cart_cnt | BIGINT | 加购次数 |
| fav_cnt | BIGINT | 收藏次数 |
| buy_cnt | BIGINT | 购买次数 |
| active_days | BIGINT | 活跃天数 |
| item_cnt | BIGINT | 互动商品数 |

### `taobao_dws.dws_item_summary`
商品维度宽表,每商品一行。约 414 万行。/ Item grain, ~4.14M rows.

| 字段 | 类型 | 说明 |
|---|---|---|
| item_id | BIGINT | 商品 ID |
| category_id | BIGINT | 类目 ID |
| pv_cnt | BIGINT | 被浏览次数 |
| cart_cnt | BIGINT | 被加购次数 |
| fav_cnt | BIGINT | 被收藏次数 |
| buy_cnt | BIGINT | 被购买次数 |
| user_cnt | BIGINT | 互动用户数 |

---

## ADS 层 / ADS Layer

### `taobao_ads.ads_conversion_funnel`
转化漏斗,2 行(两种口径)。/ Conversion funnel, 2 rows.

| 字段 | 类型 | 说明 |
|---|---|---|
| funnel_type | STRING | event_count / unique_user |
| pv_cnt / cart_cnt / fav_cnt / buy_cnt | BIGINT | 各环节数值 |
| buy_pv_rate_pct | DECIMAL | 购买/浏览转化率(%) |

### `taobao_ads.ads_cart_not_buy`
加购未购名单。约 498 万行。/ Cart-not-buy list, ~4.98M rows.

| 字段 | 类型 | 说明 |
|---|---|---|
| user_id | BIGINT | 用户 ID |
| item_id | BIGINT | 商品 ID |
| category_id | BIGINT | 类目 ID |

### `taobao_ads.ads_daily_conversion`
每日转化趋势,9 行。/ Daily trend, 9 rows.

| 字段 | 类型 | 说明 |
|---|---|---|
| dt | STRING | 日期 |
| pv_cnt / cart_cnt / fav_cnt / buy_cnt | BIGINT | 当日各行为次数 |
| buy_pv_rate_pct | DECIMAL | 当日转化率(%) |
