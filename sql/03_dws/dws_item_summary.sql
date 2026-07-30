-- =====================================================================
-- DWS 层 (Data Warehouse Summary / 汇总层) - 商品维度宽表
-- 作用: 按商品聚合成宽表, 每个商品一行
-- Purpose: aggregate by item into a wide table, one row per item
-- =====================================================================

USE taobao_dws;

DROP TABLE IF EXISTS dws_item_summary;

CREATE TABLE dws_item_summary
STORED AS ORC
TBLPROPERTIES ('orc.compress' = 'SNAPPY')
AS
SELECT
    item_id,
    MAX(category_id) AS category_id,          -- 商品所属类目 / item category
    SUM(CASE WHEN behavior_type = 'pv'   THEN 1 ELSE 0 END) AS pv_cnt,
    SUM(CASE WHEN behavior_type = 'cart' THEN 1 ELSE 0 END) AS cart_cnt,
    SUM(CASE WHEN behavior_type = 'fav'  THEN 1 ELSE 0 END) AS fav_cnt,
    SUM(CASE WHEN behavior_type = 'buy'  THEN 1 ELSE 0 END) AS buy_cnt,
    COUNT(DISTINCT user_id) AS user_cnt       -- 互动用户数 / distinct users interacted
FROM taobao_dwd.dwd_user_behavior
GROUP BY item_id;
