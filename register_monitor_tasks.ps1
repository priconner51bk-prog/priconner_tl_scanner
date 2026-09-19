$ErrorActionPreference = "Stop"

$repo = (Resolve-Path $PSScriptRoot).Path
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python -ErrorAction Stop
}
$python = $pythonCommand.Source
$bootstrap = Join-Path $repo "scheduler_bootstrap.ps1"
$taskPrefix = "PriconnerTlScanner-"
$oldTaskNames = @("YouTubeSearch", "DiscordChannel", "WorryChefs", "YouTubeChannel", "Collector", "DiscordQueue")
foreach ($oldTaskName in $oldTaskNames) {
    Unregister-ScheduledTask -TaskName "$taskPrefix$oldTaskName" -TaskPath "\" -Confirm:$false -ErrorAction SilentlyContinue
}
Get-ScheduledTask -TaskPath "\" -ErrorAction SilentlyContinue |
    Where-Object {
        $_.TaskName -like "$taskPrefix*" -and
        $_.TaskName -match "-(YouTubeSearch|DiscordChannel|WorryChefs|YouTubeChannel)-\d{8}$"
    } |
    ForEach-Object {
        Unregister-ScheduledTask -TaskName $_.TaskName -TaskPath "\" -Confirm:$false -ErrorAction SilentlyContinue
    }
Unregister-ScheduledTask -TaskName "$taskPrefix-Bootstrap" -TaskPath "\" -Confirm:$false -ErrorAction SilentlyContinue
foreach ($day in 20..30) {
    Unregister-ScheduledTask -TaskName "$taskPrefix-Bootstrap-$day" -TaskPath "\" -Confirm:$false -ErrorAction SilentlyContinue
}
$taskCommand = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$bootstrap`""
foreach ($day in 20..30) {
    $taskName = "$taskPrefix-Bootstrap-$day"
    schtasks.exe /Create /TN $taskName /TR $taskCommand /SC MONTHLY /D $day /ST 12:00 /RU "$env:USERDOMAIN\$env:USERNAME" /RL LIMITED /F | Out-Null
}

Write-Host "Registered 11 Priconner TL scanner bootstrap tasks for days 20-30 for $env:USERDOMAIN\$env:USERNAME"
