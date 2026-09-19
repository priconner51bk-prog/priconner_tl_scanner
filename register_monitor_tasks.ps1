$ErrorActionPreference = "Stop"

$repo = (Resolve-Path $PSScriptRoot).Path
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python -ErrorAction Stop
}
$python = $pythonCommand.Source
$runner = Join-Path $repo "scheduled_monitor.py"
$taskPrefix = "PriconnerTlScanner-"
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

$taskDefinitions = @(
    @{ Name = "YouTubeSearch"; Stage = "youtube-search"; Minutes = 5; Start = "12:00" }
    @{ Name = "DiscordChannel"; Stage = "discord-channel"; Minutes = 5; Start = "12:01" }
    @{ Name = "WorryChefs"; Stage = "worrychefs"; Minutes = 10; Start = "12:02" }
    @{ Name = "YouTubeChannel"; Stage = "youtube-channel"; Minutes = 30; Start = "12:03" }
)

foreach ($definition in $taskDefinitions) {
    $taskName = "$taskPrefix$($definition.Name)"
    $action = New-ScheduledTaskAction `
        -Execute $python `
        -Argument "`"$runner`" --stage $($definition.Stage)" `
        -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger -Daily -At $definition.Start
    $repetition = New-ScheduledTaskTrigger `
        -Once `
        -At $definition.Start `
        -RepetitionInterval (New-TimeSpan -Minutes $definition.Minutes) `
        -RepetitionDuration (New-TimeSpan -Days 1)
    $trigger.Repetition = $repetition.Repetition

    Unregister-ScheduledTask `
        -TaskName $taskName `
        -TaskPath "\" `
        -Confirm:$false `
        -ErrorAction SilentlyContinue
    Register-ScheduledTask `
        -TaskName $taskName `
        -TaskPath "\" `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Run $($definition.Stage) during the month-end monitoring window." `
        -Force | Out-Null
}

Write-Host "Registered $($taskDefinitions.Count) Priconner TL scanner tasks for $env:USERDOMAIN\$env:USERNAME"
