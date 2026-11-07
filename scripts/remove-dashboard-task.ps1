[CmdletBinding(SupportsShouldProcess)]
param([string]$TaskName = 'AIStockTrader-Dashboard')

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeStatePath = Join-Path $repoRoot '.local\logs\dashboard-task-pid.json'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) { Write-Output "Scheduled task '$TaskName' is not registered."; exit 0 }
$runnerPath = Join-Path $PSScriptRoot 'run-dashboard-task.ps1'
if (@($task.Actions | Where-Object { [string]$_.Arguments -match [regex]::Escape($runnerPath) }).Count -eq 0) {
    throw "Scheduled task '$TaskName' does not belong to this repository; it was not changed."
}

if ($PSCmdlet.ShouldProcess("Current Windows user", "Stop and remove dashboard task '$TaskName'")) {
    if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    } while ($task -and $task.State -eq 'Running' -and [DateTimeOffset]::UtcNow -lt $deadline)
    if ($task -and $task.State -eq 'Running') { throw 'Windows did not stop the dashboard task in time.' }

    if (Test-Path -LiteralPath $runtimeStatePath) {
        $state = Get-Content -LiteralPath $runtimeStatePath -Raw | ConvertFrom-Json
        $nodeId = [int]$state.nodeProcessId
        $node = Get-CimInstance Win32_Process -Filter "ProcessId = $nodeId" -ErrorAction SilentlyContinue
        if ($node) {
            $nodePath = (Get-Command node.exe -ErrorAction Stop).Source
            $nextCli = [IO.Path]::GetFullPath((Join-Path $repoRoot 'dashboard\node_modules\next\dist\bin\next'))
            if ([string]::Equals([string]$node.ExecutablePath, [IO.Path]::GetFullPath($nodePath), [StringComparison]::OrdinalIgnoreCase) -and
                [string]$node.CommandLine -match [regex]::Escape($nextCli)) {
                Stop-Process -Id $nodeId -Force
            } else {
                throw 'Stored dashboard Node process identity did not match this repository; it was not stopped.'
            }
        }
        Remove-Item -LiteralPath $runtimeStatePath -Force -ErrorAction SilentlyContinue
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed '$TaskName'. Dashboard logs were kept."
}
