# Use D: and E: when C: is full (pip cache, temp, Playwright browsers).
# Run from project root: .\scripts\setup_use_d_and_e.ps1

$CacheRoot = "D:\cp-assistant-cache"
$PlaywrightPath = "D:\cp-assistant-cache\playwright-browsers"

# Prefer E: if D: does not exist or is full
if (-not (Test-Path "D:\")) { $CacheRoot = "E:\cp-assistant-cache"; $PlaywrightPath = "E:\cp-assistant-cache\playwright-browsers" }

New-Item -ItemType Directory -Force -Path "$CacheRoot\pip" | Out-Null
New-Item -ItemType Directory -Force -Path "$CacheRoot\tmp" | Out-Null
New-Item -ItemType Directory -Force -Path $PlaywrightPath | Out-Null

$env:PIP_CACHE_DIR = "$CacheRoot\pip"
$env:TEMP = "$CacheRoot\tmp"
$env:TMP = "$CacheRoot\tmp"
$env:PLAYWRIGHT_BROWSERS_PATH = $PlaywrightPath

Write-Host "Using D/E for caches:"
Write-Host "  PIP_CACHE_DIR = $env:PIP_CACHE_DIR"
Write-Host "  TEMP/TMP      = $env:TEMP"
Write-Host "  PLAYWRIGHT_BROWSERS_PATH = $env:PLAYWRIGHT_BROWSERS_PATH"
Write-Host ""

# Install core deps (pip will use PIP_CACHE_DIR on D/E)
Write-Host "Installing core dependencies..."
pip install -r requirements-core.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Optional: install Playwright and browsers on D/E
Write-Host ""
$install = Read-Host "Install Playwright and Chromium on D/E? (y/n)"
if ($install -eq "y") {
    pip install playwright
    playwright install chromium
}

Write-Host ""
Write-Host "Done. To run the app with D/E, use: .\scripts\run_with_de.ps1 api   or   .\scripts\run_with_de.ps1 scheduler"
