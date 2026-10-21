@echo off
title XAUBOTTY - XAU/USD Precision Signal Bot
cd /d "%~dp0"
echo ===================================================
echo   XAUBOTTY - XAU/USD Precision Signal Bot
echo   Connected to TwelveData & Telegram
echo ===================================================
.venv\Scripts\python.exe bot.py
pause

