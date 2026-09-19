$ErrorActionPreference = "Stop"

$repo = (Resolve-Path $PSScriptRoot).Path
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python -ErrorAction Stop
}
$python = $pythonCommand.Source
$bootstrap = Join-Path $repo "scheduler_bootstrap.ps1"
$taskPrefix = "PriconnerTlScanner-"
$taskName = "$taskPrefix-Bootstrap"
$oldTaskNames = @("YouTubeSearch", "DiscordChannel", "WorryChefs", "YouTubeChannel")
foreach ($oldTaskName in $oldTaskNames) {
    schtasks.exe /Delete /TN "$taskPrefix$oldTaskName" /F 2>$null | Out-Null
}
$taskCommand = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$bootstrap`""
schtasks.exe /Create `
    /TN $taskName `
    /TR $taskCommand `
    /SC MONTHLY `
    /D 20-30 `
    /ST 12:00 `
    /RU "$env:USERDOMAIN\$env:USERNAME" `
    /RL LIMITED `
    /F | Out-Null

Write-Host "Registered the Priconner TL scanner bootstrap task for $env:USERDOMAIN\$env:USERNAME"
