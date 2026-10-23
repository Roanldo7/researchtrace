@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11+ is required. Install Python and add it to PATH.
  pause
  exit /b 1
)
python -B agent_demo\main.py serve --open
pause
