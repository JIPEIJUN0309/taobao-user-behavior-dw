SELECT
    d_user_tier.user_tier AS user_tier,
    COUNT(DISTINCT t.user_id) AS active_uv
FROM taobao_dwd.dwd_user_behavior AS t
LEFT JOIN taobao_dws.dim_user_tier AS d_user_tier
    ON d_user_tier.user_id = t.user_id
WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
GROUP BY d_user_tier.user_tier
LIMIT 1000
