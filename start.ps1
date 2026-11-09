param([int]$Port = 8765)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$erpPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $erpPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Creating the Python environment failed." }
}
& $erpPython -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw "Installing dependencies failed." }
& $erpPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
& $erpPython manage.py init_demo
if ($LASTEXITCODE -ne 0) { throw "Demo initialization failed." }
Write-Host "Local ERP: http://127.0.0.1:$Port/"
Write-Host "Account details: $PSScriptRoot\LOCAL_ACCESS.txt"
& $erpPython manage.py runserver "127.0.0.1:$Port"
