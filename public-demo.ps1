param([int]$Port = 8768)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use. Choose another port or stop your existing demo."
}
$erpPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $erpPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Environment setup failed." }
}
& $erpPython -m pip install -r requirements-demo.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
$env:ERP_DEMO_MODE = "1"
$env:ERP_DEBUG = "0"
foreach ($erpArguments in @(@("migrate", "--noinput"), @("init_demo"), @("collectstatic", "--noinput"))) {
    & $erpPython manage.py @erpArguments
    if ($LASTEXITCODE -ne 0) { throw "Demo initialization failed." }
}
New-Item -ItemType Directory -Path .tools,output -Force | Out-Null
$erpTunnelExe = Join-Path $PSScriptRoot ".tools\cloudflared.exe"
if (-not (Test-Path -LiteralPath $erpTunnelExe)) {
    Invoke-WebRequest -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $erpTunnelExe -UseBasicParsing
}
$erpTimestamp = Get-Date -Format "yyyyMMddHHmmssfff"
$erpTunnelLog = Join-Path $PSScriptRoot "output\tunnel-$erpTimestamp.log"
$erpTunnel = Start-Process -FilePath $erpTunnelExe -ArgumentList @("tunnel", "--url", "http://127.0.0.1:$Port", "--protocol", "http2", "--no-autoupdate") -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot "output\tunnel-$erpTimestamp.out.log") -RedirectStandardError $erpTunnelLog -PassThru
$erpPublicUrl = ""
for ($erpAttempt = 0; $erpAttempt -lt 45; $erpAttempt++) {
    if (Test-Path -LiteralPath $erpTunnelLog) {
        $erpLogText = [string](Get-Content -LiteralPath $erpTunnelLog -Raw)
        $erpPublicUrl = [regex]::Match($erpLogText, "https://[a-z0-9-]+\.trycloudflare\.com").Value
    }
    if ($erpPublicUrl) { break }
    if ($erpTunnel.HasExited) { throw "Tunnel could not start. See $erpTunnelLog" }
    Start-Sleep -Seconds 1
}
if (-not $erpPublicUrl) { throw "Timed out waiting for a public URL. See $erpTunnelLog" }
$erpHostnames = ([uri]$erpPublicUrl).Host + ",127.0.0.1,localhost"
$erpServer = Start-Process -FilePath $erpPython -ArgumentList @("scripts/serve_demo.py", "--port", "$Port", "--https-proxy", "--hostnames", $erpHostnames) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot "output\demo-$erpTimestamp.out.log") -RedirectStandardError (Join-Path $PSScriptRoot "output\demo-$erpTimestamp.err.log") -PassThru
$erpReady = $false
for ($erpAttempt = 0; $erpAttempt -lt 20; $erpAttempt++) {
    try {
        $erpHealth = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health/" -UseBasicParsing -TimeoutSec 2
        if ($erpHealth.StatusCode -eq 200) { $erpReady=$true; break }
    } catch {}
    if ($erpServer.HasExited) { break }
    Start-Sleep -Seconds 1
}
if (-not $erpReady) { throw "The application did not become ready. See output/demo-$erpTimestamp.err.log" }
@{url=$erpPublicUrl;pid=$erpServer.Id;tunnel_pid=$erpTunnel.Id;port=$Port} | ConvertTo-Json | Set-Content -LiteralPath output\public-demo.json
Write-Host "Temporary online demo: $erpPublicUrl"
Write-Host "Viewer: demo / HexuDemo2026!"
Write-Host "This URL requires this computer and both background processes to stay running."
