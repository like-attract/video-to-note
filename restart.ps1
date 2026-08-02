$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $projectRoot "stop.ps1") -Quiet
& (Join-Path $projectRoot "start.ps1") @args
