@echo off
title Indian Mutual Funds Analytics Dashboard
echo ========================================================
echo    Indian Mutual Funds AMFI Analytics Dashboard
echo ========================================================
echo.

docker compose up -d

echo.
echo ========================================================
echo    DASHBOARD IS UP AND RUNNING!
echo ========================================================
echo Dashboard URL : http://localhost:8501
echo Database      : DuckDB (embedded in fetcher/data/mutual_funds.duckdb)
echo AMFI Schemes  : growing daily via official AMFI sync
echo ========================================================
echo.

start http://localhost:8501
pause
