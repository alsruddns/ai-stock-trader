[CmdletBinding()]
param(
    [string]$TaskName = 'AIStockTrader-PAPER-No-Order-Confirmation-20261116',
    [datetime]$RunAt = [datetime]'2026-11-16T16:30:00'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$confirmationScript = Join-Path $PSScriptRoot 'confirm-paper-no-order-after-refresh.ps1'
$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $confirmationScript -PathType Leaf) -or
    -not (Test-Path -LiteralPath $powerShellPath -PathType Leaf)) {
    throw 'No-order confirmation task dependencies are missing.'
}
if ([TimeZoneInfo]::Local.Id -ne 'Korea Standard Time') {
    throw 'Windows time zone must be Korea Standard Time for the one-time confirmation task.'
}
if ($RunAt -le (Get-Date).AddMinutes(2)) {
    throw 'No-order confirmation task must be scheduled at least two minutes in the future.'
}

$restartName = 'AIStockTrader-PostSessionRefresh-20261116'
$dashboardName = 'AIStockTrader-Dashboard-PostSession-20261116'
$restartTask = Get-ScheduledTask -TaskName $restartName -ErrorAction SilentlyContinue
$dashboardTask = Get-ScheduledTask -TaskName $dashboardName -ErrorAction SilentlyContinue
if (-not $restartTask -or $restartTask.State -eq 'Disabled' -or
    -not $dashboardTask -or $dashboardTask.State -eq 'Disabled') {
    throw 'The post-session Spring and Dashboard refresh tasks must both be registered and enabled.'
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $sameAction = @($existing.Actions | Where-Object {
        [string]$_.Arguments -match [regex]::Escape($confirmationScript)
    }).Count -gt 0
    $existingInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    if (-not $sameAction -or $existing.State -eq 'Disabled' -or
        $existingInfo.NextRunTime -ne $RunAt) {
        throw "Scheduled task '$TaskName' already exists but does not match this enabled run time and action; refusing to replace it."
    }
    Write-Output "Verified existing '$TaskName' at $RunAt; no task changes were made."
    return
}

$action = New-ScheduledTaskAction -Execute $powerShellPath `
    -Argument "-WindowStyle Hidden -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$confirmationScript`"" `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Once -At $RunAt
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Confirms the specific KIS PAPER no-order incident only after post-session services refresh; never submits or cancels a broker order.' | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
if (-not $task.Settings.StartWhenAvailable -or $task.State -eq 'Disabled' -or
    $info.NextRunTime -eq [datetime]::MinValue) {
    throw 'Windows did not retain the enabled no-order confirmation task and next run time.'
}
Write-Output "Registered '$TaskName' for $($info.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss zzz')); PowerShell will run hidden."
