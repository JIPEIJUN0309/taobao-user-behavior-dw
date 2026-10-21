SELECT
    d_user_tier.user_tier AS user_tier,
    SUM(CASE WHEN t.buy_cnt >= 2 THEN 1 ELSE 0 END) AS repurchase_uv
FROM taobao_dws.dws_user_summary AS t
LEFT JOIN taobao_dws.dim_user_tier AS d_user_tier
    ON d_user_tier.user_id = t.user_id
GROUP BY d_user_tier.user_tier
LIMIT 1000
