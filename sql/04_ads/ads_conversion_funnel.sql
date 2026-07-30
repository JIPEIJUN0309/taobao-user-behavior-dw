-- =====================================================================
-- ADS 层 (Application Data Store / 应用层) - 转化漏斗
-- 作用: 计算 pv->cart->fav->buy 转化漏斗, 提供两种口径
-- Purpose: conversion funnel with two definitions
--   event_count: 事件数口径 (行为总次数) / count of events
--   unique_user: 独立用户口径 (去重用户数) / distinct users
-- =====================================================================

CREATE DATABASE IF NOT EXISTS taobao_ads;
USE taobao_ads;

DROP TABLE IF EXISTS ads_conversion_funnel;

CREATE TABLE ads_conversion_funnel
STORED AS ORC
AS
-- 口径1: 事件数 / event count
SELECT
    'event_count' AS funnel_type,
    SUM(CASE WHEN behavior_type = 'pv'   THEN 1 ELSE 0 END) AS pv_cnt,
    SUM(CASE WHEN behavior_type = 'cart' THEN 1 ELSE 0 END) AS cart_cnt,
    SUM(CASE WHEN behavior_type = 'fav'  THEN 1 ELSE 0 END) AS fav_cnt,
    SUM(CASE WHEN behavior_type = 'buy'  THEN 1 ELSE 0 END) AS buy_cnt,
    ROUND(SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END) * 100.0 /
          SUM(CASE WHEN behavior_type = 'pv'  THEN 1 ELSE 0 END), 4) AS buy_pv_rate_pct
FROM taobao_dwd.dwd_user_behavior

UNION ALL

-- 口径2: 独立用户 / unique user
SELECT
    'unique_user' AS funnel_type,
    COUNT(DISTINCT CASE WHEN behavior_type = 'pv'   THEN user_id END) AS pv_cnt,
    COUNT(DISTINCT CASE WHEN behavior_type = 'cart' THEN user_id END) AS cart_cnt,
    COUNT(DISTINCT CASE WHEN behavior_type = 'fav'  THEN user_id END) AS fav_cnt,
    COUNT(DISTINCT CASE WHEN behavior_type = 'buy'  THEN user_id END) AS buy_cnt,
    ROUND(COUNT(DISTINCT CASE WHEN behavior_type = 'buy' THEN user_id END) * 100.0 /
          COUNT(DISTINCT CASE WHEN behavior_type = 'pv'  THEN user_id END), 4) AS buy_pv_rate_pct
FROM taobao_dwd.dwd_user_behavior;
