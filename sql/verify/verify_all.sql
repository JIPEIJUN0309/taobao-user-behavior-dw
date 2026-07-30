-- =====================================================================
-- 数据验证脚本 / Data verification queries
-- 用于确认各层数据正确性 / verify data correctness across layers
-- =====================================================================

-- ---------- DWD 验证 / DWD checks ----------
USE taobao_dwd;

-- 查看所有分区 (应有9个) / show partitions (expect 9)
SHOW PARTITIONS dwd_user_behavior;

-- 每个分区行数 / row count per partition
SELECT dt, COUNT(*) AS cnt
FROM dwd_user_behavior
GROUP BY dt
ORDER BY dt;

-- 行为类型分布 / behavior type distribution
SELECT behavior_type, COUNT(*) AS cnt
FROM dwd_user_behavior
GROUP BY behavior_type;

-- ---------- DWS 验证 / DWS checks ----------
USE taobao_dws;

SELECT COUNT(*) AS user_count FROM dws_user_summary;   -- expect ~987,984
SELECT COUNT(*) AS item_count FROM dws_item_summary;   -- expect ~4,142,583

-- Top10 购买用户 / top buyers
SELECT user_id, pv_cnt, cart_cnt, fav_cnt, buy_cnt, active_days
FROM dws_user_summary
ORDER BY buy_cnt DESC
LIMIT 10;

-- Top10 热门商品 / top items
SELECT item_id, category_id, pv_cnt, buy_cnt, user_cnt
FROM dws_item_summary
ORDER BY buy_cnt DESC
LIMIT 10;

-- ---------- ADS 验证 / ADS checks ----------
USE taobao_ads;

SELECT * FROM ads_conversion_funnel;
SELECT * FROM ads_daily_conversion;
SELECT COUNT(*) AS cart_not_buy_count FROM ads_cart_not_buy;
SELECT * FROM ads_cart_not_buy LIMIT 20;
