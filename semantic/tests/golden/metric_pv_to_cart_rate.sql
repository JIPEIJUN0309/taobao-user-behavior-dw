SELECT
    t.dt AS dt,
    ROUND(100.0 * COUNT(DISTINCT CASE WHEN t.behavior_type = 'cart' THEN t.user_id END) / NULLIF(COUNT(DISTINCT CASE WHEN t.behavior_type = 'pv' THEN t.user_id END), 0), 4) AS pv_to_cart_rate
FROM taobao_dwd.dwd_user_behavior AS t
WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
GROUP BY t.dt
ORDER BY t.dt ASC
LIMIT 1000
