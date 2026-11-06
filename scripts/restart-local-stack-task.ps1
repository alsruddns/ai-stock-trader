[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'AIStockTrader-LocalStack',
    [ValidateRange(10, 300)] [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runtimeStatePath = Join-Path $repoRoot '.local\logs\local-stack-pids.json'
$pythonHealthUrl = 'http://127.0.0.1:8000/api/v1/health'
$springSummaryUrl = 'http://127.0.0.1:8080/api/v1/operations/summary'

function Test-SessionResponsesFreshAndClosed($Sessions, [DateTimeOffset]$NowUtc) {
    if (@($Sessions).Count -ne 2) { return $false }
    foreach ($market in @('KR', 'US')) {
        $session = @($Sessions | Where-Object { [string]$_.market -eq $market }) | Select-Object -First 1
        if (-not $session) { return $false }
        if ([string]$session.market -ne $market -or
            $null -eq $session.PSObject.Properties['is_open'] -or
            $null -eq $session.PSObject.Properties['evaluated_at']) {
            return $false
        }
        $sessionTime = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse([string]$session.evaluated_at, [ref]$sessionTime)) {
            return $false
        }
        $ageSeconds = ($NowUtc.ToUniversalTime() - $sessionTime.ToUniversalTime()).TotalSeconds
        if ($ageSeconds -lt -10 -or $ageSeconds -gt 30) {
            return $false
        }
        if ([bool]$session.is_open) { return $false }
    }
    return $true
}

function Get-ConfirmedClosedSessions {
    $nowUtc = [DateTimeOffset]::UtcNow
    $sessions = foreach ($market in @('KR', 'US')) {
        Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/market/sessions/$market" -TimeoutSec 5
    }
    if (-not (Test-SessionResponsesFreshAndClosed $sessions $nowUtc)) {
        throw 'Both exchanges must be confirmed closed by fresh local calendar responses; local stack restart was not attempted.'
    }
}

function Get-TrackedProcess($ProcessId, [string]$Description) {
    if (-not $ProcessId -or [int]$ProcessId -le 0) { throw "Missing tracked $Description process ID." }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$ProcessId)" -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $commandLine = [string]$process.CommandLine
    $valid = switch ($Description) {
        'runner' { $commandLine.Contains((Join-Path $PSScriptRoot 'run-local-stack-task.ps1'), [StringComparison]::OrdinalIgnoreCase) }
        'backend' { $commandLine.Contains((Join-Path $PSScriptRoot 'start-local.ps1'), [StringComparison]::OrdinalIgnoreCase) }
        'market-ai' { $process.ExecutablePath.StartsWith((Join-Path $repoRoot 'services\market-ai\.venv\Scripts\python.exe'), [StringComparison]::OrdinalIgnoreCase) -and $commandLine.Contains('uvicorn app.main:app') }
        default { $false }
    }
    if (-not $valid) { throw "Tracked $Description process does not match this repository; restart was stopped." }
    return $process
}

function Stop-TrackedProcessTree($Process) {
    if ($null -eq $Process) { return }
    $current = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$Process.ProcessId)" -ErrorAction SilentlyContinue
    if (-not $current) { return }
    if ([string]$current.CommandLine -ne [string]$Process.CommandLine) {
        throw 'A tracked process ID changed owners; local stack restart was stopped.'
    }
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    if (-not (Test-Path -LiteralPath $taskkill)) { throw 'Windows taskkill is unavailable; local stack restart was stopped.' }
    & $taskkill /PID $Process.ProcessId /T /F 2>$null | Out-Null
}

function Test-LocalPortsFree {
    foreach ($port in @(8000, 8080)) {
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { return $false }
    }
    return $true
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) { throw "Scheduled task '$TaskName' is not registered." }
if ($task.State -ne 'Running') { throw "Scheduled task '$TaskName' is not running; restart was not attempted." }
$expectedRunnerPath = Join-Path $PSScriptRoot 'run-local-stack-task.ps1'
$taskActionMatches = @($task.Actions | Where-Object {
    [string]$_.Arguments -match [regex]::Escape($expectedRunnerPath)
}).Count -gt 0
if (-not $taskActionMatches) { throw 'The scheduled task does not launch this repository runner; restart was stopped.' }
if (-not (Test-Path -LiteralPath $runtimeStatePath)) { throw 'Local stack process state is missing; restart was not attempted.' }

Get-ConfirmedClosedSessions
$state = Get-Content -LiteralPath $runtimeStatePath -Raw | ConvertFrom-Json
$runner = Get-TrackedProcess $state.runnerProcessId 'runner'
$backend = Get-TrackedProcess $state.backendProcessId 'backend'
$marketAi = Get-TrackedProcess $state.marketAiProcessId 'market-ai'
if ($null -eq $runner -or $null -eq $backend -or $null -eq $marketAi) {
    throw 'One or more tracked API processes are missing; restart was not attempted.'
}
$runnerId = [int]$runner.ProcessId
if ([int]$backend.ParentProcessId -ne $runnerId -or [int]$marketAi.ParentProcessId -ne $runnerId) {
    throw 'A tracked API process is not owned by the local stack runner; restart was stopped.'
}

if ($PSCmdlet.ShouldProcess("Closed-session local stack '$TaskName'", 'Restart Spring Boot and Python APIs')) {
    Stop-ScheduledTask -TaskName $TaskName
    $stopDeadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
    do {
        Start-Sleep -Milliseconds 250
        $currentTask = Get-ScheduledTask -TaskName $TaskName
    } while ($currentTask.State -eq 'Running' -and [DateTimeOffset]::UtcNow -lt $stopDeadline)

    if ($currentTask.State -eq 'Running') { throw 'Windows did not stop the local stack task in time.' }
    Stop-TrackedProcessTree $runner
    Stop-TrackedProcessTree $backend
    Stop-TrackedProcessTree $marketAi

    $portDeadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
    while (-not (Test-LocalPortsFree) -and [DateTimeOffset]::UtcNow -lt $portDeadline) {
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-LocalPortsFree)) { throw 'Old local API listeners did not close; new instances were not started.' }

    $currentState = Get-Content -LiteralPath $runtimeStatePath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($currentState -and [int]$currentState.runnerProcessId -eq $runnerId) {
        Remove-Item -LiteralPath $runtimeStatePath -Force
    }
    Start-ScheduledTask -TaskName $TaskName

    $startupDeadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $pythonHealth = $null
    $springSummary = $null
    while ([DateTimeOffset]::UtcNow -lt $startupDeadline) {
        try { $pythonHealth = Invoke-RestMethod -Uri $pythonHealthUrl -TimeoutSec 2 } catch { $pythonHealth = $null }
        try { $springSummary = Invoke-RestMethod -Uri $springSummaryUrl -TimeoutSec 2 } catch { $springSummary = $null }
        if ($pythonHealth -and $springSummary) { break }
        Start-Sleep -Seconds 2
    }
    if (-not $pythonHealth -or -not $springSummary) { throw 'The local APIs did not become ready after restart.' }
    $pythonOpenApi = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/openapi.json' -TimeoutSec 5
    if ($null -eq $pythonOpenApi.paths.PSObject.Properties['/api/v1/market/session-quality/{market}/{symbol}']) {
        throw 'The restarted Python API is missing the expected session-quality route.'
    }
    if ($pythonHealth.status -ne 'ok' -or $pythonHealth.live_trading_enabled -or $pythonHealth.paper_automation_enabled -or
        $springSummary.tradingMode -ne 'paper' -or $springSummary.liveTrading -ne 'disabled' -or
        $springSummary.paperAutomationEnabled -ne $false -or $springSummary.kisRealOrdersEnabled -ne $false) {
        throw 'The restarted APIs did not report the required trading locks.'
    }
    Write-Output 'Local Spring Boot and Python APIs restarted after both exchange sessions were confirmed closed; live trading remains disabled.'
}
