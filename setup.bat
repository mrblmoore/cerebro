@echo off
REM Cerebro setup. Kept for muscle memory — it just calls the cross-platform installer.
cd /d "%~dp0"
call cerebro.bat setup %*
pause
