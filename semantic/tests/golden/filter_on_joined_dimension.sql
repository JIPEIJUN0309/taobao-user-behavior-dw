SELECT
    COUNT(DISTINCT CASE WHEN t.behavior_type = 'buy' THEN t.user_id END) AS buy_uv
FROM taobao_dwd.dwd_user_behavior AS t
LEFT JOIN taobao_dws.dim_user_tier AS d_user_tier
    ON d_user_tier.user_id = t.user_id
WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
  AND d_user_tier.user_tier = 'high'
LIMIT 1000
