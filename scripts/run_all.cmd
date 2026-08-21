@echo off
REM ============================================================
REM  Windows native runner (no bash / WSL required).
REM  Runs every layer SQL in order: ODS -> DWD -> DWS -> ADS
REM  Usage:  scripts\run_all.cmd        (run from the repo root)
REM  Prereq: docker compose -f docker\docker-compose.yml up -d --build
REM          scripts\load_to_hdfs.cmd
REM ============================================================
setlocal
set HIVE=jdbc:hive2://localhost:10000

for %%F in (
  "sql\01_ods\ods_user_behavior.sql"
  "sql\02_dwd\dwd_user_behavior.sql"
  "sql\03_dws\dws_user_summary.sql"
  "sql\03_dws\dws_item_summary.sql"
  "sql\04_ads\ads_conversion_funnel.sql"
  "sql\04_ads\ads_cart_not_buy.sql"
  "sql\04_ads\ads_daily_conversion.sql"
) do (
  echo.
  echo === running: %%~F
  docker cp "%%~F" hive:/tmp/_run.sql || exit /b 1
  docker exec -i hive beeline -u %HIVE% -f /tmp/_run.sql || exit /b 1
)

echo.
echo === all done. Next: scripts\verify.cmd
