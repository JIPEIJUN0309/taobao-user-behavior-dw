#!/usr/bin/env bash
# =====================================================================
# 跑一遍各层验证查询 / Run the verification queries across all layers
# 用法 / usage:  bash scripts/verify.sh          (在仓库根目录执行 / run from repo root)
# 前提 / prereq: bash scripts/run_all.sh 已跑完
# =====================================================================
set -e

HIVE=jdbc:hive2://localhost:10000
SQL=sql/verify/verify_all.sql

echo ">> 执行验证 / running: ${SQL}"
docker cp "${SQL}" hive:/tmp/_verify.sql
docker exec -i hive beeline -u ${HIVE} -f /tmp/_verify.sql

echo ">> 验证完成 / verification done."
