-- =====================================================================
-- ADS 层 (Application Data Store / 应用层) - 每日转化趋势
-- 作用: 按天统计各行为次数与购买转化率, 观察大促效应
-- Purpose: daily behavior counts + conversion rate, spot promotion effect
-- =====================================================================

USE taobao_ads;

DROP TABLE IF EXISTS ads_daily_conversion;

CREATE TABLE ads_daily_conversion
STORED AS ORC
AS
SELECT
    dt,
    SUM(CASE WHEN behavior_type = 'pv'   THEN 1 ELSE 0 END) AS pv_cnt,
    SUM(CASE WHEN behavior_type = 'cart' THEN 1 ELSE 0 END) AS cart_cnt,
    SUM(CASE WHEN behavior_type = 'fav'  THEN 1 ELSE 0 END) AS fav_cnt,
    SUM(CASE WHEN behavior_type = 'buy'  THEN 1 ELSE 0 END) AS buy_cnt,
    ROUND(SUM(CASE WHEN behavior_type = 'buy' THEN 1 ELSE 0 END) * 100.0 /
          SUM(CASE WHEN behavior_type = 'pv'  THEN 1 ELSE 0 END), 4) AS buy_pv_rate_pct
FROM taobao_dwd.dwd_user_behavior
GROUP BY dt
ORDER BY dt;
