@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo First run: installing...
  call setup.bat
)
title JobAutomater - http://127.0.0.1:8765
set PYTHONIOENCODING=utf-8
.venv\Scripts\python -m app.main
pause
