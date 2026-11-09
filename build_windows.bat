@echo off
REM Builds dist\StreamPrompter.exe: a single file with no console window, no Python needed to run it.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo Python 3 was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name StreamPrompter ^
  --icon "stream_prompter\assets\icon.ico" ^
  --add-data "stream_prompter\default_sets;stream_prompter\default_sets" ^
  --add-data "stream_prompter\assets;stream_prompter\assets" ^
  launcher.py
if errorlevel 1 goto fail

echo.
echo Done: dist\StreamPrompter.exe
pause
exit /b 0

:fail
echo.
echo Build failed. See the messages above.
pause
exit /b 1
