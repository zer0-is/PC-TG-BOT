@echo off
chcp 65001 >NUL
cd /d "%~dp0"
title PC bot
".venv\Scripts\python.exe" bot.py
if errorlevel 1 pause
