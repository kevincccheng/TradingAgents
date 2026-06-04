@echo off
chcp 65001 >nul
echo ============================================
echo  TradingAgents — Windows Setup
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found on PATH. Install Python 3.10+ and retry.
    pause
    exit /b 1
)

echo Creating virtual environment...
python -m venv .venv
if errorlevel 1 (
    echo ERROR: Failed to create .venv
    pause
    exit /b 1
)

echo Activating .venv...
call .venv\Scripts\activate.bat

echo Upgrading pip...
python -m pip install --upgrade pip --quiet

echo Installing TradingAgents and dependencies...
pip install -e . --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Check requirements above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Setup complete!
echo  Next: fill in your API keys in .env
echo  Then double-click run.bat to start.
echo ============================================
pause
