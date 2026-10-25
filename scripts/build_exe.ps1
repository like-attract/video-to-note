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

# VC++ 运行时的取源目录。PyInstaller 默认收构建机 System32 那一份，而包内 DLL 在搜索顺序上
# 盖过用户自己的 System32 —— 本机 System32 的 base 是 2016 年的 14.00，收进包后 ctranslate2
# 的全局 std::mutex 由旧实现来锁，转写必崩 0xC0000005（取证见 dev-notes/asr-worker-process.md）。
# 所以这里主动挑一份 14.10+ 的 base，用 --add-binary 覆盖（已用实验包验明 --add-binary 赢过
# 自动收集的同名条目）。CI 上没有 conda，会落到 System32，行为与从前一致。
$vcRuntimeCandidates = @()
if ($env:VIDEOTONOTES_VC_RUNTIME_DIR) { $vcRuntimeCandidates += $env:VIDEOTONOTES_VC_RUNTIME_DIR }
$basePrefix = (& $Python -c "import sys; print(sys.base_prefix)").Trim()
$vcRuntimeCandidates += (Join-Path $basePrefix "Library\bin")
$vcRuntimeCandidates += $basePrefix
$vcRuntimeCandidates += (Join-Path $env:SystemRoot "System32")

$vcRuntimeDir = $null
foreach ($dir in $vcRuntimeCandidates) {
    if (-not $dir -or -not (Test-Path -LiteralPath $dir)) { continue }
    $baseDll = Join-Path $dir "msvcp140.dll"
    $crtDll = Join-Path $dir "vcruntime140.dll"
    if (-not (Test-Path -LiteralPath $baseDll)) { continue }
    if (-not (Test-Path -LiteralPath $crtDll)) { continue }
    $vcVersion = (Get-Item -LiteralPath $baseDll).VersionInfo.FileVersion
    if ($vcVersion -notmatch '^(\d+)\.(\d+)') { continue }
    if ([int]$Matches[1] -gt 14 -or ([int]$Matches[1] -eq 14 -and [int]$Matches[2] -ge 10)) {
        $vcRuntimeDir = $dir
        Write-Host ("包内 C++ 运行时取自 {0}（msvcp140.dll {1}）" -f $dir, $vcVersion) -ForegroundColor Cyan
        break
    }
    Write-Host ("  跳过 {0}：msvcp140.dll {1} 低于 14.10" -f $dir, $vcVersion) -ForegroundColor DarkGray
}
if (-not $vcRuntimeDir) {
    throw ("候选目录里没有 14.10+ 的 msvcp140.dll / vcruntime140.dll（查过：" +
           ($vcRuntimeCandidates -join "、") +
           "）。装一份 VC++ 2015-2022 可再发行组件，或用 VIDEOTONOTES_VC_RUNTIME_DIR 指个目录。")
}

# 取源目录里存在的这几个全用 --add-binary 钉死，让包内是同一代运行时——实验包就是这么打的，
# 产物与验过的字节保持一致才作数（缺的文件不传，PyInstaller 对不存在的源会直接报错）。
$vcAdd = @()
foreach ($dllName in @("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
                       "vcruntime140.dll", "vcruntime140_1.dll")) {
    $source = Join-Path $vcRuntimeDir $dllName
    if (Test-Path -LiteralPath $source) {
        $vcAdd += "--add-binary"
        $vcAdd += "$source;."
    } else {
        Write-Host ("  {0} 在取源目录里没有，按 PyInstaller 自动收集" -f $dllName) -ForegroundColor DarkGray
    }
}

# 整条命令行拼成数组再 splat：PyInstaller 的 --add-binary 条数取决于该目录里有哪些伴生 DLL，
# 循环拼数组比反引号续行少一个坑（续行漏一个反引号就整行掉参数）。
# 用 `+=` 而不是把 $vcAdd 塞进数组字面量：字面量里的数组会成为嵌套元素，只有 splat 时才摊平，
# 中间任何一步想数元素个数都会数错。
$pyiArgs = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole",
    "--name", "VideoToNo",
    "--add-data", "frontend;frontend",
    "--add-data", "sources/icon.png;sources",
    "--add-data", "sources/icon.ico;sources"
)
$pyiArgs += $vcAdd
$pyiArgs += @(
    "--collect-data", "faster_whisper",
    "--collect-data", "docx",
    "--collect-all", "sherpa_onnx",
    "--collect-submodules", "mcp.server",
    "--collect-data", "mcp",
    "--version-file", "scripts\version_info.txt",
    "--icon", "sources\icon.ico",
    "--hidden-import", "pystray._win32",
    "--exclude-module", "tkinter",
    "launcher.py"
)
& $Python @pyiArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

$target = Join-Path $projectRoot "dist\VideoToNo-$version-portable.exe"
Copy-Item -LiteralPath (Join-Path $projectRoot "dist\VideoToNo.exe") -Destination $target -Force
# 固定名副本：桌面快捷方式指向它，升级后无需改快捷方式目标
$latest = Join-Path $projectRoot "dist\VideoToNo-portable.exe"
Copy-Item -LiteralPath (Join-Path $projectRoot "dist\VideoToNo.exe") -Destination $latest -Force
Remove-Item -LiteralPath (Join-Path $projectRoot "dist\VideoToNo.exe") -Force

# 收尾再量一次产物里的 VC++ 运行时版本，作为上面取源的兜底：包内 DLL 在搜索顺序上盖过用户
# 自己的 System32，一份 2016 年的 base 会让每个用户的转写都崩 0xC0000005（2026-10 群测，
# 崩点 msvcp140!mtx_do_lock，取证见 dev-notes/asr-worker-process.md）。
# 门槛定 14.10：msvcp140_1.dll 这个组件从 VS2017 15.3 才有，base 比 _1 老一整条产品线是
# 不被支持的组合。宁可构建红叉，也不要发一个"用户一点就静默消失、日志一行不剩"的包。
$runtimeDir = Join-Path $projectRoot ".tmp\bundle-runtimes"
& $Python (Join-Path $projectRoot "scripts\extract_bundle_runtimes.py") $target --out $runtimeDir
if ($LASTEXITCODE -ne 0) { throw "抽不出包内运行时，无法判断版本（PyInstaller 可能没收到）" }
foreach ($dllName in @("msvcp140.dll", "vcruntime140.dll")) {
    $dllPath = Join-Path $runtimeDir $dllName
    if (-not (Test-Path -LiteralPath $dllPath)) { throw "包里没有 $dllName" }
    $version = (Get-Item -LiteralPath $dllPath).VersionInfo.FileVersion
    if ($version -notmatch '^(\d+)\.(\d+)') { throw "$dllName 版本号读不出来：$version" }
    Write-Host ("  包内运行时 {0} -> {1}" -f $dllName, $version)
    if ([int]$Matches[1] -lt 14 -or ([int]$Matches[1] -eq 14 -and [int]$Matches[2] -lt 10)) {
        throw ("包内 $dllName 是 $version，低于 14.10（取源目录 $vcRuntimeDir 没生效）。" +
               "这个包会在转写时崩掉用户的任务：先查上面的取源日志，必要时用 " +
               "VIDEOTONOTES_VC_RUNTIME_DIR 指一份 14.10+ 的运行时再重打。")
    }
}

Write-Host ""
Write-Host "构建完成: $target" -ForegroundColor Green
Write-Host "固定名（快捷方式可指向此文件）: $latest" -ForegroundColor Cyan
Write-Host "大小: $([math]::Round((Get-Item -LiteralPath $target).Length / 1MB, 1)) MB"
