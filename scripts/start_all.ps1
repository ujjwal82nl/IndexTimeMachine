# Start script
$RootDir = Split-Path $PSScriptRoot -Parent

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " Starting TradingView Scraper Environment" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

# Check if port 9222 is active
$chromeActive = $false
try {
    $tcp = [System.Net.Sockets.TcpClient]::new("127.0.0.1", 9222)
    $chromeActive = $true
    $tcp.Close()
} catch {
    $chromeActive = $false
}

if ($chromeActive) {
    Write-Host "[INFO] Chrome debugging instance already active on port 9222. Skipping launch." -ForegroundColor Yellow
} else {
    Write-Host "1. Starting Chrome in Remote Debugging Mode..." -ForegroundColor Green
    $chromeArgs = @(
        "--remote-debugging-port=9222",
        "--remote-allow-origins=*",
        "--user-data-dir=$RootDir\ChromeProfile",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
        "--disable-sync"
    )
    Start-Process -FilePath "C:\Program Files\Google\Chrome\Application\chrome.exe" -ArgumentList $chromeArgs
}

# Manual Pause
Write-Host ""
Write-Host "==================================================" -ForegroundColor Yellow
Write-Host " ACTION REQUIRED:" -ForegroundColor Yellow
Write-Host " 1. In your Chrome debugging tab, open tradingview.com" -ForegroundColor Yellow
Write-Host "    and load your chart." -ForegroundColor Yellow
Write-Host " 2. Once the chart and table are fully loaded, " -ForegroundColor Yellow
Write-Host "    come back here and press Enter." -ForegroundColor Yellow
Write-Host "==================================================" -ForegroundColor Yellow
Read-Host "Press Enter to start the scraper script..."

# 3. Start Python Scraper
Write-Host "3. Launching Python Scraper Script..." -ForegroundColor Green
$env:PYTHONIOENCODING="utf-8"
& "$RootDir\venv_314\Scripts\python.exe" "$RootDir\tv_scraper.py"
