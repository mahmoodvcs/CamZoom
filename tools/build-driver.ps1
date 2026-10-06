# Builds the CamZoom Camera driver DLLs (64- and 32-bit) into build\driver-x64 and build\driver-x86.
# Needs Visual Studio (or Build Tools) with the "Desktop development with C++" workload.
$ErrorActionPreference = 'Continue'  # native tools log to stderr; failures are caught via $LASTEXITCODE
$root = Split-Path $PSScriptRoot -Parent

$cmake = (Get-Command cmake -ErrorAction SilentlyContinue).Source
if (-not $cmake) {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    $cmake = "$vs\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
}

foreach ($arch in @(@{ Dir = 'x64'; Platform = 'x64' }, @{ Dir = 'x86'; Platform = 'Win32' })) {
    $build = "$root\build\driver-$($arch.Dir)"
    & $cmake -S "$root\driver" -B $build -A $arch.Platform
    if ($LASTEXITCODE) { throw "cmake configure failed ($($arch.Dir))" }
    & $cmake --build $build --config Release
    if ($LASTEXITCODE) { throw "cmake build failed ($($arch.Dir))" }
}
