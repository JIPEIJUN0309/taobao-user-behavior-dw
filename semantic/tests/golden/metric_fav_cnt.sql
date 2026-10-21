SELECT
    t.dt AS dt,
    SUM(CASE WHEN t.behavior_type = 'fav' THEN 1 ELSE 0 END) AS fav_cnt
FROM taobao_dwd.dwd_user_behavior AS t
WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
GROUP BY t.dt
ORDER BY t.dt ASC
LIMIT 1000
