$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboardRoot = Join-Path $repoRoot 'dashboard'
$nextCli = Join-Path $dashboardRoot 'node_modules\next\dist\bin\next'
$logDirectory = Join-Path $repoRoot '.local\logs'
$taskLog = Join-Path $logDirectory 'dashboard-task.log'
$stdoutPath = Join-Path $logDirectory 'dashboard.stdout.log'
$stderrPath = Join-Path $logDirectory 'dashboard.stderr.log'
$runtimeStatePath = Join-Path $logDirectory 'dashboard-task-pid.json'
$nodeProcess = $null
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
. (Join-Path $PSScriptRoot 'lib\dashboard-task.ps1')

function Write-DashboardTaskLog {
    param([Parameter(Mandatory)][string]$Message)
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Add-Content -LiteralPath $taskLog -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz') $Message" -Encoding UTF8
}

function Get-DashboardProcessIdentity {
    param([int]$ProcessId)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    if (-not (Test-DashboardNodeProcessIdentity -ExecutablePath ([string]$process.ExecutablePath) `
            -CommandLine ([string]$process.CommandLine) -ExpectedNodePath $nodePath -ExpectedCliPath $nextCli)) {
        return $null
    }
    return $process
}

function Stop-DashboardNode {
    param($Process)
    if ($null -eq $Process) { return }
    $current = Get-DashboardProcessIdentity -ProcessId ([int]$Process.Id)
    if ($null -eq $current) { return }
    Stop-Process -Id ([int]$Process.Id) -Force -ErrorAction SilentlyContinue
    try { $Process.WaitForExit(10000) | Out-Null } catch { }
}

function Test-UpstreamServicesReady {
    $spring = $null
    $python = $null
    try { $spring = Invoke-RestMethod -Uri 'http://127.0.0.1:18080/api/v1/operations/summary' -TimeoutSec 3 } catch { }
    try { $python = Invoke-RestMethod -Uri 'http://127.0.0.1:18000/api/v1/health' -TimeoutSec 3 } catch { }
    if ($null -eq $spring -or $null -eq $python) { return $false }
    if ($spring.tradingMode -ne 'paper' -or $spring.liveTrading -ne 'disabled' -or
        $spring.paperAutomationEnabled -ne $false -or $spring.kisPaperOrdersEnabled -ne $false -or
        $spring.kisRealOrdersEnabled -ne $false -or $spring.kisRealAccountReadsEnabled -ne $false -or
        $spring.kisRealReconciliationEnabled -ne $false -or $python.live_trading_enabled -ne $false -or
        $python.paper_automation_enabled -ne $false) {
        throw 'Dashboard startup stopped because an upstream API did not report all PAPER and order locks.'
    }
    return $true
}

try {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    if (-not (Test-Path -LiteralPath $nextCli)) { throw 'Dashboard production build is missing; build the dashboard before starting its task.' }
    $allListeners = @(Get-NetTCPConnection -State Listen -LocalPort 13000 -ErrorAction SilentlyContinue)
    if (@($allListeners | Where-Object {
            $_.LocalAddress -notin @('127.0.0.1', '::1')
        }).Count -gt 0) {
        throw 'Dashboard port 13000 is bound to a non-loopback address; no process was replaced.'
    }
    $listeners = @($allListeners | Where-Object {
        [string]::Equals([string]$_.LocalAddress, '127.0.0.1', [StringComparison]::OrdinalIgnoreCase)
    })
    if ($listeners.Count -gt 0) {
        $listenerOwners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
        if ($listenerOwners.Count -ne 1) { throw 'Dashboard loopback port 13000 has ambiguous listener ownership; no process was replaced.' }
        $listenerPid = [int]$listenerOwners[0]
        $verifiedNode = Get-DashboardProcessIdentity -ProcessId $listenerPid
        if ($null -eq $verifiedNode) { throw 'Dashboard loopback port 13000 is occupied by an unverified process; no process was replaced.' }
        if (-not (Test-Path -LiteralPath $runtimeStatePath)) {
            throw 'A verified dashboard process is running without its supervisor state; no process was replaced.'
        }
        $previousState = Get-Content -LiteralPath $runtimeStatePath -Raw | ConvertFrom-Json
        if ([int]$previousState.nodeProcessId -ne $listenerPid) {
            throw 'Dashboard supervisor state does not match the verified listener; no process was replaced.'
        }
        if (Get-Process -Id ([int]$previousState.runnerProcessId) -ErrorAction SilentlyContinue) {
            throw 'Dashboard is already supervised by a running task; duplicate startup was refused.'
        }
        $nodeProcess = Get-Process -Id $listenerPid -ErrorAction Stop
        $startupMode = 'adopted the verified dashboard process after its previous supervisor stopped'
    }

    $upstreamDeadline = [DateTimeOffset]::UtcNow.AddMinutes(6)
    while ([DateTimeOffset]::UtcNow -lt $upstreamDeadline) {
        if (Test-UpstreamServicesReady) { break }
        Start-Sleep -Seconds 3
    }
    if (-not (Test-UpstreamServicesReady)) { throw 'Spring and FastAPI did not become healthy and PAPER-locked within six minutes.' }

    if ($null -eq $nodeProcess) {
        $argumentLine = '"{0}" start --hostname 127.0.0.1 --port 13000' -f $nextCli
        $nodeProcess = Start-Process -FilePath $nodePath -ArgumentList $argumentLine `
            -WorkingDirectory $dashboardRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $startupMode = 'started a new dashboard process'
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)
    $readyResponse = $null
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if ($nodeProcess.HasExited) { throw 'Dashboard Node process exited during startup.' }
        try { $readyResponse = Invoke-RestMethod -Uri 'http://127.0.0.1:13000/api/operations' -TimeoutSec 20 } catch { $readyResponse = $null }
        Assert-DashboardOperationsReady -Response $readyResponse
        if (Test-DashboardOperationsReady -Response $readyResponse) { break }
        Start-Sleep -Seconds 2
    }
    if (-not (Test-DashboardOperationsReady -Response $readyResponse)) {
        throw 'Dashboard did not report both local APIs and required trading locks within 60 seconds.'
    }

    $listener = Get-NetTCPConnection -State Listen -LocalPort 13000 -ErrorAction SilentlyContinue |
        Where-Object { [int]$_.OwningProcess -eq [int]$nodeProcess.Id } | Select-Object -First 1
    if (-not $listener) { throw 'Dashboard listener did not belong to the launched Node process.' }
    $state = @{ runnerProcessId = $PID; nodeProcessId = $nodeProcess.Id; updatedAt = (Get-Date).ToString('o') } |
        ConvertTo-Json -Compress
    $temporaryStatePath = "$runtimeStatePath.tmp"
    Set-Content -LiteralPath $temporaryStatePath -Value $state -Encoding UTF8
    Move-Item -LiteralPath $temporaryStatePath -Destination $runtimeStatePath -Force
    Write-DashboardTaskLog "Dashboard is healthy on loopback and both APIs report required PAPER and order locks; supervisor $startupMode."

    while ($true) {
        Start-Sleep -Seconds 5
        if ($nodeProcess.HasExited) { throw 'Dashboard Node process stopped unexpectedly.' }
        $currentListener = Get-NetTCPConnection -State Listen -LocalPort 13000 -ErrorAction SilentlyContinue |
            Where-Object { [int]$_.OwningProcess -eq [int]$nodeProcess.Id } | Select-Object -First 1
        if (-not $currentListener) { throw 'Dashboard listener ownership changed; task is stopping its verified Node process.' }
        $operations = $null
        try { $operations = Invoke-RestMethod -Uri 'http://127.0.0.1:13000/api/operations' -TimeoutSec 20 } catch { }
        Assert-DashboardOperationsReady -Response $operations
    }
} catch {
    $message = $_.Exception.Message
    if ($message -match 'PAPER and order locks|already occupied|production build is missing|did not become|listener ownership changed|stopped unexpectedly|exited during startup') {
        Write-DashboardTaskLog $message
    } else {
        Write-DashboardTaskLog ('Dashboard task failed (' + $_.Exception.GetType().Name + ').')
    }
    exit 1
} finally {
    Stop-DashboardNode -Process $nodeProcess
    if (Test-Path -LiteralPath $runtimeStatePath) {
        try {
            $state = Get-Content -LiteralPath $runtimeStatePath -Raw | ConvertFrom-Json
            if ([int]$state.runnerProcessId -eq $PID) { Remove-Item -LiteralPath $runtimeStatePath -Force }
        } catch { }
    }
}
