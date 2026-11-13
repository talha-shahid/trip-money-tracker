# One-command setup for Windows (PowerShell): virtual environment, all packages, database and editable config.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (Test-Path .\.venv\Scripts\python.exe) {
    $existingVersion = & .\.venv\Scripts\python.exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -eq 0 -and $existingVersion -ne "3.12") {
        throw "The existing .venv uses Python $existingVersion. Remove it with 'Remove-Item -Recurse -Force .venv', then rerun this script."
    }
}

& py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12), 'Python 3.12 is required'"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.12 was not found. Install Python 3.12 and rerun this script."
}

& py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) {
    throw "Could not create the virtual environment."
}

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Could not upgrade pip."
}

& $venvPython -m pip install -e ".[all]"
if ($LASTEXITCODE -ne 0) {
    throw "Package installation failed. Fix the reported error and rerun this script."
}

& (Join-Path $PSScriptRoot ".venv\Scripts\tripmoney.exe") init
if ($LASTEXITCODE -ne 0) {
    throw "Trip Money initialization failed."
}

Write-Host ""
Write-Host "Installed. Start the app with:  .\start.ps1"
