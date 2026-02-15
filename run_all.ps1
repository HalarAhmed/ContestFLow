# Run CP Assistant (API + Scheduler). Uses D: for temp and Playwright.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$env:TEMP = "D:\cp-assistant-cache\tmp"
$env:TMP = "D:\cp-assistant-cache\tmp"
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\cp-assistant-cache\playwright-browsers"

$pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonExe) {
    Write-Host "Python not found in PATH. Install Python and try again."
    exit 1
}
Write-Host "Starting API on http://localhost:8000 ..."
Start-Process -FilePath $pythonExe -ArgumentList "run_api.py" -WindowStyle Hidden -WorkingDirectory $PSScriptRoot
$maxWait = 35
$waited = 0
while ($waited -lt $maxWait) {
    Start-Sleep -Seconds 1
    $waited++
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000/api/health" -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        if ($r.StatusCode -eq 200) {
            Write-Host "API is up (after ${waited}s). Waiting for data fetch..."
            break
        }
    } catch {}
    if ($waited -ge $maxWait) {
        Write-Host "API did not respond. Opening browser anyway - you may see a loading page. Check port 8000."
        break
    }
}
# Give background thread time to fill overview cache (Codeforces + LeetCode take ~15-25s)
Start-Sleep -Seconds 25
Write-Host "Starting Scheduler ..."
Start-Process -FilePath $pythonExe -ArgumentList "run_scheduler.py" -WindowStyle Hidden -WorkingDirectory $PSScriptRoot
Write-Host "Opening http://localhost:8000"
Write-Host "If you see 'Loading your data', wait a few seconds and the page will refresh with your profiles."
Start-Process "http://localhost:8000"
