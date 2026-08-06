param(
    [string]$Python = "",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

if (-not $Python) {
    $Python = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python 解释器不存在: $Python"
}

$versionLine = Select-String -Path (Join-Path $projectRoot "launcher.py") -Pattern '^VERSION = "([^"]+)"' | Select-Object -First 1
if (-not $versionLine) { throw "无法从 launcher.py 读取 VERSION" }
$version = $versionLine.Matches[0].Groups[1].Value
Write-Host "打包版本: $version" -ForegroundColor Cyan

if (-not $SkipInstall) {
    & $Python -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller 安装失败" }
}

Set-Location -LiteralPath $projectRoot
& $Python -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name "VideoToNo" `
    --add-data "frontend;frontend" `
    --add-data "sources/icon.png;sources" `
    --collect-data faster_whisper `
    --collect-submodules mcp.server `
    --collect-data mcp `
    --version-file "scripts\version_info.txt" `
    --icon "sources\icon.ico" `
    --hidden-import pystray._win32 `
    --exclude-module tkinter `
    launcher.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

$target = Join-Path $projectRoot "dist\VideoToNo-$version-portable.exe"
Copy-Item -LiteralPath (Join-Path $projectRoot "dist\VideoToNo.exe") -Destination $target -Force
Remove-Item -LiteralPath (Join-Path $projectRoot "dist\VideoToNo.exe") -Force
Write-Host ""
Write-Host "构建完成: $target" -ForegroundColor Green
Write-Host "大小: $([math]::Round((Get-Item -LiteralPath $target).Length / 1MB, 1)) MB"
