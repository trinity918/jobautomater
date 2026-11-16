@echo off
cd /d "%~dp0"
echo Setting up JobAutomater...
if not exist .venv (
  python -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
)
call .venv\Scripts\python -m pip install --upgrade pip -q
call .venv\Scripts\python -m pip install -r requirements.txt -q || (echo pip install failed & pause & exit /b 1)
rem Chrome or Edge on this PC is used first; Playwright's Chromium is only a fallback (Smart App Control may block it).
call .venv\Scripts\python -m playwright install chromium || echo Chromium download skipped: Chrome or Edge will be used.
if not exist .env copy .env.example .env >nul
if not exist config\profile.yaml copy config\profile.example.yaml config\profile.yaml >nul
if not exist config\companies.yaml call .venv\Scripts\python tools\build_companies.py
echo.
echo Done. Next: run run.bat, upload your resume, and put anything else in info.txt (see README.md)
pause
