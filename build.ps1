# Local build: .\build.ps1   ->  dist\NiimbotStudio\ (app) and, if Inno Setup is installed, dist\NiimbotStudio-Setup-<version>.exe
# Release builds normally come from GitHub Actions (push a vX.Y.Z tag).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    python -m venv .venv
    & $py -m pip install --upgrade pip
    & $py -m pip install -r requirements.txt pyinstaller
}

$version = & $py tools\write_version_info.py
Write-Host "Building Niimbot Studio $version"
& $py -m PyInstaller --noconfirm NiimbotStudio.spec
if ($LASTEXITCODE) { throw "PyInstaller failed" }

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc /Qp "/DAppVersion=$version" installer\NiimbotStudio.iss
    if ($LASTEXITCODE) { throw "ISCC failed" }
    Write-Host "Installer: dist\NiimbotStudio-Setup-$version.exe"
} else {
    Write-Host "Inno Setup not found; skipped the installer. App folder: dist\NiimbotStudio"
}
