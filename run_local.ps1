# Run CP Assistant on localhost. Uses port 8000 unless PORT is set.
# If port 8000 is in use: $env:PORT=8001; .\run_local.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$port = if ($env:PORT) { $env:PORT } else { "8000" }
Write-Host "Starting CP Assistant on http://localhost:$port (Ctrl+C to stop)"
Write-Host "Dashboard: http://localhost:$port"
python run_api.py
