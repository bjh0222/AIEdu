<#
从品牌 SVG 复现生成 Windows 快捷方式用的 .ico。
源图：设计/品牌视觉/logo定稿/ai-edu-script-edu.svg（只读，不修改）
输出：%LOCALAPPDATA%\AI Edu 2.0\ai-edu.ico（工作区外，不进 Git）

依赖：Chrome 或 Edge（headless 渲染 SVG）+ 任一装有 Pillow 的 Python。
用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-aiedu-icon.ps1
#>
[CmdletBinding()]
param(
    [string]$SvgPath = (Join-Path $PSScriptRoot '..\..\设计\品牌视觉\logo定稿\ai-edu-script-edu.svg'),
    [string]$IcoPath = (Join-Path $env:LOCALAPPDATA 'AI Edu 2.0\ai-edu.ico')
)

$ErrorActionPreference = 'Continue'

if (-not (Test-Path -LiteralPath $SvgPath -PathType Leaf)) { throw "SVG not found: $SvgPath" }
$svgFull = (Resolve-Path -LiteralPath $SvgPath).Path

$browsers = @(
    'C:\Program Files\Google\Chrome\Application\chrome.exe',
    'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
)
$browser = $browsers | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $browser) { throw 'Chrome/Edge not found; cannot rasterize SVG.' }

# 找一个带 Pillow 的 Python（系统 Python 优先，其次项目私有 venv）
$pyCandidates = @(
    (Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source,
    (Join-Path $env:LOCALAPPDATA 'AI Edu 2.0\venv\Scripts\python.exe')
)
$python = $null
foreach ($c in $pyCandidates) {
    if (-not $c -or -not (Test-Path -LiteralPath $c -PathType Leaf)) { continue }
    & $c -c 'import PIL' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $python = $c; break }
}
if (-not $python) { throw 'No Python with Pillow found; cannot build ICO.' }

$tmp = Join-Path $env:TEMP 'aiedu-icon-build'
if (-not (Test-Path -LiteralPath $tmp)) { New-Item -ItemType Directory -Path $tmp -Force | Out-Null }
$png     = Join-Path $tmp 'ai-edu-512.png'
$html    = Join-Path $tmp 'icon.html'
$profile = Join-Path $tmp 'chrome-profile-icon'

$svgUri = ([uri]$svgFull).AbsoluteUri
$htmlContent = '<!doctype html><html><head><meta charset="utf-8"><style>' +
               'html,body{margin:0;padding:0;width:512px;height:512px;background:transparent}' +
               'img{display:block;width:512px;height:512px}</style></head><body>' +
               '<img src="' + $svgUri + '"></body></html>'
[IO.File]::WriteAllText($html, $htmlContent, (New-Object Text.UTF8Encoding($false)))
$htmlUri = ([uri]$html).AbsoluteUri

if (Test-Path -LiteralPath $png) { Remove-Item -LiteralPath $png -Force }

# Chrome 会往 stderr 写扩展注册表告警，不能让 $ErrorActionPreference='Stop' 把它变成终止错误。
$chromeArgs = @(
    '--headless=new', '--disable-gpu', '--no-sandbox', '--no-first-run',
    '--no-default-browser-check', '--disable-extensions', '--hide-scrollbars',
    '--force-device-scale-factor=1', '--window-size=512,512',
    '--default-background-color=00000000',
    ('--user-data-dir="{0}"' -f $profile),
    ('--screenshot="{0}"' -f $png),
    ('"{0}"' -f $htmlUri)
)
& $browser $chromeArgs 2>$null | Out-Null
if (-not (Test-Path -LiteralPath $png -PathType Leaf)) { throw "Rasterization failed: $png not produced" }

$icoDir = Split-Path -Parent $IcoPath
if (-not (Test-Path -LiteralPath $icoDir)) { New-Item -ItemType Directory -Path $icoDir -Force | Out-Null }

$pyCode = @'
from PIL import Image
import sys, os
src, dst = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGBA")
if im.size != (512, 512):
    raise SystemExit("unexpected raster size: %s" % (im.size,))
if im.getpixel((0, 0))[3] != 0:
    raise SystemExit("corner is not transparent; rounded corners lost")
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
im.save(dst, format="ICO", sizes=sizes)
print("ico=%s bytes=%d sizes=%s" % (dst, os.path.getsize(dst), sorted(Image.open(dst).ico.sizes())))
'@
$pyFile = Join-Path $tmp 'make_ico.py'
[IO.File]::WriteAllText($pyFile, $pyCode, (New-Object Text.UTF8Encoding($false)))
& $python $pyFile $png $IcoPath
if ($LASTEXITCODE -ne 0) { throw 'ICO build failed' }
