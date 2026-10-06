# Builds everything: the camera driver, the app (PyInstaller) and the installer (Inno Setup).
# Output: dist\CamZoom-<version>-setup.exe
param([string]$Version = "1.0.0")
$ErrorActionPreference = 'Continue'  # native tools log to stderr; failures are caught via $LASTEXITCODE
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

& "$PSScriptRoot\build-driver.ps1"

$python = if (Test-Path "$root\.venv\Scripts\python.exe") { "$root\.venv\Scripts\python.exe" } else { "python" }
& $python -m PyInstaller --noconfirm --clean camzoom.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup 6 not found (https://jrsoftware.org/isinfo.php)" }
& $iscc "/DAppVersion=$Version" installer\camzoom.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }
