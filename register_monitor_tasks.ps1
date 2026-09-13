$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot ".")).Path
$python = (Get-Command pythonw -ErrorAction Stop).Source
$runner = Join-Path $repo "scheduled_window_runner.py"
$taskPrefix = "PriconnerTlMovieScanner-"

foreach ($name in @("worrychefs.py", "youtube_channel.py", "youtube_search.py", "boss_names_sync.py", "discord_channel.py")) {
    $taskName = "$taskPrefix$name"
    Unregister-ScheduledTask -TaskName $taskName -TaskPath "\" -Confirm:$false -ErrorAction SilentlyContinue
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

function Register-WindowTask($scriptName, $intervalMinutes) {
    $taskName = "$taskPrefix$scriptName"
    $action = New-ScheduledTaskAction -Execute $python -Argument "`"$runner`" $scriptName"
    $trigger = New-ScheduledTaskTrigger -Daily -At "12:00"
    $trigger.Repetition = (New-ScheduledTaskTrigger -Once -At "12:00" -RepetitionInterval (New-TimeSpan -Minutes $intervalMinutes) -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Run $scriptName only during the month-end monitoring window." -Force | Out-Null
}

Register-WindowTask "worrychefs.py" 10
Register-WindowTask "youtube_channel.py" 30
Register-WindowTask "youtube_search.py" 5
Register-WindowTask "discord_channel.py" 5

$bossAction = New-ScheduledTaskAction -Execute $python -Argument "`"$runner`" boss_names_sync.py"
$bossTrigger = New-ScheduledTaskTrigger -Daily -At "12:15"
Register-ScheduledTask -TaskName ($taskPrefix + "boss_names_sync.py") -Action $bossAction -Trigger $bossTrigger -Settings $settings -Description "Run boss_names_sync.py once on the month-end window start date." -Force | Out-Null

Write-Host "Registered month-end monitoring tasks for $repo"
