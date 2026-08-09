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

# 版本元数据同步：从模板（scripts/version_info.txt.template）生成 version_info.txt，
# 版本号取自 launcher.py 的 VERSION，避免 exe 属性页版本号滞后
$versionInfoTemplate = Join-Path $projectRoot "scripts\version_info.txt.template"
$versionInfoPath = Join-Path $projectRoot "scripts\version_info.txt"
$templateText = Get-Content -LiteralPath $versionInfoTemplate -Raw -Encoding UTF8
$cleanVersion = $version -replace '[^0-9.].*$', ''
$versionParts = @(($cleanVersion -split '\.' | ForEach-Object { [int]$_ }) + @(0, 0, 0))[0..3]
$fileVersion = $versionParts -join '.'
$versionInfoText = $templateText `
    -replace '__VERSION_PARTS__', ($versionParts -join ', ') `
    -replace '__VERSION__', $fileVersion
[System.IO.File]::WriteAllText($versionInfoPath, $versionInfoText, [System.Text.Encoding]::UTF8)

if (-not $SkipInstall) {
    & $Python -m pip install --quiet pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller 安装失败" }
}

Set-Location -LiteralPath $projectRoot
& $Python -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name "VideoToNo" `
    --add-data "frontend;frontend" `
    --add-data "sources/icon.png;sources" `
    --add-data "sources/icon.ico;sources" `
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
# 固定名副本：桌面快捷方式指向它，升级后无需改快捷方式目标
$latest = Join-Path $projectRoot "dist\VideoToNo-portable.exe"
Copy-Item -LiteralPath (Join-Path $projectRoot "dist\VideoToNo.exe") -Destination $latest -Force
Remove-Item -LiteralPath (Join-Path $projectRoot "dist\VideoToNo.exe") -Force
Write-Host ""
Write-Host "构建完成: $target" -ForegroundColor Green
Write-Host "固定名（快捷方式可指向此文件）: $latest" -ForegroundColor Cyan
Write-Host "大小: $([math]::Round((Get-Item -LiteralPath $target).Length / 1MB, 1)) MB"
