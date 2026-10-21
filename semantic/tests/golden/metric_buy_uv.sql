SELECT
    t.dt AS dt,
    COUNT(DISTINCT CASE WHEN t.behavior_type = 'buy' THEN t.user_id END) AS buy_uv
FROM taobao_dwd.dwd_user_behavior AS t
WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
GROUP BY t.dt
LIMIT 1000
