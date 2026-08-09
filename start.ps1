param(
    [switch]$Foreground,
    [switch]$Restart,
    [string]$BindHost = "",
    [Nullable[int]]$Port = $null
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runtimeDir = Join-Path $projectRoot ".runtime"
$statePath = Join-Path $runtimeDir "server.json"
$stdoutPath = Join-Path $runtimeDir "server.stdout.log"
$stderrPath = Join-Path $runtimeDir "server.stderr.log"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Create .venv and install backend/requirements.txt first."
}

$fileConfig = @{}
$envPath = Join-Path $projectRoot ".env"
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath -Encoding utf8) {
        if ($line -match '^\s*([^#=\s]+)\s*=\s*(.*)\s*$') {
            $fileConfig[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
        }
    }
}

if (-not $BindHost) {
    $BindHost = if ($env:HOST) { $env:HOST } elseif ($fileConfig.HOST) { $fileConfig.HOST } else { "127.0.0.1" }
}
if ($BindHost -notin @("127.0.0.1", "localhost", "::1")) {
    throw "VideoToNo 1.0 is local-only. BindHost must be 127.0.0.1, localhost, or ::1."
}
if ($null -eq $Port) {
    $portValue = if ($env:PORT) { $env:PORT } elseif ($fileConfig.PORT) { $fileConfig.PORT } else { "8000" }
    $Port = [int]$portValue
}

if ($Restart -and (Test-Path -LiteralPath $statePath)) {
    & (Join-Path $projectRoot "stop.ps1") -Quiet
}

if (Test-Path -LiteralPath $statePath) {
    try {
        $state = Get-Content -LiteralPath $statePath -Raw -Encoding utf8 | ConvertFrom-Json
        $existing = Get-Process -Id ([int]$state.pid) -ErrorAction SilentlyContinue
        if ($existing) {
            Write-Host "VideoToNo is already running (PID $($state.pid)): $($state.url)" -ForegroundColor Green
            exit 0
        }
    } catch {
        # A stale or malformed state file is replaced below.
    }
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
}

$portBusy = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
    Where-Object { $_.Port -eq $Port }
if ($portBusy) {
    throw "Port $Port is already in use. Choose another port with .\start.ps1 -Port <port>."
}

Set-Location -LiteralPath $projectRoot
$arguments = @("-m", "uvicorn", "backend.main:app", "--host", $BindHost, "--port", [string]$Port)
$reloadValue = if ($env:RELOAD) { $env:RELOAD } elseif ($fileConfig.RELOAD) { $fileConfig.RELOAD } else { "false" }
$reloadEnabled = $reloadValue -eq "true"

if ($Foreground) {
    if ($reloadEnabled) { $arguments += "--reload" }
    Write-Host "Starting VideoToNo in foreground: http://${BindHost}:$Port" -ForegroundColor Cyan
    & $python @arguments
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$duplicatePath = @([System.Environment]::GetEnvironmentVariables().Keys | Where-Object { $_ -ceq "PATH" })
$savedUpperPath = if ($duplicatePath.Count) { $env:PATH } else { $null }
try {
    if ($duplicatePath.Count) {
        [System.Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    }
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -WindowStyle Hidden -PassThru
} finally {
    if ($duplicatePath.Count) {
        [System.Environment]::SetEnvironmentVariable("PATH", $savedUpperPath, "Process")
    }
}

$url = "http://${BindHost}:$Port"
$healthy = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if ($process.HasExited) { break }
    try {
        $response = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 1
        if ($response.status -eq "ok") {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 250
    }
}

if (-not $healthy) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    $details = if (Test-Path -LiteralPath $stderrPath) {
        (Get-Content -LiteralPath $stderrPath -Tail 8 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
    } else { "No server error log was produced." }
    throw "VideoToNo failed to start.`n$details"
}

$listenerPid = $process.Id
$listenerPattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
foreach ($line in netstat -ano) {
    if ($line -match $listenerPattern) {
        $listenerPid = [int]$matches[1]
        break
    }
}

$state = [ordered]@{
    pid = $listenerPid
    launcher_pid = $process.Id
    project_root = $projectRoot
    executable = $python
    url = $url
    started_at = (Get-Date).ToString("o")
}
$state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host "VideoToNo started (PID $listenerPid): $url" -ForegroundColor Green
Write-Host "Stop it with .\stop.ps1; logs are in .runtime\." -ForegroundColor DarkGray
