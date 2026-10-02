param(
    [string]$PythonExe = "python",
    [string]$AppName = "api-client",
    [switch]$InstallPyInstaller
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$entry = Join-Path $root "APIClient.pyw"
if (-not (Test-Path -LiteralPath $entry)) {
    throw "Entry file not found: $entry"
}

if ($InstallPyInstaller) {
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install pyinstaller
}

# Fail fast if pyinstaller is not available.
& $PythonExe -m PyInstaller --version | Out-Null

$iconPath = Join-Path $root "assetspp.ico"
if (-not (Test-Path -LiteralPath $iconPath)) {
    & $PythonExe (Join-Path $root "make_icon.py")
    if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }
}

$releaseDir = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

$docsArg = "$(Join-Path $root 'docs');docs"

& $PythonExe -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $AppName `
    --icon $iconPath `
    --distpath $releaseDir `
    --workpath (Join-Path $root "build") `
    --specpath $root `
    --add-data $docsArg `
    $entry

Write-Host "Build complete. EXE: $(Join-Path $releaseDir ($AppName + '.exe'))"
