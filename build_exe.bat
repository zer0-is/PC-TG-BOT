@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Python окружение не найдено. Сначала запусти setup.bat.
  pause
  exit /b 1
)

if not exist "%~dp0build" mkdir "%~dp0build"
if not exist "%~dp0releases" mkdir "%~dp0releases"

"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости приложения.
  pause
  exit /b 1
)

"%PY%" -m pip install pyinstaller
if errorlevel 1 (
  echo Не удалось установить PyInstaller.
  pause
  exit /b 1
)

"%PY%" -m PyInstaller --noconfirm --onefile --noconsole --name PCBot ^
  --collect-data customtkinter ^
  --distpath "%~dp0releases" ^
  --workpath "%~dp0build\work" ^
  --specpath "%~dp0build" ^
  "%~dp0control_panel.py"
if errorlevel 1 (
  echo Сборка не удалась.
  pause
  exit /b 1
)

echo.
echo Готово: "%~dp0releases\PCBot.exe"
echo Создай или скопируй файл .env рядом с EXE. Он содержит секреты.
pause
