[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
$taskPattern = '^AIStockTrader-(?:KRX|US)-Session-Monitor-\d{8}$|^AIStockTrader-P4-Model-(?:Train|Holdout)-|^AIStockTrader-(?:KRX|US)-Paper-Lifecycle-Smoke-\d{8}$'
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskName -match $taskPattern })
if ($tasks.Count -eq 0) { throw 'No registered market-monitor, PAPER lifecycle, or P4 evaluation tasks were found.' }

$updated = [System.Collections.Generic.List[string]]::new()
foreach ($task in $tasks) {
    if ($task.State -eq 'Running') { throw "Refusing to modify running task '$($task.TaskName)'." }
    if ($task.Principal.LogonType -ne 'Interactive') {
        throw "Task '$($task.TaskName)' does not use the expected interactive Windows user."
    }

    $settings = $task.Settings
    if ($settings.StartWhenAvailable -and $settings.WakeToRun) { continue }
    $settings.StartWhenAvailable = $true
    $settings.WakeToRun = $true
    if ($PSCmdlet.ShouldProcess($task.TaskName, 'Allow delayed start and request wake from sleep')) {
        Set-ScheduledTask -TaskName $task.TaskName -Settings $settings | Out-Null
        $verified = Get-ScheduledTask -TaskName $task.TaskName
        if (-not $verified.Settings.StartWhenAvailable -or -not $verified.Settings.WakeToRun) {
            throw "Windows did not retain the wake settings for '$($task.TaskName)'."
        }
        $updated.Add($task.TaskName)
    }
}

Write-Output "Examined $($tasks.Count) market-monitor and P4 scheduled tasks; updated $($updated.Count)."
