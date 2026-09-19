$ErrorActionPreference = "Stop"

$repo = (Resolve-Path $PSScriptRoot).Path
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python -ErrorAction Stop
}
$python = $pythonCommand.Source
$bootstrap = Join-Path $repo "scheduler_bootstrap.ps1"
$taskPrefix = "PriconnerTlScanner-"
$oldTaskNames = @("YouTubeSearch", "DiscordChannel", "WorryChefs", "YouTubeChannel")
foreach ($oldTaskName in $oldTaskNames) {
    Unregister-ScheduledTask -TaskName "$taskPrefix$oldTaskName" -TaskPath "\" -Confirm:$false -ErrorAction SilentlyContinue
}
Unregister-ScheduledTask -TaskName "$taskPrefix-Bootstrap" -TaskPath "\" -Confirm:$false -ErrorAction SilentlyContinue
foreach ($day in 20..30) {
    Unregister-ScheduledTask -TaskName "$taskPrefix-Bootstrap-$day" -TaskPath "\" -Confirm:$false -ErrorAction SilentlyContinue
}
$taskCommand = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$bootstrap`""
$taskName = "$taskPrefix-Bootstrap"
schtasks.exe /Create `
    /TN $taskName `
    /TR $taskCommand `
    /SC MONTHLY `
    /D "20,21,22,23,24,25,26,27,28,29,30" `
    /ST 12:00 `
    /RU "$env:USERDOMAIN\$env:USERNAME" `
    /RL LIMITED `
    /F | Out-Null

Write-Host "Registered one Priconner TL scanner bootstrap task for days 20-30 for $env:USERDOMAIN\$env:USERNAME"
