@echo off
title S2 Telegram Bot
color 0A
echo ========================================
echo Starting S2 Telegram Bot...
echo Please leave this window open.
echo ========================================
cd /d "%~dp0"
python run_bot.py
pause
