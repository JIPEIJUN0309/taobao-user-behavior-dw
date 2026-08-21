@echo off
REM ============================================================
REM  Windows native verifier (no bash / WSL required).
REM  Usage:  scripts\verify.cmd         (run from the repo root)
REM  Prereq: scripts\run_all.cmd finished
REM ============================================================
setlocal
set HIVE=jdbc:hive2://localhost:10000
set SQL=sql\verify\verify_all.sql

echo === running: %SQL%
docker cp "%SQL%" hive:/tmp/_verify.sql || exit /b 1
docker exec -i hive beeline -u %HIVE% -f /tmp/_verify.sql || exit /b 1

echo.
echo === verification done.
