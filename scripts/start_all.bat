@echo off
for %%i in ("%~dp0..") do set "ROOT=%%~fyi"
cd /d "%ROOT%"

echo ==================================================
echo  Starting TradingView Scraper Environment
echo ==================================================

rem Check if port 9222 is active (listening)
netstat -ano | findstr LISTENING | findstr :9222 >nul
if %errorlevel% equ 0 (
    echo [INFO] Chrome debugging instance is already running on port 9222. Skipping launch.
) else (
    echo 1. Starting Chrome in Remote Debugging Mode...
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="%ROOT%\ChromeProfile" --no-first-run --no-default-browser-check --disable-search-engine-choice-screen --disable-sync
)

echo.
echo ==================================================
echo  ACTION REQUIRED:
echo  1. In your Chrome debugging tab, open tradingview.com
echo     and load your chart.
echo  2. Once the chart and table are fully loaded, 
echo     come back here and press any key to start.
echo ==================================================
pause

echo 2. Launching Python Scraper Script...
set PYTHONIOENCODING=utf-8
.\venv_314\Scripts\python.exe tv_scraper.py

pause
