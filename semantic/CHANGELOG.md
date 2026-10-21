# 口径变更记录

规则：只要指标的口径（分子/分母/去重方式/默认范围/分层阈值）发生变化，就必须在这里记一行，并把该指标的 `version` 加一。

## v1.0.0 · 2026-10-21

首批 20 个指标（18 原子 + 2 派生）、6 个维度、3 个数据集上线。

- 规模：`pv_cnt` `cart_cnt` `fav_cnt` `buy_cnt` `behavior_cnt`
- 用户：`active_uv` `buy_uv` `repurchase_uv`
- 强度：`pv_per_user` `purchase_frequency` `active_days_avg` `item_touch_avg`
- 转化：`pv_to_cart_rate` `cart_to_buy_rate` `buy_pv_rate` `category_conv_rate` `item_cart_rate`
- 留存：`retention_d1`
- 派生：`behavior_share`（占比）、`day_over_day`（环比）

口径决定：

1. 「转化率」类默认 **用户去重口径**；`category_conv_rate` / `item_cart_rate` 因底层是商品汇总表，只能是 **次数口径**，已在 `definition` 里写明。
2. `repurchase_uv` 定义为「全期购买次数 >= 2 的用户数」，绑定 `dws_user_summary`（用户粒度），不是事件粒度。
3. `retention_d1` 只支持按 `dt` 查看；不传维度时默认按天展示并在 assumptions 里披露。
4. `day_over_day` 的基指标是 `buy_pv_rate`，按 `dt` 升序取 LAG，并自动回溯前一日数据。

验证状态：

- 快照 28 份（8 条手工用例 + 20 个指标各一条）
- 真数据核对 10/10 通过（`python tools/verify_metrics.py`）
- 与 README 已公布数字一致：pv 88,596,886 / buy 1,998,944 / UV 987,984 / 购买用户 670,370 / 转化率 68.12%
