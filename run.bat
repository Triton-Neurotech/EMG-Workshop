@echo off
REM Starts the workshop app using the environment created by setup.bat.
REM Arguments are passed through, e.g.:  run.bat --keyboard     run.bat --channels 1
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo No .venv found - run setup.bat first.
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python main.py %*
if errorlevel 1 pause
