@echo off
chcp 65001 >nul
pushd "%~dp0"
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: .venv not found. Run setup.bat first.
    pause
    popd & exit /b 1
)

call .venv\Scripts\activate.bat

if not exist "outputs" mkdir outputs
if not exist "outputs\crash_logs" mkdir outputs\crash_logs
set TRADINGAGENTS_RESULTS_DIR=outputs

rem ---- API key check (reads .env directly) ----------------------
set KEY_OK=0
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if /i "%%A"=="ANTHROPIC_API_KEY" if not "%%B"=="" set KEY_OK=1
    )
)
if %KEY_OK%==0 (
    echo.
    echo  [!] WARNING: ANTHROPIC_API_KEY not found or empty in .env
    echo      API credit is SEPARATE from your claude.ai subscription.
    echo      Top up at: platform.anthropic.com/settings/billing
    echo.
)

rem ---- Crash log with timestamp ---------------------------------
for /f %%i in (\'powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm"\') do set TIMESTAMP=%%i
set CRASH_LOG=outputs\crash_logs\crash_%TIMESTAMP%.txt

rem ---- Launch (stderr captured to crash log) --------------------
echo.
tradingagents 2>"%CRASH_LOG%"
set EXIT_CODE=%ERRORLEVEL%
echo.

rem ---- Post-run handling ----------------------------------------
if %EXIT_CODE% neq 0 (
    echo ============================================
    echo  Analysis ended with error (exit code %EXIT_CODE%)
    echo  Crash log: %CRASH_LOG%
    echo  Attempting to save any partial output...
    echo ============================================
    echo.
    python convert_report.py
    if errorlevel 1 (
        echo  No partial output found.
        echo  Check %CRASH_LOG% for error details.
    ) else (
        echo.
        echo  Partial PDF: outputs\latest_report.pdf
    )
) else (
    for %%F in ("%CRASH_LOG%") do if %%~zF==0 del "%CRASH_LOG%" 2>nul
    echo ============================================
    echo  Generating PDF report...
    echo  Tip: press Y at the Save report? prompt
    echo ============================================
    echo.
    python convert_report.py
    if errorlevel 1 (
        echo  NOTE: No saved report found. Press Y next time.
    ) else (
        echo.
        echo  PDF saved to: outputs\latest_report.pdf
    )
)
echo.
pause
popd
