SELECT
    t.dt AS dt,
    ROUND(SUM(CASE WHEN t.behavior_type = 'pv' THEN 1 ELSE 0 END) / NULLIF(COUNT(DISTINCT t.user_id), 0), 4) AS pv_per_user
FROM taobao_dwd.dwd_user_behavior AS t
WHERE t.dt BETWEEN '2017-11-27' AND '2017-12-03'
GROUP BY t.dt
LIMIT 1000
