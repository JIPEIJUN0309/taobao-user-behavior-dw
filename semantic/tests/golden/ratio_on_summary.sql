SELECT
    t.category_id AS category_id,
    ROUND(100.0 * SUM(t.buy_cnt) / NULLIF(SUM(t.pv_cnt), 0), 4) AS category_conv_rate
FROM taobao_dws.dws_item_summary AS t
GROUP BY t.category_id
ORDER BY category_conv_rate DESC NULLS LAST, t.category_id ASC
LIMIT 5
