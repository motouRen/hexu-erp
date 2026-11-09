param([int]$Port = 8767)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:ERP_DEMO_MODE = "1"
$env:ERP_DEBUG = "1"
$erpPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $erpPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Creating the Python environment failed." }
}
& $erpPython -m pip install -r requirements-demo.txt
if ($LASTEXITCODE -ne 0) { throw "Installing dependencies failed." }
& $erpPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
& $erpPython manage.py init_demo
if ($LASTEXITCODE -ne 0) { throw "Demo initialization failed." }
Write-Host "Local isolated demo: http://127.0.0.1:$Port/"
Write-Host "Account: demo / HexuDemo2026! (read-only)"
& $erpPython scripts/serve_demo.py --port $Port
