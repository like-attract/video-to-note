param([switch]$Quiet)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$statePath = Join-Path $projectRoot ".runtime\server.json"
$expectedPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $statePath)) {
    if (-not $Quiet) { Write-Host "VideoToNo is not running (no PID state file)." -ForegroundColor DarkGray }
    exit 0
}

try {
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
    $recordedPid = [int]$state.pid
    $launcherPid = if ($state.PSObject.Properties.Name -contains "launcher_pid") {
        [int]$state.launcher_pid
    } else {
        $recordedPid
    }
} catch {
    throw "The runtime state file is malformed: $statePath"
}

if ($state.project_root -ne $projectRoot) {
    throw "The runtime state belongs to another project directory; refusing to stop its processes."
}

$launcher = Get-Process -Id $launcherPid -ErrorAction SilentlyContinue
if ($launcher) {
    $actualPath = $null
    try { $actualPath = $launcher.Path } catch { }
    if ($actualPath -and ([System.IO.Path]::GetFullPath($actualPath) -ne [System.IO.Path]::GetFullPath($expectedPython))) {
        throw "Launcher PID $launcherPid does not belong to this project's virtual environment; refusing to stop it."
    }
}

$listenerPid = $recordedPid
$listener = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
if ($listener) {
    $listenerInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
    $commandLine = if ($listenerInfo) { [string]$listenerInfo.CommandLine } else { "" }
    $hasExpectedCommand = $commandLine.IndexOf("backend.main:app", [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    $hasProjectPath = $commandLine.IndexOf($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    $isVerifiedChild = $listenerInfo -and [int]$listenerInfo.ParentProcessId -eq $launcherPid -and $launcher
    $belongsToProject = $hasExpectedCommand -and ($hasProjectPath -or $isVerifiedChild -or $listenerPid -eq $launcherPid)
    if (-not $belongsToProject) {
        throw "Listener PID $listenerPid cannot be verified as this VideoToNo instance; refusing to stop it."
    }
}
if (-not $launcher -and -not $listener) {
    Remove-Item -LiteralPath $statePath -Force
    if (-not $Quiet) { Write-Host "Removed stale VideoToNo PID state." -ForegroundColor DarkGray }
    exit 0
}

if ($listener) { Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue }
if ($launcher -and $launcherPid -ne $listenerPid) {
    Stop-Process -Id $launcherPid -Force -ErrorAction SilentlyContinue
}
Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
if (-not $Quiet) { Write-Host "VideoToNo stopped (PID $listenerPid)." -ForegroundColor Green }
