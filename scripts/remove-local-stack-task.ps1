[CmdletBinding(SupportsShouldProcess)]
param([string]$TaskName = 'AIStockTrader-LocalStack')

$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Output "Scheduled task '$TaskName' is not registered."
    exit 0
}

if ($PSCmdlet.ShouldProcess('Current Windows user', "Stop and remove scheduled task '$TaskName'")) {
    if ($task.State -eq 'Running') { Disable-ScheduledTask -TaskName $TaskName | Out-Null }
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $runtimeState = Join-Path $repoRoot '.local\logs\local-stack-pids.json'
    $runnerStopped = $false
    if (Test-Path -LiteralPath $runtimeState) {
        try {
            $state = Get-Content -LiteralPath $runtimeState -Raw | ConvertFrom-Json
            $runner = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.runnerProcessId)" -ErrorAction SilentlyContinue
            if ($runner -and $runner.CommandLine.Contains('run-local-stack-task.ps1')) {
                $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
                if (Test-Path -LiteralPath $taskkill) {
                    & $taskkill /PID $runner.ProcessId /T /F 2>$null | Out-Null
                    $runnerStopped = $true
                }
            }
        } catch { }
    }
    if (-not $runnerStopped -and $task.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName }
    Remove-Item -LiteralPath $runtimeState -Force -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed '$TaskName'. Local logs and reports were kept."
}
