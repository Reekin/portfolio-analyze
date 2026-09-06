@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0scripts\.env" set "IBKR_ANALYZER_ENV=%~dp0scripts\.env"
python launcher.py
