#!/usr/bin/env bash
# =====================================================================
# 依次执行各层 SQL, 从 ODS 一路建到 ADS
# Run all layer SQL in order: ODS -> DWD -> DWS -> ADS
# 用法 / usage:  bash scripts/run_all.sh        (在仓库根目录执行 / run from repo root)
# 前提 / prereq: docker compose -f docker/docker-compose.yml up -d
#                bash scripts/load_to_hdfs.sh
# =====================================================================
set -e

HIVE=jdbc:hive2://localhost:10000
run() {
  echo ">> 执行 / running: $1"
  docker cp "$1" hive:/tmp/_run.sql
  docker exec -i hive beeline -u ${HIVE} -f /tmp/_run.sql
}

run sql/01_ods/ods_user_behavior.sql
run sql/02_dwd/dwd_user_behavior.sql
run sql/03_dws/dws_user_summary.sql
run sql/03_dws/dws_item_summary.sql
run sql/04_ads/ads_conversion_funnel.sql
run sql/04_ads/ads_cart_not_buy.sql
run sql/04_ads/ads_daily_conversion.sql

echo ">> 全部完成 / all done."
echo ">> 运行验证 / verify:  bash scripts/verify.sh"
