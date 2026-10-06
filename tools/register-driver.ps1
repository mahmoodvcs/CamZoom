# Registers (or with -Unregister, removes) the locally built CamZoom Camera driver, for development.
# The installer does this for end users. Asks for administrator rights.
param([switch]$Unregister)

$root = Split-Path $PSScriptRoot -Parent
$dlls = @(
    @{ Dll = "$root\build\driver-x64\Release\camzoom-camera64.dll"; RegSvr = "$env:WINDIR\System32\regsvr32.exe" },
    @{ Dll = "$root\build\driver-x86\Release\camzoom-camera32.dll"; RegSvr = "$env:WINDIR\SysWOW64\regsvr32.exe" }
)

$commands = foreach ($d in $dlls) {
    if (-not (Test-Path $d.Dll)) { throw "Not built yet: $($d.Dll). Run tools\build-driver.ps1 first." }
    Copy-Item "$root\driver\placeholder.png" (Split-Path $d.Dll) -Force  # shown when CamZoom isn't running
    $flag = if ($Unregister) { '/u ' } else { '' }
    "& '$($d.RegSvr)' /s $flag'$($d.Dll)'; if (`$LASTEXITCODE) { exit `$LASTEXITCODE }"
}

$p = Start-Process powershell -Verb RunAs -Wait -PassThru -WindowStyle Hidden `
    -ArgumentList '-NoProfile', '-Command', ($commands -join '; ')
if ($p.ExitCode) { throw "regsvr32 failed with exit code $($p.ExitCode)" }
Write-Host ("CamZoom Camera " + $(if ($Unregister) { 'unregistered' } else { 'registered' }))
