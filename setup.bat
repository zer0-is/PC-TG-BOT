@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Настраиваю PC Bot...
set PY=
where py >NUL 2>NUL && set PY=py -3.13
if not defined PY (where python >NUL 2>NUL && set PY=python)
if not defined PY (
  echo Python не найден. Установи Python 3.13 и запусти этот файл снова.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Не удалось создать виртуальное окружение.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo Не удалось установить библиотеки & pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install -r requirements-panel.txt || (echo Не удалось установить библиотеки панели & pause & exit /b 1)
echo.
echo Готово! Открываю панель управления...
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0control_panel.py"
pause
