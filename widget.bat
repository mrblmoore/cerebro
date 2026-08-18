@echo off
REM Launch the Cerebro desktop widget with no console window.
REM Double-click this file, or pin it to your taskbar / Start menu.
setlocal
cd /d "%~dp0"

REM Prefer the project's own virtual environment.
set "PYW=.venv\Scripts\pythonw.exe"
if exist "%PYW%" (
  start "" "%PYW%" cerebro.py widget
  exit /b 0
)

where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw cerebro.py widget
  exit /b 0
)

echo Cerebro is not installed yet. Running setup first...
call cerebro.bat setup
if exist "%PYW%" start "" "%PYW%" cerebro.py widget
endlocal
