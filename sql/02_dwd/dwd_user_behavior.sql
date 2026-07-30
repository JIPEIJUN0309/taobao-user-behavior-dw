-- =====================================================================
-- DWD 层 (Data Warehouse Detail / 明细层)
-- 作用: 清洗脏数据 + 时间戳转换 + 按天分区 + 转 ORC 存储
-- Purpose: clean dirty data + convert timestamp + partition by day + ORC
-- 核心技术: 动态分区 / dynamic partition, ORC 列式存储 / columnar storage
-- =====================================================================

CREATE DATABASE IF NOT EXISTS taobao_dwd;
USE taobao_dwd;

DROP TABLE IF EXISTS dwd_user_behavior;

-- 分区表 + ORC + Snappy 压缩
-- Partitioned table + ORC + Snappy compression
CREATE TABLE dwd_user_behavior (
    user_id       BIGINT        COMMENT '用户ID / user id',
    item_id       BIGINT        COMMENT '商品ID / item id',
    category_id   BIGINT        COMMENT '类目ID / category id',
    behavior_type STRING        COMMENT '行为类型 / behavior type',
    ts            BIGINT        COMMENT '时间戳 / timestamp',
    behavior_time STRING        COMMENT '行为时间 yyyy-MM-dd HH:mm:ss',
    hour          INT           COMMENT '小时 / hour of day'
)
COMMENT '淘宝用户行为 DWD 明细表 / DWD detail table'
PARTITIONED BY (dt STRING COMMENT '日期分区 / date partition')
STORED AS ORC
TBLPROPERTIES ('orc.compress' = 'SNAPPY');

-- 开启动态分区 / enable dynamic partition
SET hive.exec.dynamic.partition = true;
SET hive.exec.dynamic.partition.mode = nonstrict;
SET hive.exec.max.dynamic.partitions = 1000;
SET hive.exec.max.dynamic.partitions.pernode = 1000;

-- 清洗 + 转换 + 写入分区
-- Clean + transform + load into partitions
INSERT OVERWRITE TABLE dwd_user_behavior PARTITION (dt)
SELECT
    user_id,
    item_id,
    category_id,
    behavior_type,
    ts,
    from_unixtime(ts, 'yyyy-MM-dd HH:mm:ss')        AS behavior_time,
    hour(from_unixtime(ts, 'yyyy-MM-dd HH:mm:ss'))  AS hour,
    from_unixtime(ts, 'yyyy-MM-dd')                 AS dt
FROM taobao_ods.ods_user_behavior
WHERE user_id IS NOT NULL                                            -- 过滤空用户 / filter null user
  AND item_id IS NOT NULL                                           -- 过滤空商品 / filter null item
  AND behavior_type IN ('pv', 'buy', 'cart', 'fav')                 -- 只保留合法行为 / valid behaviors only
  AND from_unixtime(ts, 'yyyy-MM-dd')
      BETWEEN '2017-11-25' AND '2017-12-03';                        -- 只保留官方日期范围 / official date range only
