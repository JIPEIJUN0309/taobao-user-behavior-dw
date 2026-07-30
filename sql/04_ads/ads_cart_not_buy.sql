-- =====================================================================
-- ADS 层 (Application Data Store / 应用层) - 加购未购名单
-- 作用: 找出"加购了但没购买"的用户+商品, 高意向未转化人群
-- Purpose: users who added-to-cart but did NOT buy -> re-marketing gold
-- 核心技术: 反连接 anti-join
--   left join 加购表 与 购买表, 取购买侧为 NULL 的记录
--   (in cart but NOT in buy)
-- =====================================================================

USE taobao_ads;

DROP TABLE IF EXISTS ads_cart_not_buy;

CREATE TABLE ads_cart_not_buy
STORED AS ORC
AS
SELECT
    c.user_id,
    c.item_id,
    c.category_id
FROM (
    -- 所有加购的 (user, item) / all add-to-cart pairs
    SELECT DISTINCT user_id, item_id, category_id
    FROM taobao_dwd.dwd_user_behavior
    WHERE behavior_type = 'cart'
) c
LEFT JOIN (
    -- 所有购买的 (user, item) / all buy pairs
    SELECT DISTINCT user_id, item_id
    FROM taobao_dwd.dwd_user_behavior
    WHERE behavior_type = 'buy'
) b
  ON c.user_id = b.user_id AND c.item_id = b.item_id
WHERE b.user_id IS NULL;   -- 购买侧匹配不上 = 加购了但没买 / not matched in buy = cart-not-buy
