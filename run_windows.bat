@echo off
REM Starts Stream Prompter. The first run sets up a private Python environment in .venv (needs internet).
cd /d "%~dp0"

if exist ".venv\installed.ok" goto run

echo First run: setting up Stream Prompter...
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo Python 3 was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run this file again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installing packages failed. Check your internet connection and try again.
  pause
  exit /b 1
)
echo ok> ".venv\installed.ok"

:run
start "" ".venv\Scripts\pythonw.exe" -m stream_prompter %*
