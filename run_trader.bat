@echo off
REM Auto-trade one cycle. Task Scheduler calls this every 5 min during market hours.
REM Console output is also appended to logs\trader_out.log (for import/config errors).
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
".venv\Scripts\python.exe" "src\trader.py" >> "logs\trader_out.log" 2>&1
