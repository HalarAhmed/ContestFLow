# Start MongoDB for CP Assistant. Use after installing via: winget install MongoDB.Server
# If MongoDB is not in PATH, open a new terminal or reboot after install.

$dbpath = "D:\cp-assistant-cache\mongodb-data"
New-Item -ItemType Directory -Force -Path $dbpath | Out-Null

# Try Windows service first (default install creates "MongoDB" service)
$svc = Get-Service -Name "MongoDB" -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -ne "Running") {
        Start-Service MongoDB
        Write-Host "MongoDB service started."
    } else {
        Write-Host "MongoDB service already running."
    }
    exit 0
}

# Try mongod in PATH (after winget install you may need new terminal)
$mongod = Get-Command mongod -ErrorAction SilentlyContinue
if ($mongod) {
    Write-Host "Starting mongod with dbpath $dbpath ..."
    Start-Process -FilePath $mongod.Source -ArgumentList "--dbpath", "`"$dbpath`"" -WindowStyle Hidden
    Write-Host "MongoDB started in background. Wait a few seconds then run the app."
    exit 0
}

# Common install path for MongoDB 8.x
$exe = "C:\Program Files\MongoDB\Server\8.2\bin\mongod.exe"
if (Test-Path $exe) {
    Write-Host "Starting mongod from $exe ..."
    Start-Process -FilePath $exe -ArgumentList "--dbpath", "`"$dbpath`"" -WindowStyle Hidden
    Write-Host "MongoDB started. Wait a few seconds then run the app."
    exit 0
}

Write-Host "MongoDB not found. Install with: winget install MongoDB.Server"
Write-Host "Then open a new terminal and run this script again, or start MongoDB from Services (services.msc)."
