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
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

$definitions = @(
    @{ Name = "YouTubeSearch"; Stage = "youtube-search"; Minutes = 5; Start = "12:00" }
    @{ Name = "DiscordChannel"; Stage = "discord-channel"; Minutes = 5; Start = "12:01" }
    @{ Name = "WorryChefs"; Stage = "worrychefs"; Minutes = 10; Start = "12:02" }
    @{ Name = "YouTubeChannel"; Stage = "youtube-channel"; Minutes = 30; Start = "12:03" }
)

foreach ($definition in $definitions) {
    $taskName = "$taskPrefix$($definition.Name)-$dateKey"
    if (Get-ScheduledTask -TaskName $taskName -TaskPath "\" -ErrorAction SilentlyContinue) {
        continue
    }

    $action = New-ScheduledTaskAction `
        -Execute $python `
        -Argument "`"$runner`" --stages $($definition.Stage)" `
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
