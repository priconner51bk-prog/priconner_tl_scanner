$ErrorActionPreference = "Stop"

$repo = (Resolve-Path (Join-Path $PSScriptRoot ".")).Path
$python = (Get-Command pythonw -ErrorAction Stop).Source
$taskName = "PriconnerTlMovieScanner-GitPull"
$scriptPath = Join-Path $repo "git_auto_pull.py"

$action = New-ScheduledTaskAction -Execute $python -Argument "`"$scriptPath`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Fast-forward priconner_tl_movie_scanner from GitHub every 15 minutes." -Force
Write-Host "Registered $taskName for $repo"
