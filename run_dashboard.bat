@echo off
REM Dashboard web server -> http://localhost:5000
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" "src\server.py"
