[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\market-session-scheduler.ps1')
$monitorPath = Join-Path $PSScriptRoot 'test-paper-websocket-monitor.ps1'
$targetDuration = Get-SessionMonitorDurationSeconds
$tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
    $_.TaskName -match '^AIStockTrader-(KRX|US)-Session-Monitor-\d{8}$'
})
if ($tasks.Count -eq 0) { throw 'No registered AI Stock Trader market-session monitors were found.' }

$plans = [System.Collections.Generic.List[object]]::new()
foreach ($task in $tasks) {
    if ($task.State -ne 'Ready') {
        throw "Task '$($task.TaskName)' is not Ready; refusing to alter an active session monitor."
    }
    $feed = if ($task.TaskName -like 'AIStockTrader-KRX-*') { 'paper' } else { 'real' }
    if (@($task.Actions).Count -ne 1 -or @($task.Triggers).Count -ne 1) {
        throw "Task '$($task.TaskName)' has an unexpected action or trigger count."
    }
    $arguments = [string]$task.Actions[0].Arguments
    $durationMatches = [regex]::Matches($arguments, '(?i)-DurationSeconds\s+(\d+)(?=\s|$)')
    if ($durationMatches.Count -ne 1) {
        throw "Task '$($task.TaskName)' does not have exactly one bounded duration argument."
    }
    $currentDuration = [int]$durationMatches[0].Groups[1].Value
    $runAt = [DateTimeOffset]::Parse([string]$task.Triggers[0].StartBoundary, [Globalization.CultureInfo]::InvariantCulture)
    if ($currentDuration -eq $targetDuration) {
        if (-not (Test-SessionMonitorTaskDefinition -Task $task -MonitorPath $monitorPath `
                -Feed $feed -ExpectedRunAt $runAt -ExpectedDurationSeconds $targetDuration)) {
            throw "Task '$($task.TaskName)' does not match the required safe monitor definition."
        }
        continue
    }
    if ($currentDuration -ne 24300 -or
        -not (Test-SessionMonitorTaskDefinition -Task $task -MonitorPath $monitorPath `
            -Feed $feed -ExpectedRunAt $runAt -ExpectedDurationSeconds 24300)) {
        throw "Task '$($task.TaskName)' is not a recognized previous-version monitor; refusing to replace it."
    }

    $newArguments = [regex]::Replace($arguments, '(?i)(-DurationSeconds\s+)24300(?=\s|$)', ('${1}' + $targetDuration))
    $newAction = New-ScheduledTaskAction -Execute $task.Actions[0].Execute `
        -Argument $newArguments -WorkingDirectory $task.Actions[0].WorkingDirectory
    $candidate = [pscustomobject]@{
        State = $task.State
        Actions = @($newAction)
        Triggers = @($task.Triggers)
        Principal = $task.Principal
        Settings = $task.Settings
    }
    if (-not (Test-SessionMonitorTaskDefinition -Task $candidate -MonitorPath $monitorPath `
            -Feed $feed -ExpectedRunAt $runAt -ExpectedDurationSeconds $targetDuration)) {
        throw "The updated action for '$($task.TaskName)' failed the safe monitor definition check."
    }
    $plans.Add([pscustomobject]@{
        TaskName = $task.TaskName
        Feed = $feed
        RunAt = $runAt
        OldAction = $task.Actions[0]
        NewAction = $newAction
    })
}

$attempted = [System.Collections.Generic.List[object]]::new()
try {
    foreach ($plan in $plans) {
        if (-not $PSCmdlet.ShouldProcess($plan.TaskName, 'Add a close-detection buffer to the read-only market monitor')) {
            continue
        }
        $attempted.Add($plan)
        Set-ScheduledTask -TaskName $plan.TaskName -Action $plan.NewAction -ErrorAction Stop | Out-Null
        $updated = Get-ScheduledTask -TaskName $plan.TaskName -ErrorAction Stop
        if (-not (Test-SessionMonitorTaskDefinition -Task $updated -MonitorPath $monitorPath `
                -Feed $plan.Feed -ExpectedRunAt $plan.RunAt -ExpectedDurationSeconds $targetDuration)) {
            throw "Task '$($plan.TaskName)' did not retain the expected settings after its action update."
        }
    }
} catch {
    $rollbackFailures = [System.Collections.Generic.List[string]]::new()
    for ($index = $attempted.Count - 1; $index -ge 0; $index--) {
        $plan = $attempted[$index]
        try {
            Set-ScheduledTask -TaskName $plan.TaskName -Action $plan.OldAction -ErrorAction Stop | Out-Null
            $restored = Get-ScheduledTask -TaskName $plan.TaskName -ErrorAction Stop
            if (-not (Test-SessionMonitorTaskDefinition -Task $restored -MonitorPath $monitorPath `
                    -Feed $plan.Feed -ExpectedRunAt $plan.RunAt -ExpectedDurationSeconds 24300)) {
                throw 'restored task definition did not verify'
            }
        } catch {
            $rollbackFailures.Add($plan.TaskName)
        }
    }
    if ($rollbackFailures.Count -gt 0) {
        throw "Monitor update failed and rollback needs attention for $($rollbackFailures.Count) task(s)."
    }
    throw 'Monitor update failed; all attempted task definitions were restored.'
}

Write-Output "Verified $($tasks.Count) session monitor task(s); updated $($attempted.Count) with at least a 15-minute close-detection buffer."
