-- =====================================================================
-- DWS 层 (Data Warehouse Summary / 汇总层) - 用户维度宽表
-- 作用: 按用户聚合成宽表, 每个用户一行
-- Purpose: aggregate by user into a wide table, one row per user
-- 核心技术: CTAS 建表, 条件聚合 sum(case when), 去重计数 count(distinct)
-- =====================================================================

CREATE DATABASE IF NOT EXISTS taobao_dws;
USE taobao_dws;

DROP TABLE IF EXISTS dws_user_summary;

-- CTAS: 建表同时灌入聚合结果 / create table + load in one step
CREATE TABLE dws_user_summary
STORED AS ORC
TBLPROPERTIES ('orc.compress' = 'SNAPPY')
AS
SELECT
    user_id,
    -- 条件聚合: 一次扫描算出4种行为次数 / conditional aggregation in one scan
    SUM(CASE WHEN behavior_type = 'pv'   THEN 1 ELSE 0 END) AS pv_cnt,
    SUM(CASE WHEN behavior_type = 'cart' THEN 1 ELSE 0 END) AS cart_cnt,
    SUM(CASE WHEN behavior_type = 'fav'  THEN 1 ELSE 0 END) AS fav_cnt,
    SUM(CASE WHEN behavior_type = 'buy'  THEN 1 ELSE 0 END) AS buy_cnt,
    COUNT(DISTINCT dt)      AS active_days,   -- 活跃天数 / active days
    COUNT(DISTINCT item_id) AS item_cnt       -- 互动商品数 / distinct items interacted
FROM taobao_dwd.dwd_user_behavior
GROUP BY user_id;
