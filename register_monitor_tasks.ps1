$ErrorActionPreference = "Stop"

$gitRoot = Split-Path -Parent $PSScriptRoot
$centralScript = Join-Path $gitRoot "priconner_clan_battle_task_scheduler\register_scheduler.ps1"
if (-not (Test-Path -LiteralPath $centralScript -PathType Leaf)) {
    throw "Central scheduler was not found: $centralScript"
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $centralScript
if ($LASTEXITCODE -ne 0) {
    throw "Central scheduler registration failed with exit code $LASTEXITCODE."
}

Write-Host "Delegated schedule registration to $centralScript"
