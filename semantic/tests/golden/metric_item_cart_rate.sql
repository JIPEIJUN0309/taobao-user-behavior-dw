SELECT
    t.category_id AS category_id,
    ROUND(100.0 * SUM(t.cart_cnt) / NULLIF(SUM(t.pv_cnt), 0), 4) AS item_cart_rate
FROM taobao_dws.dws_item_summary AS t
GROUP BY t.category_id
ORDER BY t.category_id ASC
LIMIT 1000
