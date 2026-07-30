-- =====================================================================
-- ODS 层 (Operational Data Store / 贴源层)
-- 作用: 原始数据照搬进来, 不做清洗, 保持与源头一致
-- Purpose: Load raw data as-is, no cleaning, keep source fidelity
-- 存储格式: TextFile (贴源保持原样 / keep raw format)
-- 表类型: 外部表 (drop 表不删数据, 数据安全 / external table, safe)
-- =====================================================================

CREATE DATABASE IF NOT EXISTS taobao_ods;
USE taobao_ods;

DROP TABLE IF EXISTS ods_user_behavior;

CREATE EXTERNAL TABLE ods_user_behavior (
    user_id       BIGINT COMMENT '用户ID / user id',
    item_id       BIGINT COMMENT '商品ID / item id',
    category_id   BIGINT COMMENT '商品类目ID / category id',
    behavior_type STRING COMMENT '行为类型 pv/buy/cart/fav',
    ts            BIGINT COMMENT '行为时间戳(秒) / behavior timestamp (sec). 注: timestamp 是 Hive 保留字, 故命名 ts'
)
COMMENT '淘宝用户行为 ODS 贴源表 / Taobao user behavior ODS table'
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 'hdfs://namenode:9000/user/taobao/ods/user_behavior';

-- 验证 / verify
SELECT * FROM ods_user_behavior LIMIT 5;
