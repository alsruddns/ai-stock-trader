[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyTime = "07:30",
    [string]$TaskName = "AIStockTrader-DailyOperationsReport"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$runnerPath = Join-Path $PSScriptRoot "run-daily-report-task.ps1"
$runAt = [DateTime]::Today.Add([TimeSpan]::ParseExact($DailyTime, "hh\:mm", [Globalization.CultureInfo]::InvariantCulture))
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$powerShellPath = Join-Path $PSHOME "powershell.exe"
$action = New-ScheduledTaskAction -Execute $powerShellPath `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`"" `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $runAt
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew

if ($PSCmdlet.ShouldProcess("Current Windows user", "Register daily task '$TaskName' at $DailyTime")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description "Exports the local PAPER and operations summary to .local/reports." -Force | Out-Null
    Write-Output "Registered '$TaskName' for daily local time $DailyTime. The current Windows user must be signed in and Spring Boot must be running."
}
