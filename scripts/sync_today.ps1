# ==============================================================================
# CONFIGURATION
# ==============================================================================
$vpsUser = "ujjwal"
$vpsHost = "187.127.138.189"
$vpsPath = "/home/ujjwal/src/IndexTimeMachine"
$localPath = "C:\src\antigravity\IndexTimeMachine"

# Target directories to check for updates today
$folders = @("logs", "trades", "reports", "data")

# ==============================================================================
# EXECUTION
# ==============================================================================
Write-Host "=============================================" -ForegroundColor Yellow
Write-Host " Syncing Today's Modified Files (Tar Batch)" -ForegroundColor Yellow
Write-Host "=============================================" -ForegroundColor Yellow

$localArchive = Join-Path $localPath "today_sync.tar.gz"
$foldersStr = $folders -join " "

# We use -f string formatting on a single-quoted string to insert variables.
# This changes to the project directory on the VPS before searching and archiving.
$sshCmd = 'ssh {0}@{1} "cd {2} && tar -czf - $(find {3} -type f -newermt ''00:00:00'' 2>/dev/null)" > "{4}"' -f $vpsUser, $vpsHost, $vpsPath, $foldersStr, $localArchive

Write-Host "Downloading today's changes (you will only enter your password ONCE)..." -ForegroundColor Cyan
cmd.exe /c $sshCmd

if (-not (Test-Path $localArchive) -or (Get-Item $localArchive).Length -lt 100) {
    Write-Host "No modified files were found today or the download failed." -ForegroundColor Green
    if (Test-Path $localArchive) { Remove-Item $localArchive }
    Write-Host "=============================================" -ForegroundColor Green
    exit
}

Write-Host "Extracting files locally..." -ForegroundColor Cyan
try {
    # Windows 10/11 has tar.exe built-in
    tar.exe -xzf $localArchive -C $localPath
    Remove-Item $localArchive
    Write-Host "=============================================" -ForegroundColor Green
    Write-Host " Sync Completed Successfully!" -ForegroundColor Green
    Write-Host "=============================================" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Failed to extract archive: $_" -ForegroundColor Red
}