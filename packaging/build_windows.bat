@echo off
REM Build the Cerebro Windows executable and installer.
REM
REM   packaging\build_windows.bat
REM
REM Produces:
REM   dist\Cerebro\Cerebro.exe    the application
REM   dist\CerebroSetup.exe       the installer (needs Inno Setup 6)
REM
REM Requires Python 3.9+ on PATH. Everything else is installed here.

setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo.
echo   Building Cerebro for Windows
echo.

set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (where python >nul 2>&1 && set "PY=python")
if not defined PY (
  echo   Python was not found. Install it from https://python.org/downloads
  echo   and tick "Add python.exe to PATH".
  exit /b 1
)

echo   [1/4] Preparing the build environment...
if not exist ".venv-build\Scripts\python.exe" (
  %PY% -m venv .venv-build || exit /b 1
)
set "BUILDPY=.venv-build\Scripts\python.exe"

%BUILDPY% -m pip install --quiet --upgrade pip
%BUILDPY% -m pip install --quiet -r backend\requirements.txt || exit /b 1
%BUILDPY% -m pip install --quiet -r backend\requirements-documents.txt || exit /b 1
%BUILDPY% -m pip install --quiet -r desktop\requirements.txt || exit /b 1
%BUILDPY% -m pip install --quiet pyinstaller || exit /b 1

echo   [2/4] Cleaning previous output...
if exist "dist\Cerebro" rmdir /s /q "dist\Cerebro"
if exist "build" rmdir /s /q "build"

echo   [3/4] Building the executables (this takes a few minutes)...
%BUILDPY% -m PyInstaller packaging\cerebro.spec --noconfirm --distpath dist --workpath build
if errorlevel 1 (
  echo.
  echo   Build failed. The PyInstaller output above says why.
  exit /b 1
)

echo   [4/4] Building the installer...
set "ISCC="
for %%P in (
  "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do if exist %%P set "ISCC=%%~P"

if not defined ISCC (
  echo.
  echo   Inno Setup 6 was not found, so no installer was built.
  echo   The application itself is ready in dist\Cerebro\
  echo.
  echo   To build the installer too, install Inno Setup from
  echo   https://jrsoftware.org/isdl.php and run this again.
  exit /b 0
)

"%ISCC%" packaging\installer.iss
if errorlevel 1 exit /b 1

echo.
echo   Done.
echo     Application : dist\Cerebro\Cerebro.exe
echo     Installer   : dist\CerebroSetup.exe
echo.
endlocal
