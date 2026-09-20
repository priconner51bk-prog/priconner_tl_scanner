$ErrorActionPreference = "Stop"

$repo = (Resolve-Path $PSScriptRoot).Path
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python -ErrorAction Stop
}
$python = $pythonCommand.Source
$runner = Join-Path $repo "monitor_runner.py"
$taskPrefix = "PriconnerTlScanner-"
$today = Get-Date
$dateKey = $today.ToString("yyyyMMdd")
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

$definitions = @(
    @{ Name = "YouTubeChannel"; Stage = "youtube-channel"; Minutes = 15; Start = "12:00" }
    @{ Name = "YouTubeKeyword"; Stage = "youtube-search"; Minutes = 5; Start = "12:02" }
)

foreach ($definition in $definitions) {
    $taskName = "$taskPrefix$($definition.Name)-$dateKey"
    if (Get-ScheduledTask -TaskName $taskName -TaskPath "\" -ErrorAction SilentlyContinue) {
        continue
    }

    $runnerArguments = "`"$runner`""
    if ($definition.Stage) {
        $runnerArguments += " --stages $($definition.Stage)"
    }
    $action = New-ScheduledTaskAction `
        -Execute $python `
        -Argument $runnerArguments `
        -WorkingDirectory $repo

    if ($today.Day -ge 22) {
        $trigger = New-ScheduledTaskTrigger `
            -Once `
            -At $definition.Start `
            -RepetitionInterval (New-TimeSpan -Minutes $definition.Minutes) `
            -RepetitionDuration (New-TimeSpan -Days 1)
    }
    else {
        $trigger = New-ScheduledTaskTrigger -Once -At $definition.Start
    }

    Register-ScheduledTask `
        -TaskName $taskName `
        -TaskPath "\" `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Run $($definition.Stage) for $($today.ToString('yyyy-MM-dd'))." `
        -Force | Out-Null
}

$queueTaskName = "$taskPrefix-DiscordQueue-$dateKey"
if (-not (Get-ScheduledTask -TaskName $queueTaskName -TaskPath "\" -ErrorAction SilentlyContinue)) {
    $queueAction = New-ScheduledTaskAction `
        -Execute $python `
        -Argument "`"$repo\discord_queue.py`"" `
        -WorkingDirectory $repo
    $queueTrigger = New-ScheduledTaskTrigger `
        -Once `
        -At $today.Date.AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 1) `
        -RepetitionDuration (New-TimeSpan -Days 1)
    Register-ScheduledTask `
        -TaskName $queueTaskName `
        -TaskPath "\" `
        -Action $queueAction `
        -Trigger $queueTrigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Drain the Discord post queue for $($today.ToString('yyyy-MM-dd'))." `
        -Force | Out-Null
}
