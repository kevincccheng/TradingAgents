@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: .venv not found. Run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

rem .env is loaded automatically by tradingagents on startup (python-dotenv)
tradingagents
