param(
    [int]$Port = 8010,
    [string]$Python = "$env:LOCALAPPDATA\AI Edu 2.0\venv\Scripts\python.exe",
    [switch]$AllowPaid,
    # 经 Tailscale Serve 等方式访问时显式放行的主机/来源（逗号分隔），留空保持仅本机。
    [string]$AllowedHosts = "",
    [string]$AllowedOrigins = ""
)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1024 -or $Port -gt 65535) { throw 'Invalid local port' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Private Python environment not found; see services/api/README-v2.md'
}
$ApiRoot = Join-Path $PSScriptRoot '..\services\api'
$env:AIEDU_ALLOW_PAID = if ($AllowPaid) { '1' } else { '0' }
$env:AIEDU_ENABLE_LEGACY_API = '0'
$env:AIEDU_START_WORKER = '1'
# Never reuse the old instance/port or its database. Defaults are repository-external.
if ($AllowedHosts) { $env:AIEDU_V2_ALLOWED_HOSTS = $AllowedHosts } else { Remove-Item Env:AIEDU_V2_ALLOWED_HOSTS -ErrorAction SilentlyContinue }
if ($AllowedOrigins) { $env:AIEDU_V2_ALLOWED_ORIGIN_HOSTS = $AllowedOrigins } else { Remove-Item Env:AIEDU_V2_ALLOWED_ORIGIN_HOSTS -ErrorAction SilentlyContinue }
if ($AllowedOrigins) {
    $extraCors = ($AllowedOrigins -split ',' | ForEach-Object { "https://$($_.Trim())" }) -join ','
    $env:CORS_ORIGINS = "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:3001,http://localhost:3001,app://obsidian.md,capacitor://localhost,$extraCors"
} else { Remove-Item Env:CORS_ORIGINS -ErrorAction SilentlyContinue }
if ($env:DATABASE_URL -and $env:DATABASE_URL.Replace('\','/').Contains('/dsh/ai-edu/')) {
    throw 'Refusing the legacy workspace database'
}
Push-Location -LiteralPath $ApiRoot
try {
    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
