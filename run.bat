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

if not exist "outputs" mkdir outputs
set TRADINGAGENTS_RESULTS_DIR=outputs

rem .env is loaded automatically by tradingagents on startup (python-dotenv)
tradingagents

echo.
echo ============================================
echo  Generating PDF report...
echo  (Tip: press Y at Save report? for best results)
echo ============================================
python convert_report.py
if errorlevel 1 (
    echo NOTE: PDF skipped - no saved report found.
    echo       Press Y at the Save report? prompt next time.
) else (
    echo.
    echo PDF also saved to: outputs\latest_report.pdf
)
echo.
pause
