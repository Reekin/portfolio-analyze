@echo off
setlocal
cd /d "%~dp0"
python backend\benchmarks.py sync
exit /b %ERRORLEVEL%
