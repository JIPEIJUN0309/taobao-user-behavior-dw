SELECT
    w.dt AS dt,
    w.day_over_day AS day_over_day
FROM (
    SELECT
        s.dt AS dt,
        ROUND(100.0 * (s.__base - LAG(s.__base, 1) OVER (ORDER BY s.dt ASC)) / NULLIF(LAG(s.__base, 1) OVER (ORDER BY s.dt ASC), 0), 4) AS day_over_day
    FROM (
        SELECT
            t.dt AS dt,
            ROUND(100.0 * COUNT(DISTINCT CASE WHEN t.behavior_type = 'buy' THEN t.user_id END) / NULLIF(COUNT(DISTINCT CASE WHEN t.behavior_type = 'pv' THEN t.user_id END), 0), 4) AS __base
        FROM taobao_dwd.dwd_user_behavior AS t
        WHERE t.dt BETWEEN '2017-11-24' AND '2017-12-03'
        GROUP BY t.dt
    ) AS s
) AS w
WHERE w.dt BETWEEN '2017-11-25' AND '2017-12-03'
ORDER BY w.dt ASC
LIMIT 1000
