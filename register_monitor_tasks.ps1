$ErrorActionPreference = "Stop"

$repo = (Resolve-Path $PSScriptRoot).Path
$pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    $pythonCommand = Get-Command python -ErrorAction Stop
}
$python = $pythonCommand.Source
$bootstrap = Join-Path $repo "scheduler_bootstrap.ps1"
$taskPrefix = "PriconnerTlScanner-"
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew

$taskName = "$taskPrefix-Bootstrap"
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$bootstrap`"" `
        -WorkingDirectory $repo
    $trigger = New-ScheduledTaskTrigger `
        -Monthly `
        -DaysOfMonth (20..30) `
        -At "12:00"

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
        -Description "Register the daily monitoring tasks for the current day." `
        -Force | Out-Null

Write-Host "Registered the Priconner TL scanner bootstrap task for $env:USERDOMAIN\$env:USERNAME"
