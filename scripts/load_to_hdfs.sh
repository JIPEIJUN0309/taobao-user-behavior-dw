#!/usr/bin/env bash
# =====================================================================
# 把原始 CSV 从挂载目录加载进 HDFS
# Load raw CSV from mounted dir into HDFS
# 用法 / usage:  bash scripts/load_to_hdfs.sh
# 前提 / prereq: docker compose up -d 已启动 3 个容器
# =====================================================================
set -e

HDFS_DIR=/user/taobao/ods/user_behavior
LOCAL_FILE=/data/UserBehavior.csv   # 容器内挂载路径 / mounted path inside container

echo ">> 创建 HDFS 目录 / creating HDFS dir ..."
docker exec namenode hdfs dfs -mkdir -p ${HDFS_DIR}

echo ">> 上传 CSV 到 HDFS (3.4GB, 需几分钟) / uploading CSV ..."
docker exec namenode hdfs dfs -put -f ${LOCAL_FILE} ${HDFS_DIR}/

echo ">> 验证 / verifying ..."
docker exec namenode hdfs dfs -ls -h ${HDFS_DIR}/

echo ">> 完成 / done."
