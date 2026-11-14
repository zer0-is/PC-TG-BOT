@echo off
chcp 65001 >NUL
cd /d "%~dp0"
echo Обновляю библиотеки бота...
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Не удалось установить библиотеки & pause & exit /b 1)
echo.
echo Готово! Запускаю бота...
start "PC bot" /min cmd /c "%~dp0start_bot.bat"
timeout /t 3 >NUL
