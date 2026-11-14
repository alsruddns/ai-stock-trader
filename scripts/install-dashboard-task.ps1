[CmdletBinding(SupportsShouldProcess)]
param([string]$TaskName = 'AIStockTrader-Dashboard')

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeStatePath = Join-Path $repoRoot '.local\logs\dashboard-task-pid.json'
$runnerPath = Join-Path $PSScriptRoot 'run-dashboard-task.ps1'
$nextCli = Join-Path $repoRoot 'dashboard\node_modules\next\dist\bin\next'
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
. (Join-Path $PSScriptRoot 'lib\dashboard-task.ps1')
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powerShellPath)) { $powerShellPath = Join-Path $PSHOME 'pwsh.exe' }
if (-not (Test-Path -LiteralPath $powerShellPath)) { throw 'No PowerShell executable was found for the dashboard task.' }

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $matchesThisRepository = @($existing.Actions | Where-Object {
        [string]$_.Arguments -match [regex]::Escape($runnerPath)
    }).Count -gt 0
    if (-not $matchesThisRepository) { throw "Scheduled task '$TaskName' already exists with a different action; it was not replaced." }
}

$action = New-ScheduledTaskAction -Execute $powerShellPath `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`"" `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 5 -MultipleInstances IgnoreNew

if ($PSCmdlet.ShouldProcess("Current Windows user '$identity'", "Register local read-only dashboard task '$TaskName'")) {
    if ($existing -and $existing.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description 'Starts the loopback-only read-only AI Stock Trader dashboard after Spring and FastAPI report PAPER-locked state.' -Force | Out-Null
    $startRequestedAt = Get-Date
    Start-ScheduledTask -TaskName $TaskName
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
    $ready = $false
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task -or $task.State -ne 'Running') {
            $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
            $taskStarted = $info -and $info.LastRunTime -ge $startRequestedAt.AddSeconds(-1)
            if ($taskStarted) {
                $result = [string]$info.LastTaskResult
                throw "Dashboard task exited before supervisor readiness was verified (result $result)."
            }
        }
        $state = $null
        try {
            if (Test-Path -LiteralPath $runtimeStatePath) {
                $state = Get-Content -LiteralPath $runtimeStatePath -Raw | ConvertFrom-Json
            }
        } catch { $state = $null }
        if (Test-DashboardSupervisorState -State $state) {
            $runner = Get-Process -Id ([int]$state.runnerProcessId) -ErrorAction SilentlyContinue
            $node = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.nodeProcessId)" -ErrorAction SilentlyContinue
            $listener = @(Get-NetTCPConnection -State Listen -LocalPort 13000 -ErrorAction SilentlyContinue |
                Where-Object { [string]::Equals([string]$_.LocalAddress, '127.0.0.1', [StringComparison]::OrdinalIgnoreCase) })
            $processReady = $null -ne $runner -and $null -ne $node -and
                [int]$listener.Count -eq 1 -and [int]$listener[0].OwningProcess -eq [int]$state.nodeProcessId -and
                (Test-DashboardNodeProcessIdentity -ExecutablePath ([string]$node.ExecutablePath) `
                    -CommandLine ([string]$node.CommandLine) -ExpectedNodePath $nodePath -ExpectedCliPath $nextCli)
            if ($processReady) {
                try {
                    $operations = Invoke-RestMethod -Uri 'http://127.0.0.1:13000/api/operations' -TimeoutSec 5
                    if (Test-DashboardOperationsReady -Response $operations) { $ready = $true; break }
                    Assert-DashboardOperationsReady -Response $operations
                } catch {
                    if ($_.Exception.Message -match 'required PAPER and order locks|trading locks') { throw }
                }
            }
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { throw 'Dashboard task did not establish a verified supervisor, loopback listener, and PAPER-locked operations response within 90 seconds.' }
    Write-Output "Registered '$TaskName' to start the loopback-only dashboard at sign-in."
}
