# 淘宝用户行为离线数据仓库 / Taobao User-Behavior Offline Data Warehouse

> 基于 Hadoop + Hive 构建的四层离线数仓,处理 **1 亿行**淘宝用户行为数据,完成从原始日志到业务指标(转化漏斗、加购未购名单)的全链路加工。
>
> A four-layer offline data warehouse on Hadoop + Hive, processing **100M rows** of Taobao user-behavior data from raw logs into business metrics such as conversion funnels and re-marketing lists.

![Architecture](docs/architecture.svg)

---

## 目录 / Table of Contents
- [项目亮点 / Highlights](#项目亮点--highlights)
- [技术栈 / Tech Stack](#技术栈--tech-stack)
- [数据集 / Dataset](#数据集--dataset)
- [数仓分层 / Warehouse Layers](#数仓分层--warehouse-layers)
- [核心成果 / Key Results](#核心成果--key-results)
- [快速开始 / Quick Start](#快速开始--quick-start)
- [目录结构 / Project Structure](#目录结构--project-structure)
- [技术要点 / Technical Highlights](#技术要点--technical-highlights)
- [踩坑记录 / Lessons Learned](#踩坑记录--lessons-learned)

---

## 项目亮点 / Highlights

- 🗄️ **真实大数据规模** — 处理阿里天池 1 亿行 / 3.4GB 原始行为日志 / handles 100M rows on HDFS
- 🏗️ **规范数仓分层** — ODS → DWD → DWS → ADS 四层建模 / standard 4-layer modeling
- ⚡ **存储与查询优化** — ORC 列式存储 + Snappy 压缩,压缩比达 **14:1** / ORC + Snappy, 14:1 compression
- 📊 **业务指标产出** — 转化漏斗(双口径)、加购未购名单(500万)、每日趋势 / real business metrics
- 🐳 **一键可复现环境** — Docker Compose 编排 Hadoop + Hive / reproducible via Docker Compose

---

## 技术栈 / Tech Stack

| 类别 / Category | 技术 / Technology |
|---|---|
| 分布式存储 / Storage | HDFS (Hadoop 3.2) |
| 计算引擎 / Compute | Hive 4.0 on Tez |
| 文件格式 / File Format | ORC (列式 columnar) + Snappy |
| 编排 / Orchestration | Docker Compose |
| 语言 / Language | HiveSQL |

---

## 数据集 / Dataset

来源:[阿里天池 UserBehavior](https://tianchi.aliyun.com/dataset/649) / Source: Alibaba Tianchi UserBehavior

| 字段 / Field | 类型 / Type | 说明 / Description |
|---|---|---|
| user_id | BIGINT | 用户 ID / user id |
| item_id | BIGINT | 商品 ID / item id |
| category_id | BIGINT | 类目 ID / category id |
| behavior_type | STRING | 行为类型:`pv` 浏览 / `cart` 加购 / `fav` 收藏 / `buy` 购买 |
| timestamp | BIGINT | 行为时间戳(秒)/ behavior timestamp (sec) |

- **规模 / Volume**: ~100,000,000 rows, 3.41 GB
- **时间范围 / Range**: 2017-11-25 ~ 2017-12-03 (9 天 / 9 days)

---

## 数仓分层 / Warehouse Layers

数据加工遵循 **ELT** 范式(先加载原始数据,再在仓库内逐层转换)。粒度逐层变粗。
Follows the **ELT** paradigm; granularity coarsens layer by layer.

| 层 / Layer | 全称 / Full Name | 职责 / Role | 存储 / Storage |
|---|---|---|---|
| **ODS** | Operational Data Store | 贴源,原样加载不清洗 / raw copy | 外部表 External · TextFile |
| **DWD** | Data Warehouse Detail | 清洗 + 时间转换 + 按天分区 / clean & partition | ORC + Snappy |
| **DWS** | Data Warehouse Summary | 按用户/商品聚合宽表 / aggregate | ORC + Snappy |
| **ADS** | Application Data Store | 业务指标产出 / business metrics | ORC |

---

## 核心成果 / Key Results

### 1️⃣ 转化漏斗 / Conversion Funnel

| 口径 / Definition | PV | Cart | Fav | Buy | 购买/浏览率 Buy-Rate |
|---|---|---|---|---|---|
| 事件数 event_count | 88.6M | 5.47M | 2.85M | 2.00M | **2.26%** |
| 独立用户 unique_user | 984K | 737K | 388K | 670K | **68.12%** |

> **洞察 / Insight**: 两种口径相差 30 倍。单次浏览转化仅 2.26%,但 **68% 的活跃用户最终会购买**。说明业务应关注用户长期转化路径,而非单次转化率。选对口径直接影响判断。
> Event-count buy-rate is only 2.26%, yet 68% of active users eventually buy — metric definition drives the business conclusion.

### 2️⃣ 加购未购名单 / Cart-Not-Buy List

- **规模 / Size**: **4,981,518** 条高意向未转化记录 / high-intent non-converting records
- **实现 / Method**: 反连接 anti-join(加购明细 `LEFT JOIN` 购买明细,取购买侧为 NULL)
- **业务价值 / Value**: 这些用户已加购,是营销 ROI 最高的召回人群(优惠券、购物车提醒)

### 3️⃣ 每日转化趋势 / Daily Trend

发现 **大促预热效应**:12-02(临近双十二)流量比平时高 30%,但转化率降至最低(2.08%)——用户提前涌入浏览加购囤货,下单集中在大促当天。
Found a **pre-promotion effect**: Dec-2 traffic +30% but conversion dips to its lowest, as users browse/cart ahead of the sale.

---

## 快速开始 / Quick Start

### 前置 / Prerequisites
- Docker Desktop (WSL2 backend on Windows)
- 下载 UserBehavior.csv 放到 `D:/data/taobao_data/UserBehavior/`(或改 compose 中的挂载路径)

### 步骤 / Steps

```bash
# 1. 启动环境 / start the stack (3 containers)
cd docker
docker compose up -d
# 等 60-90 秒让 Hive 就绪 / wait 60-90s for Hive to be ready

# 2. 数据落 HDFS / load CSV into HDFS
bash ../scripts/load_to_hdfs.sh

# 3. 一键建全部四层 / build all layers ODS->DWD->DWS->ADS
bash ../scripts/run_all.sh

# 4. 验证结果 / verify
docker cp ../sql/verify/verify_all.sql hive:/tmp/verify_all.sql
docker exec -it hive beeline -u jdbc:hive2://localhost:10000 -f /tmp/verify_all.sql
```

> Web UI: HDFS `http://localhost:9870` · HiveServer2 `http://localhost:10002`

---

## 目录结构 / Project Structure

```
taobao-user-behavior-dw/
├── README.md
├── docker/
│   ├── docker-compose.yml       # 3容器编排 (含持久化元数据卷)
│   └── hadoop.env               # Hadoop 配置
├── sql/
│   ├── 01_ods/
│   │   └── ods_user_behavior.sql        # 外部表贴源
│   ├── 02_dwd/
│   │   └── dwd_user_behavior.sql        # 清洗+分区+ORC
│   ├── 03_dws/
│   │   ├── dws_user_summary.sql         # 用户维度宽表
│   │   └── dws_item_summary.sql         # 商品维度宽表
│   ├── 04_ads/
│   │   ├── ads_conversion_funnel.sql    # 转化漏斗(双口径)
│   │   ├── ads_cart_not_buy.sql         # 加购未购(anti-join)
│   │   └── ads_daily_conversion.sql     # 每日趋势
│   └── verify/
│       └── verify_all.sql               # 各层验证查询
├── scripts/
│   ├── load_to_hdfs.sh          # CSV 落 HDFS
│   └── run_all.sh               # 依次执行各层SQL
└── docs/
    └── architecture.svg         # 架构图
```

---

## 技术要点 / Technical Highlights

以下是本项目用到的关键技术,也是数据工程评审高频考点。
Key techniques used — and common data-engineering topics.

| 技术 / Technique | 说明 / Description | 用在哪 / Where |
|---|---|---|
| **外部表 External Table** | drop 表不删数据,贴源层数据安全 | ODS |
| **动态分区 Dynamic Partition** | 按数据的 `dt` 字段自动创建分区,需 `nonstrict` 模式 | DWD |
| **ORC + 压缩** | 列式存储,只读需要的列,压缩比 14:1 | DWD/DWS |
| **CTAS** | `CREATE TABLE AS SELECT` 建表同时灌数据 | DWS/ADS |
| **条件聚合 Conditional Agg** | `SUM(CASE WHEN ...)` 一次扫描算多指标 | DWS/ADS |
| **反连接 Anti-Join** | `LEFT JOIN ... WHERE b.key IS NULL` 找"A有B没有" | ADS |
| **UNION ALL** | 合并两种漏斗口径到一张表 | ADS |

---

## 踩坑记录 / Lessons Learned

真实开发中遇到并解决的问题(也是宝贵的工程经验):
Real issues encountered and solved during development.

1. **Metastore 反复退出 / metastore keeps exiting**
   最初的 bde2020 镜像独立 metastore 容器因启动竞速崩溃 → 换成 apache/hive:4.0.0 单容器镜像。
   *启示:组件反复修不好时,换经过验证的方案比死磕更专业。*

2. **大文件传输卡顿 / large file transfer stalls**
   `docker cp` 传 3.4GB 会卡死容器 → 改用**挂载卷 (volume mount)** 让容器直读本地文件,零拷贝。
   *这也是生产环境处理大文件的标准做法。*

3. **`file:/` vs `hdfs://`**
   外部表 location 写相对路径被当成本地路径报错 → location 必须写完整 HDFS 地址 `hdfs://namenode:9000/...`。

4. **元数据丢失 / metastore data loss** ⭐
   重建 hive 容器时,内嵌 Derby 元数据库随容器删除,导致表定义全丢(数据仍在 HDFS)→ 把 Derby 目录**持久化到数据卷**。
   *启示:为什么生产环境 metastore 必须独立持久化。*

---

## License

本项目仅用于学习与作品集展示。数据集版权归阿里天池所有。
For learning and portfolio purposes only. Dataset © Alibaba Tianchi.
