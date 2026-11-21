<#
AI Edu 2.0 一键停止入口。
只停止由 start-aiedu.ps1 启动、且 PID 文件中有记录的进程树。
绝不停止复用进来的既有实例（8010/8011/8012 等整合轮服务）。
PID 可能被系统复用，因此每次都校验进程命令行与端口后再操作。
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8010,
    [int]$WebPort = 3001
)

$ErrorActionPreference = 'Stop'
$PrivateRoot  = Join-Path $env:LOCALAPPDATA 'AI Edu 2.0'
$LauncherRoot = Join-Path $PrivateRoot 'launcher'
$LogFile      = Join-Path (Join-Path $LauncherRoot 'logs') 'launcher.log'
$RepoRoot     = Split-Path -Parent $PSScriptRoot

function Write-Log([string]$Level, [string]$Message) {
    if (-not (Test-Path -LiteralPath (Split-Path -Parent $LogFile))) { return }
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Get-MatchingProcess([int]$ProcessId, [string]$CommandPath, [int]$Port, [string]$PortFlag) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $p) { return $null }
    $cmd = [string]$p.CommandLine
    if ($cmd.IndexOf($CommandPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
        $cmd -match ('(?:^|\s)' + [regex]::Escape($PortFlag) + '\s+"?' + $Port + '"?(?:\s|$)')) { return $p }
    return $null
}

function Stop-Descendants([int]$ProcessId) {
    # Windows venv 的 python.exe 还有一层实际解释器；必须从叶子回收整条链。
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        Stop-Descendants $child.ProcessId
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.ProcessId)" -ErrorAction SilentlyContinue
        if ($current -and $current.CreationDate -eq $child.CreationDate) {
            Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Stop-OwnedTree([string]$PidFile, [string]$CommandPath, [int]$Port, [string]$PortFlag, [string]$Label) {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        Write-Log 'INFO' "$Label : no pid file, nothing to stop (reused instance is left untouched)"
        return
    }
    $raw = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue).Trim()
    $ownerPid = 0
    if (-not [int]::TryParse($raw, [ref]$ownerPid)) {
        Write-Log 'WARN' "$Label : invalid pid file, removing"
        Remove-Item -LiteralPath $PidFile -Force
        return
    }
    $owner = Get-MatchingProcess $ownerPid $CommandPath $Port $PortFlag
    if (-not $owner) {
        Write-Log 'WARN' "$Label : pid $ownerPid no longer matches our launcher (gone or reused by another process); leaving it untouched and clearing pid file"
        Remove-Item -LiteralPath $PidFile -Force
        return
    }

    # 先停后代（uvicorn / next 子进程），再停包装进程。
    Stop-Descendants $ownerPid
    Write-Log 'INFO' "$Label : stopping owner pid=$ownerPid"
    Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PidFile -Force
}

Write-Log 'INFO' '=== stop begin ==='
Stop-OwnedTree (Join-Path $LauncherRoot 'backend.pid') (Join-Path $PSScriptRoot 'start-backend-v2.ps1') $BackendPort '-Port' 'backend'
Stop-OwnedTree (Join-Path $LauncherRoot 'web.pid') (Join-Path $RepoRoot 'apps\web\node_modules\next\dist\bin\next') $WebPort '-p' 'web'

foreach ($port in @($BackendPort, $WebPort)) {
    $still = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($still) {
        Write-Log 'WARN' "port $port still listening after stop; likely an independent pre-existing instance (NOT stopped by this script)"
    }
}
Write-Log 'INFO' '=== stop done ==='
