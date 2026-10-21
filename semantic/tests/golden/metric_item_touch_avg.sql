SELECT
    d_user_tier.user_tier AS user_tier,
    ROUND(AVG(t.item_cnt), 2) AS item_touch_avg
FROM taobao_dws.dws_user_summary AS t
LEFT JOIN taobao_dws.dim_user_tier AS d_user_tier
    ON d_user_tier.user_id = t.user_id
GROUP BY d_user_tier.user_tier
ORDER BY d_user_tier.user_tier ASC
LIMIT 1000
