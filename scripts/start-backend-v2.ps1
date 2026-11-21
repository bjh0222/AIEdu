param(
    [int]$Port = 8010,
    [string]$Python = "$env:LOCALAPPDATA\AI Edu 2.0\venv\Scripts\python.exe",
    [switch]$AllowPaid
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
