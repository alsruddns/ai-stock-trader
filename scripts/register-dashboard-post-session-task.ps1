[CmdletBinding()]
param(
    [string]$TaskName = 'AIStockTrader-Dashboard-PostSession-20261116',
    [datetime]$RunAt = [datetime]'2026-11-16T16:15:00'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$rolloutScript = Join-Path $PSScriptRoot 'rebuild-dashboard-after-close.ps1'
$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $rolloutScript)) { throw 'Dashboard rollout script is missing.' }
if (-not (Test-Path -LiteralPath $powerShellPath)) { throw 'Windows PowerShell executable is missing.' }
if ($RunAt -le (Get-Date).AddMinutes(2)) { throw 'Dashboard rollout time must be at least two minutes in the future.' }

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $sameAction = @($existing.Actions | Where-Object {
        [string]$_.Arguments -match [regex]::Escape($rolloutScript)
    }).Count -gt 0
    if (-not $sameAction) { throw "Scheduled task '$TaskName' already exists with a different action." }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powerShellPath `
    -Argument "-WindowStyle Hidden -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$rolloutScript`"" `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Once -At $RunAt
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 45) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Builds and restarts the loopback-only dashboard after the verified KRX close.' | Out-Null
$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
if (-not $task.Settings.StartWhenAvailable -or $task.State -eq 'Disabled' -or $info.NextRunTime -eq [datetime]::MinValue) {
    throw 'Windows did not retain the enabled dashboard post-session task and next run time.'
}
Write-Output "Registered '$TaskName' for $($info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss zzz')); PowerShell will run hidden."
