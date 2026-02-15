# Run CP Assistant with cache/browsers on D: (or E:). Usage: .\scripts\run_with_de.ps1 api | scheduler
# Example: .\scripts\run_with_de.ps1 api

$CacheRoot = "D:\cp-assistant-cache"
$PlaywrightPath = "D:\cp-assistant-cache\playwright-browsers"
if (-not (Test-Path "D:\")) { $CacheRoot = "E:\cp-assistant-cache"; $PlaywrightPath = "E:\cp-assistant-cache\playwright-browsers" }

$env:TEMP = "$CacheRoot\tmp"
$env:TMP = "$CacheRoot\tmp"
$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightPath

$cmd = $args[0]
if ($cmd -eq "api") {
    python run_api.py
} elseif ($cmd -eq "scheduler") {
    python run_scheduler.py
} else {
    Write-Host "Usage: .\scripts\run_with_de.ps1 api   or   .\scripts\run_with_de.ps1 scheduler"
    exit 1
}
