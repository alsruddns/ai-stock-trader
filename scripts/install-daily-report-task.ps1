[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$DailyTime = "07:30",
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$AfterCloseTime = "16:00",
    [string]$TaskName = "AIStockTrader-DailyOperationsReport"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$null = . (Join-Path $PSScriptRoot "lib\daily-report-task.ps1")
$runnerPath = Join-Path $PSScriptRoot "run-daily-report-task.ps1"
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$powerShellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $powerShellPath)) {
    $powerShellPath = Join-Path $PSHOME "pwsh.exe"
}
if (-not (Test-Path -LiteralPath $powerShellPath)) {
    throw "No PowerShell executable was found for the scheduled report task."
}
$action = New-ScheduledTaskAction -Execute $powerShellPath `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`"" `
    -WorkingDirectory $repoRoot
$triggers = New-AiTraderDailyReportTriggers -Times @($DailyTime, $AfterCloseTime)
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew

if ($PSCmdlet.ShouldProcess("Current Windows user", "Register daily task '$TaskName' at $DailyTime and $AfterCloseTime")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
        -Principal $principal -Settings $settings -Description "Exports local PAPER operations, Python AI status, domestic market sessions, and stored data quality to .local/reports; read-only." -Force | Out-Null
    Write-Output "Registered '$TaskName' for daily local times $DailyTime and $AfterCloseTime. The current Windows user must be signed in and Spring Boot must be running."
}
