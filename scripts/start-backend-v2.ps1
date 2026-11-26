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
# 参数未提供时继承用户级环境变量（setx AIEDU_V2_ALLOWED_HOSTS 等），不清除——
# 否则任何不带参数的重载都会把 tailnet 放行丢掉。当前 shell 环境缺失时再从注册表用户级读取，
# 兼容在 setx 之前启动的陈旧 shell 里执行本脚本的情况。
if (-not $env:AIEDU_V2_ALLOWED_HOSTS) {
    $u = [Environment]::GetEnvironmentVariable("AIEDU_V2_ALLOWED_HOSTS", "User")
    if ($u) { $env:AIEDU_V2_ALLOWED_HOSTS = $u }
}
if (-not $env:AIEDU_V2_ALLOWED_ORIGIN_HOSTS) {
    $u3 = [Environment]::GetEnvironmentVariable("AIEDU_V2_ALLOWED_ORIGIN_HOSTS", "User")
    if ($u3) { $env:AIEDU_V2_ALLOWED_ORIGIN_HOSTS = $u3 }
}
if (-not $env:CORS_ORIGINS) {
    $u2 = [Environment]::GetEnvironmentVariable("CORS_ORIGINS", "User")
    if ($u2) { $env:CORS_ORIGINS = $u2 }
}
if ($AllowedHosts) { $env:AIEDU_V2_ALLOWED_HOSTS = $AllowedHosts }
if ($AllowedOrigins) {
    $env:AIEDU_V2_ALLOWED_ORIGIN_HOSTS = $AllowedOrigins
    $extraCors = ($AllowedOrigins -split ',' | ForEach-Object { "https://$($_.Trim())" }) -join ','
    $env:CORS_ORIGINS = "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:3001,http://localhost:3001,app://obsidian.md,capacitor://localhost,$extraCors"
}
if ($env:DATABASE_URL -and $env:DATABASE_URL.Replace('\','/').Contains('/dsh/ai-edu/')) {
    throw 'Refusing the legacy workspace database'
}
Push-Location -LiteralPath $ApiRoot
try {
    # 后端只绑 127.0.0.1，唯一合法上游是本机 Next 代理；不信任 X-Forwarded-*，
    # 否则经 Tailscale Serve 访问时 uvicorn 会把 client 视为原始设备 IP，破坏本机会话边界。
    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --no-proxy-headers
    if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
