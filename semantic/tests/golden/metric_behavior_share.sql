SELECT
    s.dt AS dt,
    s.behavior_type AS behavior_type,
    ROUND(100.0 * s.__base / NULLIF(SUM(s.__base) OVER (PARTITION BY s.dt), 0), 4) AS behavior_share
FROM (
    SELECT
        t.dt AS dt,
        t.behavior_type AS behavior_type,
        COUNT(1) AS __base
    FROM taobao_dwd.dwd_user_behavior AS t
    WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
    GROUP BY t.dt, t.behavior_type
) AS s
ORDER BY s.dt, s.behavior_type
LIMIT 1000
