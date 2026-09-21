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
echo App URL       : http://localhost:3000
echo API           : http://localhost:8000 (FastAPI, /api/health)
echo Database      : PostgreSQL 16 (compose project indian-mutual-funds)
echo Frontend rebuild after UI changes: docker compose up -d --build frontend
echo AMFI Schemes  : growing daily via official AMFI sync
echo ========================================================
echo.

start http://localhost:3000
pause
