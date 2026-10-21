SELECT
    t0.dt AS dt,
    ROUND(100.0 * COUNT(DISTINCT t1.user_id) / NULLIF(COUNT(DISTINCT t0.user_id), 0), 4) AS retention_d1
FROM (
    SELECT DISTINCT dt, user_id
    FROM taobao_dwd.dwd_user_behavior
    WHERE dt BETWEEN '2017-11-25' AND '2017-12-03'
) AS t0
LEFT JOIN (
    SELECT DISTINCT dt, user_id
    FROM taobao_dwd.dwd_user_behavior
    WHERE dt BETWEEN '2017-11-25' AND '2017-12-04'
) AS t1
    ON t1.user_id = t0.user_id
   AND t1.dt = date_add(t0.dt, 1)
GROUP BY t0.dt
ORDER BY t0.dt ASC
LIMIT 1000
