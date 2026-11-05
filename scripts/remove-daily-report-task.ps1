[CmdletBinding(SupportsShouldProcess)]
param([string]$TaskName = "AIStockTrader-DailyOperationsReport")

$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Output "Scheduled task '$TaskName' is not registered."
    exit 0
}

if ($PSCmdlet.ShouldProcess("Current Windows user", "Remove scheduled task '$TaskName'")) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed '$TaskName'. Existing reports and local logs were kept."
}
