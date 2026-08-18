@echo off
REM Cerebro launcher for Windows.
REM   cerebro.bat            interactive menu
REM   cerebro.bat start      run a command directly
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (where python >nul 2>&1 && set "PY=python")

if not defined PY (
  echo.
  echo   Python 3.9 or newer is required but was not found.
  echo.
  echo   Install it from https://python.org/downloads
  echo   Tick "Add python.exe to PATH" during installation, then run this again.
  echo.
  pause
  exit /b 1
)

if not "%~1"=="" (
  %PY% cerebro.py %*
  exit /b %errorlevel%
)

:menu
cls
echo.
echo    Cerebro
echo.
echo     1^) Start Cerebro           ^(API + dashboard^)
echo     2^) Launch desktop widget
echo     3^) Run setup / install
echo     4^) Check status
echo     5^) Diagnose problems
echo     6^) Quit
echo.
set "choice="
set /p choice=  Choose [1-6]: 

REM Each branch uses a block: `cmd & goto` on one line would run the goto
REM unconditionally, because cmd.exe splits on ^& before evaluating the if.
if "%choice%"=="1" (
  %PY% cerebro.py start
  goto end
)
if "%choice%"=="2" (
  %PY% cerebro.py widget
  goto end
)
if "%choice%"=="3" (
  %PY% cerebro.py setup
  pause
  goto menu
)
if "%choice%"=="4" (
  %PY% cerebro.py status
  pause
  goto menu
)
if "%choice%"=="5" (
  %PY% cerebro.py doctor
  pause
  goto menu
)
if "%choice%"=="6" goto end
goto menu

:end
endlocal
