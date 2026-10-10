@echo off
REM One-time setup (Windows): creates a private Python environment in .venv
REM and installs everything the workshop needs. Double-click, or run from a terminal.
cd /d "%~dp0"

REM Pick the first Python >= 3.10 we can find.
set PY=
for %%C in ("py -3.12" "py -3.13" "py -3.11" "py -3.14" "py -3.10" "py -3" "python") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set PY=%%~C
  )
)
if not defined PY (
  echo Python 3.10 or newer is required. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" in the installer, then re-run this file.
  pause
  exit /b 1
)

echo Creating .venv ...
%PY% -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
echo Installing packages (this takes a minute or two the first time) ...
python -m pip install -r requirements.txt
echo.
python check_setup.py
echo.
echo Setup complete. Start the workshop with run.bat  (add --keyboard to try it without hardware)
pause
