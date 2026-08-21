@echo off
REM ============================================================
REM  Load the raw CSV from the mounted dir into HDFS.
REM  Usage:  scripts\load_to_hdfs.cmd   (run from the repo root)
REM  Prereq: docker compose -f docker\docker-compose.yml up -d
REM  Note:   only needed on a fresh HDFS volume (3.4GB, takes minutes).
REM ============================================================
setlocal
set HDFS_DIR=/user/taobao/ods/user_behavior
set LOCAL_FILE=/data/UserBehavior.csv

echo === creating HDFS dir ...
docker exec namenode hdfs dfs -mkdir -p %HDFS_DIR% || exit /b 1

echo === uploading CSV to HDFS (3.4GB, several minutes) ...
docker exec namenode hdfs dfs -put -f %LOCAL_FILE% %HDFS_DIR%/ || exit /b 1

echo === verifying ...
docker exec namenode hdfs dfs -ls -h %HDFS_DIR%/ || exit /b 1

echo === done.
