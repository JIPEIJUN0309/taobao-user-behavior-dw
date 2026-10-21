SELECT
    t.dt AS dt,
    ROUND(SUM(CASE WHEN t.behavior_type = 'buy' THEN 1 ELSE 0 END) / NULLIF(COUNT(DISTINCT CASE WHEN t.behavior_type = 'buy' THEN t.user_id END), 0), 4) AS purchase_frequency
FROM taobao_dwd.dwd_user_behavior AS t
WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
GROUP BY t.dt
LIMIT 1000
