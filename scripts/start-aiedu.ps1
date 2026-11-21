<#
AI Edu 2.0 一键启动入口（桌面快捷方式调用）。
默认开启付费真实调用；-NoPaid 可退回演示/禁付费模式。
设计边界：
  - 按前端构建的真实代理目标启动后端；显式端口不匹配时拒绝启动。
  - 幂等：核实 AI Edu 身份及任务 API 后复用；重复点击不重启服务。
  - 绝不停止任何既有服务（含整合轮 8010/8011/8012 隔离实例）。
  - 付费开关只作用于由本脚本启动的后端；复用既有实例时其状态未知，脚本会明确提示。
  - 日志、PID 一律写入工作区外 %LOCALAPPDATA%\AI Edu 2.0\launcher，不进 Git。
  - 浏览器只访问 127.0.0.1（前端有 Origin 白名单）。
#>
[CmdletBinding()]
param(
    [switch]$NoPaid,
    [switch]$NoBrowser,
    [switch]$CheckOnly,
    [int]$BackendPort = 8010,
    [int]$WebPort = 3001,
    [int]$TimeoutSec = 90
)

$ErrorActionPreference = 'Stop'

$ScriptDir      = $PSScriptRoot
$RepoRoot       = Split-Path -Parent $ScriptDir
$WebDir         = Join-Path $RepoRoot 'apps\web'
$BackendStarter = Join-Path $ScriptDir 'start-backend-v2.ps1'
$PrivateRoot    = Join-Path $env:LOCALAPPDATA 'AI Edu 2.0'
$PaidConfigRoot = $PrivateRoot
$ModelSettings  = Join-Path $PaidConfigRoot 'model-settings.json'
$RoleSettings   = Join-Path $PaidConfigRoot 'model-roles.private.json'
$PythonExe      = Join-Path $PrivateRoot 'venv\Scripts\python.exe'
$LauncherRoot   = Join-Path $PrivateRoot 'launcher'
$LogRoot        = Join-Path $LauncherRoot 'logs'
$LogFile        = Join-Path $LogRoot 'launcher.log'
$BackendPid     = Join-Path $LauncherRoot 'backend.pid'
$WebPid         = Join-Path $LauncherRoot 'web.pid'
$BackendOut     = Join-Path $LogRoot 'backend.out.log'
$BackendErr     = Join-Path $LogRoot 'backend.err.log'
$WebOut         = Join-Path $LogRoot 'web.out.log'
$WebErr         = Join-Path $LogRoot 'web.err.log'
$WebBackend     = Join-Path $LauncherRoot 'web.backend'
$RoutesManifest = Join-Path $WebDir '.next\routes-manifest.json'

foreach ($dir in @($LauncherRoot, $LogRoot)) {
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}

function Write-Log([string]$Level, [string]$Message) {
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Show-Notice([string]$Message) {
    # 仅在需要注意时弹窗；正常启动保持无窗口，体验接近普通应用。
    try {
        $shell = New-Object -ComObject WScript.Shell
        $null = $shell.Popup($Message, 12, 'AI Edu 2.0', 48)
    } catch { Write-Log 'WARN' "Popup unavailable: $Message" }
}

function Test-TcpPort([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(700, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch { return $false } finally { $client.Close() }
}

function Wait-ForHttp([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 -MaximumRedirection 5
            if ([int]$resp.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Milliseconds 700 }
    }
    return $false
}

function Test-TaskApi([string]$BaseUrl, [string]$Origin) {
    # 必须经真实会话读取任务列表，不能把首页 200 或 401 当作连通成功。
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $null = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v2/session" `
        -Headers @{ Origin = $Origin } -WebSession $session -ContentType 'application/json' -Body '{}' -TimeoutSec 5
    $caps = Invoke-RestMethod -Uri "$BaseUrl/api/v2/capabilities" -WebSession $session -TimeoutSec 5
    $tasks = Invoke-RestMethod -Uri "$BaseUrl/api/v2/tasks?limit=1" -WebSession $session -TimeoutSec 5
    if ($caps.schema_version -ne 'aiedu.task-run.v2' -or $null -eq $tasks.items) {
        throw 'Unexpected AI Edu task API response'
    }
    return $caps
}

function Wait-ForTaskApi([string]$BaseUrl, [string]$Origin, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    $lastError = 'not ready'
    while ((Get-Date) -lt $deadline) {
        try { return (Test-TaskApi $BaseUrl $Origin) }
        catch { $lastError = $_.Exception.Message; Start-Sleep -Milliseconds 700 }
    }
    throw "Task API not ready at ${BaseUrl}: $lastError"
}

function Resolve-NodeExe {
    $found = Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.Source }
    $candidates = @(
        (Join-Path $env:ProgramFiles 'nodejs\node.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe')
    )
    $wbNodeRoot = Join-Path $env:USERPROFILE '.workbuddy\binaries\node\versions'
    if (Test-Path -LiteralPath $wbNodeRoot) {
        $candidates += (Get-ChildItem -LiteralPath $wbNodeRoot -Directory -ErrorAction SilentlyContinue |
                        Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'node.exe' })
    }
    foreach ($c in $candidates) { if ($c -and (Test-Path -LiteralPath $c -PathType Leaf)) { return $c } }
    return $null
}

trap {
    Write-Log 'ERROR' $_.Exception.Message
    if (-not $CheckOnly -and -not $NoBrowser) {
        Show-Notice "AI Edu 启动未完成：`n$($_.Exception.Message)`n`n日志：$LogRoot"
    }
    exit 1
}

Write-Log 'INFO' "=== launch begin | paid=$(-not $NoPaid) | requestedBackend=$BackendPort web=$WebPort ==="

# ---- 前置校验 ------------------------------------------------------------
if (-not (Test-Path -LiteralPath $BackendStarter -PathType Leaf)) {
    Show-Notice "找不到后端启动脚本：`n$BackendStarter"; throw 'Backend starter missing'
}
if (-not (Test-Path -LiteralPath (Join-Path $WebDir '.next\BUILD_ID') -PathType Leaf)) {
    Show-Notice "前端尚未构建，请先在 github/apps/web 执行 npm run build。"; throw 'Web build missing'
}
$nodeExe = Resolve-NodeExe
if (-not $nodeExe) { Show-Notice '未找到 node.exe，请检查本机 Node 安装。'; throw 'Node missing' }
$nextBin = Join-Path $WebDir 'node_modules\next\dist\bin\next'
if (-not (Test-Path -LiteralPath $nextBin -PathType Leaf)) {
    Show-Notice '未找到 Next CLI，请在 github/apps/web 执行 npm install。'; throw 'Next CLI missing'
}

# Next 的 external rewrites 在 build 时冻结，next start 的环境变量不会更新它。
$manifest = Get-Content -LiteralPath $RoutesManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$rewrites = @($manifest.rewrites)
if ($manifest.rewrites.PSObject.Properties.Name -contains 'afterFiles') {
    $rewrites = @($manifest.rewrites.beforeFiles) + @($manifest.rewrites.afterFiles) + @($manifest.rewrites.fallback)
}
$apiRewrite = @($rewrites | Where-Object { $_.source -eq '/api/:path*' })
if ($apiRewrite.Count -ne 1 -or $apiRewrite[0].destination -notmatch '^http://127\.0\.0\.1:(\d+)/api/:path\*$') {
    throw 'Cannot determine the local backend from the web build; rebuild apps/web with API_BACKEND_URL'
}
$builtPort = [int]$Matches[1]
if ($PSBoundParameters.ContainsKey('BackendPort') -and $BackendPort -ne $builtPort) {
    throw "BackendPort $BackendPort conflicts with web build target $builtPort; rebuild apps/web with API_BACKEND_URL or omit -BackendPort"
}
$BackendPort = $builtPort
$backendUrl = "http://127.0.0.1:$BackendPort"
$webUrl = "http://127.0.0.1:$WebPort"
if ($BackendPort -eq $WebPort -or $BackendPort -lt 1024 -or $BackendPort -gt 65535 -or $WebPort -lt 1024 -or $WebPort -gt 65535) {
    throw 'Invalid or conflicting local ports'
}

# ---- 自检模式：只报告状态，不启动进程、不开浏览器、不弹窗 -----------------
if ($CheckOnly) {
    $backendUp = Test-TcpPort $BackendPort
    $webUp = Test-TcpPort $WebPort
    $result = [ordered]@{
        paidDefault       = (-not $NoPaid)
        backendPort       = $BackendPort
        buildBackendUrl   = $backendUrl
        backendListening  = $backendUp
        webPort           = $WebPort
        webListening      = $webUp
        privatePython     = (Test-Path -LiteralPath $PythonExe -PathType Leaf)
        paidModelConfig   = (Test-Path -LiteralPath $ModelSettings -PathType Leaf)
        paidRoleConfig    = (Test-Path -LiteralPath $RoleSettings -PathType Leaf)
        webBuild          = (Test-Path -LiteralPath (Join-Path $WebDir '.next\BUILD_ID') -PathType Leaf)
        nodeExe           = $nodeExe
        nextCli           = $nextBin
        browserUrl        = "http://127.0.0.1:$WebPort"
        logDir            = $LogRoot
        wouldReuseBackend = $backendUp
    }
    Write-Output '=== AI Edu 2.0 preflight (CheckOnly: nothing started) ==='
    foreach ($k in $result.Keys) { Write-Output ("{0,-20} {1}" -f $k, $result[$k]) }
    Write-Log 'INFO' "CheckOnly completed; no process started"
    return
}

if (-not $NoPaid) {
    if (-not (Test-Path -LiteralPath $ModelSettings -PathType Leaf) -or
        -not (Test-Path -LiteralPath $RoleSettings -PathType Leaf)) {
        throw "付费模型私有配置不完整：$PaidConfigRoot"
    }
    try {
        $modelConfig = Get-Content -LiteralPath $ModelSettings -Raw -Encoding UTF8 | ConvertFrom-Json
        $roleConfig = Get-Content -LiteralPath $RoleSettings -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw '付费模型私有配置无法解析，请检查私有 JSON 文件'
    }
    foreach ($required in @('generation', 'review', 'blind_solver')) {
        $binding = $modelConfig.model_roles.$required
        if (-not $binding) { $binding = $roleConfig.roles.$required }
        $profile = @($roleConfig.profiles + $modelConfig.profiles | Where-Object { $_.id -eq $binding } | Select-Object -Last 1)
        if (-not $binding -or $profile.Count -ne 1 -or -not $profile[0].api_key -or -not $profile[0].base_url -or -not $profile[0].model) {
            throw "付费模型角色配置不完整：$required"
        }
    }
}

# 私有配置路径通过环境变量传给本次后端，密钥内容不进入日志或仓库。
if (Test-Path -LiteralPath $ModelSettings -PathType Leaf) { $env:MODEL_SETTINGS_FILE = $ModelSettings }
if (Test-Path -LiteralPath $RoleSettings -PathType Leaf) { $env:AIEDU_ROLE_SETTINGS_FILE = $RoleSettings }
$env:DEMO_MODE = if ($NoPaid) { '1' } else { '0' }
$env:PYTHONIOENCODING = 'utf-8'
# Python HTTP 客户端读取代理环境变量，不自动读取 Windows 系统代理。
if (-not $env:HTTPS_PROXY -and -not $env:HTTP_PROXY -and -not $env:ALL_PROXY) {
    $internet = Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction SilentlyContinue
    if ($internet.ProxyEnable -eq 1 -and $internet.ProxyServer) {
        $systemProxy = [string]$internet.ProxyServer
        if ($systemProxy.Contains('=')) {
            $protocols = @{}
            foreach ($part in $systemProxy.Split(';')) {
                $pair = $part.Split('=', 2)
                if ($pair.Count -eq 2) { $protocols[$pair[0]] = $pair[1] }
            }
            $systemProxy = if ($protocols['https']) { $protocols['https'] } else { $protocols['http'] }
        }
        if ($systemProxy) {
            if ($systemProxy -notmatch '^\w+://') { $systemProxy = 'http://' + $systemProxy }
            $env:HTTPS_PROXY = $systemProxy
            $env:HTTP_PROXY = $systemProxy
        }
    }
}
$env:NO_PROXY = (@($env:NO_PROXY -split ',') + @('127.0.0.1', 'localhost', '::1') | Where-Object { $_ } | Select-Object -Unique) -join ','

# 持有独占文件句柄直至就绪；并发双击等待同一启动完成，防止重复拉起。
$launchLock = $null
$lockDeadline = (Get-Date).AddSeconds($TimeoutSec)
while (-not $launchLock) {
    try {
        $launchLock = [IO.File]::Open((Join-Path $LauncherRoot 'launch.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch [IO.IOException] {
        if ((Get-Date) -ge $lockDeadline) { throw 'Another AI Edu launch is still in progress; see launcher.log' }
        Start-Sleep -Milliseconds 300
    }
}
try {

# ---- 后端 ---------------------------------------------------------------
$backendHealth = "http://127.0.0.1:$BackendPort/api/health"
$backendReused = $false
if (Test-TcpPort $BackendPort) {
    $backendReused = $true
    Write-Log 'WARN' "Backend port $BackendPort already listening; reusing existing instance, NOT restarting it."
    $h = Invoke-RestMethod -Uri $backendHealth -TimeoutSec 5
    if ($h.app -ne 'AI Edu' -or $h.status -ne 'ok') { throw "Port $BackendPort is not a healthy AI Edu backend" }
} else {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        Show-Notice "找不到私有 Python 环境：`n$PythonExe`n`n请按 services/api/README-v2.md 建立 venv。"; throw 'Private venv missing'
    }
    $args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $BackendStarter), '-Port', "$BackendPort", '-Python', ('"{0}"' -f $PythonExe))
    if (-not $NoPaid) { $args += '-AllowPaid' }
    Write-Log 'INFO' "Starting backend: AIEDU_ALLOW_PAID=$(-not $NoPaid)"
    $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $args -WorkingDirectory $RepoRoot `
                -WindowStyle Hidden -PassThru -RedirectStandardOutput $BackendOut -RedirectStandardError $BackendErr
    Set-Content -LiteralPath $BackendPid -Value $proc.Id -Encoding ASCII
    Write-Log 'INFO' "Backend wrapper pid=$($proc.Id)"
}

if (-not (Wait-ForHttp $backendHealth $TimeoutSec)) {
    Show-Notice "后端在 $TimeoutSec 秒内未就绪，请查看日志：`n$LogRoot"; throw 'Backend not healthy'
}
Write-Log 'INFO' "Backend healthy at $backendHealth (reused=$backendReused)"
$backendCaps = Wait-ForTaskApi $backendUrl $webUrl $TimeoutSec
Write-Log 'INFO' "Backend v2 task API ready; paid=$($backendCaps.paid_calls_enabled) generationConfigured=$($backendCaps.role_configuration.generation.configured)"
if ($backendCaps.paid_calls_enabled -ne (-not $NoPaid)) {
    throw "已运行后端的付费开关为 $($backendCaps.paid_calls_enabled)，与启动参数不符；请在任务结束后停止桌面实例再启动"
}
if (-not $backendCaps.role_configuration.generation.configured) {
    if (-not $NoPaid) { throw '付费后端未加载生成模型配置，已阻止启动' }
    Write-Log 'WARN' 'Generation model is not configured; demo mode task browsing remains available.'
}
if (-not $NoPaid) {
    foreach ($role in @('review', 'blind_solver')) {
        if (-not $backendCaps.role_configuration.$role.configured) {
            throw "付费后端未加载 $role 模型配置，已阻止启动"
        }
    }
}
# ---- 前端 ---------------------------------------------------------------
$env:API_BACKEND_URL = $backendUrl
if (Test-TcpPort $WebPort) {
    Write-Log 'WARN' "Web port $WebPort already listening; reusing existing web server."
}

if (-not (Test-TcpPort $WebPort)) {
    $nextArgs = @(('"' + $nextBin + '"'), 'start', '-p', "$WebPort", '--hostname', '127.0.0.1')
    Write-Log 'INFO' "Starting web to backend ${backendUrl}: $nodeExe $($nextArgs -join ' ')"
    $webProc = Start-Process -FilePath $nodeExe -ArgumentList $nextArgs -WorkingDirectory $WebDir `
                -WindowStyle Hidden -PassThru -RedirectStandardOutput $WebOut -RedirectStandardError $WebErr
    Set-Content -LiteralPath $WebPid -Value $webProc.Id -Encoding ASCII
    Set-Content -LiteralPath $WebBackend -Value $backendUrl -Encoding ASCII
    Write-Log 'INFO' "Web pid=$($webProc.Id)"
}

if (-not (Wait-ForHttp "$webUrl/" $TimeoutSec)) { throw 'Web server did not become ready; see web.err.log' }
$webCaps = Wait-ForTaskApi $webUrl $webUrl $TimeoutSec
if ($webCaps.paid_calls_enabled -ne $backendCaps.paid_calls_enabled -or
    ($webCaps.role_configuration | ConvertTo-Json -Depth 10 -Compress) -ne ($backendCaps.role_configuration | ConvertTo-Json -Depth 10 -Compress)) {
    throw '前端代理的付费/模型角色与目标后端不一致，请检查运行中的前端构建'
}
Set-Content -LiteralPath $WebBackend -Value $backendUrl -Encoding ASCII
Write-Log 'INFO' "Web ready; authenticated task API verified through $webUrl -> $backendUrl"

# ---- 打开浏览器 ---------------------------------------------------------
if (-not $NoBrowser) {
    Start-Process $webUrl
    Write-Log 'INFO' "Opened browser at $webUrl"
}
Write-Log 'INFO' "=== launch done | paid=$(-not $NoPaid) | backendReused=$backendReused ==="
} finally {
    $launchLock.Dispose()
}
