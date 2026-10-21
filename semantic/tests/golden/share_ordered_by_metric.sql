SELECT
    s.behavior_type AS behavior_type,
    ROUND(100.0 * s.__base / NULLIF(SUM(s.__base) OVER (), 0), 4) AS behavior_share
FROM (
    SELECT
        t.behavior_type AS behavior_type,
        COUNT(1) AS __base
    FROM taobao_dwd.dwd_user_behavior AS t
    WHERE t.dt BETWEEN '2017-11-25' AND '2017-12-03'
    GROUP BY t.behavior_type
) AS s
ORDER BY behavior_share DESC NULLS LAST, s.behavior_type ASC
LIMIT 1000
