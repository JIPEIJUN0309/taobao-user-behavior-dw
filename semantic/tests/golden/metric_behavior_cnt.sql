SELECT
    t.dt AS dt,
    COUNT(1) AS behavior_cnt
FROM taobao_dwd.dwd_user_behavior AS t
WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
GROUP BY t.dt
LIMIT 1000
