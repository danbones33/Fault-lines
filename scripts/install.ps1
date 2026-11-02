# FAULT LINES installer for Windows. start.cmd runs this, then starts the app.
# Safe to run again: each step is skipped when it is already done.
#   1. uv (installs Python and the app's packages for you)
#   2. MiroFish-Offline, the simulation layer, downloaded into .\mirofish (pinned version)
#   3. MiroFish's packages (the first time takes a few minutes and a few GB)
# Nothing here needs an API key. Keys are added from the app's Settings button.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$MiroFishCommit = "313fe642853ff9fff05e3ecae2e439886c2d29f4"
$MiroFishZip = "https://github.com/nikmcfly/MiroFish-Offline/archive/$MiroFishCommit.zip"

function Say($msg) { Write-Host "  $msg" }
function Step($msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "FAULT LINES setup" -ForegroundColor Cyan

# Windows limits file paths to 260 characters unless long paths are switched on. The engine's packages nest about
# 160 characters deep under the app folder, so a deep folder (a long Downloads or OneDrive path) breaks the install.
$longPaths = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled -eq 1
if (-not $longPaths -and $Root.Length -gt 95) {
    Write-Host ""
    Write-Host "This folder's path is too long for Windows ($($Root.Length) characters):" -ForegroundColor Red
    Write-Host "  $Root"
    Write-Host "Move the Fault-lines folder somewhere shorter, for example C:\FaultLines or straight onto your Desktop,"
    Write-Host "then double-click start.cmd again."
    exit 1
}

# 1. uv
Step "Checking for uv (it installs Python and the packages)"
$uvDir = Join-Path $env:USERPROFILE ".local\bin"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (Test-Path (Join-Path $uvDir "uv.exe")) {
        $env:Path = "$uvDir;$env:Path"
    } else {
        Say "Installing uv from astral.sh (one time)..."
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
        $env:Path = "$uvDir;$env:Path"
    }
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Could not install uv. Install it by hand from https://docs.astral.sh/uv/ and run start.cmd again." -ForegroundColor Red
    exit 1
}
Say "uv is ready"

# 2. MiroFish-Offline
Step "Checking for the simulation engine (MiroFish-Offline)"
$Inside = Join-Path $Root "mirofish\backend"
$Beside = Join-Path (Split-Path -Parent $Root) "mirofish\backend"
if ($env:MIROFISH_BACKEND -and (Test-Path $env:MIROFISH_BACKEND)) {
    $Backend = $env:MIROFISH_BACKEND
} elseif (Test-Path $Inside) {
    $Backend = $Inside
} elseif (Test-Path $Beside) {
    $Backend = $Beside
} else {
    Say "Downloading MiroFish-Offline (about 5 MB)..."
    $tmp = Join-Path $env:TEMP "faultlines-mirofish"
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $zip = Join-Path $tmp "mirofish.zip"
    Invoke-WebRequest -Uri $MiroFishZip -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Where-Object { $_.Name -like "MiroFish-Offline-*" } | Select-Object -First 1
    if (-not $src) { Write-Host "The MiroFish download looked wrong. Try again later." -ForegroundColor Red; exit 1 }
    Move-Item $src.FullName (Join-Path $Root "mirofish")
    Remove-Item -Recurse -Force $tmp
    $Backend = $Inside
}
Say "engine at $Backend"

# 3. MiroFish's packages
$venvPy = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Step "Installing the engine's packages (first time only: a few minutes, a few GB)"
    Push-Location $Backend
    try { uv sync } finally { Pop-Location }
    if (-not (Test-Path $venvPy)) { Write-Host "The engine's packages did not install. See the messages above." -ForegroundColor Red; exit 1 }
}
# one import of the simulation library (once; it takes ~30 s) catches a half-finished install: a full disk, a path too long
$okMark = Join-Path $Backend ".venv\.faultlines-ok"
if (-not (Test-Path $okMark)) {
    Say "checking the engine (once, about half a minute)..."
    # Windows PowerShell treats any stderr text from a program as an error under "Stop"; the libraries print warnings
    $ErrorActionPreference = "Continue"
    & $venvPy -W ignore -c "import oasis, camel, transformers.models.auto" *> $null
    $importCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($importCode -ne 0) {
        Write-Host "The engine's packages are incomplete. Delete the folder $(Join-Path $Backend '.venv') and run start.cmd again." -ForegroundColor Red
        Write-Host "If it happens again, move the Fault-lines folder to a short path such as C:\FaultLines." -ForegroundColor Red
        exit 1
    }
    Set-Content -Path $okMark -Value "ok" -Encoding ascii
}
Say "engine packages are ready"

Write-Host ""
Write-Host "Setup done. Starting FAULT LINES..." -ForegroundColor Green
exit 0
