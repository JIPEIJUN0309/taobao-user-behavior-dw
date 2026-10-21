SELECT
    t.dt AS dt,
    SUM(CASE WHEN t.behavior_type = 'pv' THEN 1 ELSE 0 END) AS pv_cnt,
    SUM(CASE WHEN t.behavior_type = 'cart' THEN 1 ELSE 0 END) AS cart_cnt
FROM taobao_dwd.dwd_user_behavior AS t
WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
  AND t.behavior_type = 'pv'
GROUP BY t.dt
LIMIT 100
