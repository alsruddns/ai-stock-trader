[CmdletBinding()]
param(
    [string]$DashboardTaskName = 'AIStockTrader-Dashboard',
    [ValidateRange(30, 300)] [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboardRoot = Join-Path $repoRoot 'dashboard'
$dashboardScript = Join-Path $PSScriptRoot 'run-dashboard-task.ps1'
$nextCli = Join-Path $dashboardRoot 'node_modules\next\dist\bin\next'
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
$runtimeStatePath = Join-Path $repoRoot '.local\logs\dashboard-task-pid.json'
$logPath = Join-Path $repoRoot '.local\logs\dashboard-post-session-rebuild.log'
$buildPath = Join-Path $dashboardRoot '.next'
$backupDirectory = Join-Path $repoRoot '.local\backups\dashboard'
$backupPath = Join-Path $backupDirectory ('.next-rollback-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$script:rolloutStage = 'initialization'
. (Join-Path $PSScriptRoot 'lib\dashboard-task.ps1')

function Write-RolloutLog([string]$Message) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
    Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz') $Message" -Encoding UTF8
}

function Assert-ClosedDomesticSession {
    $script:rolloutStage = 'market_session_gate'
    $session = Invoke-RestMethod -Uri 'http://127.0.0.1:18000/api/v1/market/sessions/KR' -TimeoutSec 5
    if (-not $session -or $session.market -ne 'KR' -or $session.is_open -ne $false -or
        $null -eq $session.PSObject.Properties['evaluated_at']) {
        throw 'A fresh domestic market session response did not confirm KRX is closed.'
    }
    $evaluatedAt = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse([string]$session.evaluated_at, [ref]$evaluatedAt)) {
        throw 'The domestic market session response is stale.'
    }
    $ageSeconds = ([DateTimeOffset]::UtcNow - $evaluatedAt.ToUniversalTime()).TotalSeconds
    if ($ageSeconds -lt -10 -or $ageSeconds -gt 30) { throw 'The domestic market session response is stale.' }
    if ($session.is_session) {
        $closeAt = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse([string]$session.close_at, [ref]$closeAt) -or
            [DateTimeOffset]::UtcNow -lt $closeAt.ToUniversalTime().AddMinutes(20)) {
            throw 'The KRX session finalization window has not elapsed.'
        }
    }
}

function Assert-TradingLocks {
    $script:rolloutStage = 'trading_lock_gate'
    $spring = Invoke-RestMethod -Uri 'http://127.0.0.1:18080/api/v1/operations/summary' -TimeoutSec 5
    $python = Invoke-RestMethod -Uri 'http://127.0.0.1:18000/api/v1/health' -TimeoutSec 5
    if ($spring.tradingMode -ne 'paper' -or $spring.liveTrading -ne 'disabled' -or
        $spring.paperAutomationEnabled -ne $false -or $spring.kisPaperOrdersEnabled -ne $false -or
        $spring.kisRealOrdersEnabled -ne $false -or $spring.kisRealAccountReadsEnabled -ne $false -or
        $spring.kisRealReconciliationEnabled -ne $false -or $python.live_trading_enabled -ne $false -or
        $python.paper_automation_enabled -ne $false) {
        throw 'Spring and FastAPI must report PAPER mode with all order and automation switches locked.'
    }
    $unknownPaperOrders = @($spring.recentPaperKisOrders | Where-Object { $_.market -eq 'KR' -and $_.status -eq 'UNKNOWN' })
    foreach ($intent in $unknownPaperOrders) {
        $keyProperty = $intent.PSObject.Properties['idempotencyKey']
        if ($null -eq $keyProperty -or [string]$keyProperty.Value -notmatch
                '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$') {
            throw 'Spring does not expose a valid domestic PAPER UNKNOWN key required by the current dashboard build.'
        }
    }
}

function Get-VerifiedDashboardListener {
    $allListeners = @(Get-NetTCPConnection -State Listen -LocalPort 13000 -ErrorAction SilentlyContinue)
    if (@($allListeners | Where-Object { $_.LocalAddress -notin @('127.0.0.1', '::1') }).Count -gt 0) {
        throw 'Dashboard port 13000 is listening on a non-loopback interface.'
    }
    $listeners = @($allListeners | Where-Object { $_.LocalAddress -eq '127.0.0.1' })
    if (-not $listeners.Count) { return $null }
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -ne 1) { throw 'Dashboard port 13000 has ambiguous process ownership.' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$owners[0])" -ErrorAction SilentlyContinue
    if (-not $process -or -not (Test-DashboardNodeProcessIdentity -ExecutablePath ([string]$process.ExecutablePath) `
            -CommandLine ([string]$process.CommandLine) -ExpectedNodePath $nodePath -ExpectedCliPath $nextCli)) {
        throw 'Dashboard port 13000 is not owned by the expected local Next.js process.'
    }
    return $process
}

function Stop-VerifiedDashboard([switch]$AllowMissingSupervisorState) {
    $process = Get-VerifiedDashboardListener
    if (-not $process) { return }
    if (-not (Test-Path -LiteralPath $runtimeStatePath) -and -not $AllowMissingSupervisorState) {
        throw 'The verified dashboard listener has no supervisor state; it was not stopped.'
    }
    if (Test-Path -LiteralPath $runtimeStatePath) {
        $state = Get-Content -LiteralPath $runtimeStatePath -Raw | ConvertFrom-Json
        if ([int]$state.nodeProcessId -ne [int]$process.ProcessId) {
            throw 'Dashboard supervisor state does not match its listener.'
        }
        $runner = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.runnerProcessId)" -ErrorAction SilentlyContinue
        if ($runner) {
            if ([string]$runner.CommandLine -notmatch [regex]::Escape($dashboardScript)) {
                throw 'Dashboard supervisor process identity does not match this repository.'
            }
            Stop-Process -Id ([int]$runner.ProcessId) -Force -ErrorAction Stop
        }
    }
    $node = Get-Process -Id ([int]$process.ProcessId) -ErrorAction Stop
    Stop-Process -Id $node.Id -Force -ErrorAction Stop
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
    while ([DateTimeOffset]::UtcNow -lt $deadline -and (Get-VerifiedDashboardListener)) {
        Start-Sleep -Milliseconds 250
    }
    if (Get-VerifiedDashboardListener) { throw 'Verified dashboard listener did not stop.' }
    Remove-Item -LiteralPath $runtimeStatePath -Force -ErrorAction SilentlyContinue
}

function Start-And-VerifyDashboard {
    $script:rolloutStage = 'dashboard_task_start'
    $task = Get-ScheduledTask -TaskName $DashboardTaskName -ErrorAction SilentlyContinue
    if (-not $task -or $task.State -eq 'Disabled' -or @($task.Actions | Where-Object {
            [string]$_.Arguments -match [regex]::Escape($dashboardScript)
        }).Count -eq 0) {
        throw 'The enabled dashboard task for this repository is missing.'
    }
    $taskDeadline = [DateTimeOffset]::UtcNow.AddSeconds(20)
    do {
        $task = Get-ScheduledTask -TaskName $DashboardTaskName -ErrorAction SilentlyContinue
        if ($task.State -ne 'Running') { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $taskDeadline)
    if ($task.State -eq 'Running') { throw 'The previous dashboard task instance did not stop.' }
    Start-ScheduledTask -TaskName $DashboardTaskName
    $script:rolloutStage = 'dashboard_operations_probe'
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    do {
        try {
            $operations = Invoke-RestMethod -Uri 'http://127.0.0.1:13000/api/operations' -TimeoutSec 20
            if (Test-DashboardOperationsReady -Response $operations) {
                $script:rolloutStage = 'dashboard_route_contract_probe'
                $client = [System.Net.Http.HttpClient]::new()
                $client.Timeout = [TimeSpan]::FromSeconds(5)
                $content = $null
                $noOrderContent = $null
                try {
                    $content = [System.Net.Http.StringContent]::new('{}', [Text.Encoding]::UTF8, 'application/json')
                    $probe = $client.PostAsync('http://127.0.0.1:13000/api/paper-orders/manual-reconcile', $content).GetAwaiter().GetResult()
                    if ([int]$probe.StatusCode -ne 400) { throw 'The new manual reconciliation route did not reject an invalid request.' }
                    $noOrderContent = [System.Net.Http.StringContent]::new('{}', [Text.Encoding]::UTF8, 'application/json')
                    $noOrderProbe = $client.PostAsync('http://127.0.0.1:13000/api/paper-orders/confirm-no-order', $noOrderContent).GetAwaiter().GetResult()
                    if ([int]$noOrderProbe.StatusCode -ne 400) { throw 'The no-order confirmation route did not reject an invalid request.' }
                } finally {
                    if ($content) { $content.Dispose() }
                    if ($noOrderContent) { $noOrderContent.Dispose() }
                    $client.Dispose()
                }
                return
            }
        } catch {
            if ($_.Exception.Message -match 'new manual reconciliation route|no-order confirmation route') { throw }
        }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    $script:rolloutStage = 'dashboard_readiness_timeout'
    throw 'Dashboard did not become healthy with all trading locks verified.'
}

$script:rolloutStage = 'market_session_gate'
Assert-ClosedDomesticSession
$script:rolloutStage = 'trading_lock_gate'
Assert-TradingLocks
$script:rolloutStage = 'dashboard_task_validation'
$dashboardTask = Get-ScheduledTask -TaskName $DashboardTaskName -ErrorAction SilentlyContinue
if (-not $dashboardTask -or @($dashboardTask.Actions | Where-Object {
        [string]$_.Arguments -match [regex]::Escape($dashboardScript)
    }).Count -eq 0) {
    throw 'The registered dashboard task does not target this repository.'
}
if (-not (Test-Path -LiteralPath $nextCli)) { throw 'The local Next.js installation is missing.' }

$script:rolloutStage = 'dashboard_listener_stop'
$hadPreviousBuild = Test-Path -LiteralPath $buildPath
Stop-VerifiedDashboard
if ($hadPreviousBuild) {
    $script:rolloutStage = 'backup_existing_build'
    New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
    Move-Item -LiteralPath $buildPath -Destination $backupPath
}
$buildSucceeded = $false
try {
    $script:rolloutStage = 'production_build'
    Push-Location $dashboardRoot
    try { & npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw "Dashboard build exited with code $LASTEXITCODE." } }
    finally { Pop-Location }
    $buildSucceeded = $true
    $script:rolloutStage = 'dashboard_restart_and_verification'
    Start-And-VerifyDashboard
    Write-RolloutLog "Dashboard production build and supervisor restart passed after the closed KRX session. Trading locks verified; rollback build retained at $backupPath."
    Write-Output 'Dashboard build and restart verified; PAPER and REAL order locks remain disabled.'
} catch {
    $failureStage = $script:rolloutStage
    $failureType = $_.Exception.GetType().Name
    Write-RolloutLog ("Dashboard update failed at stage=$failureStage (exception_type=$failureType).")
    try {
        $script:rolloutStage = 'rollback_cleanup'
        Stop-VerifiedDashboard -AllowMissingSupervisorState
        if (Test-Path -LiteralPath $buildPath) {
            New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
            $failedBuildPath = Join-Path $backupDirectory ('.next-failed-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
            Move-Item -LiteralPath $buildPath -Destination $failedBuildPath
        }
        if ($hadPreviousBuild -and (Test-Path -LiteralPath $backupPath)) {
            $script:rolloutStage = 'rollback_supervisor_restart'
            Move-Item -LiteralPath $backupPath -Destination $buildPath
            Start-And-VerifyDashboard
            Write-RolloutLog 'Dashboard update failed; previous production build was restored and restarted.'
        } else {
            Write-RolloutLog 'Dashboard update failed and no previous production build was available for rollback.'
        }
    } catch {
        Write-RolloutLog ("Dashboard rollback/restart failed at stage=$script:rolloutStage (exception_type=$($_.Exception.GetType().Name)).")
    }
    if (-not $buildSucceeded) { Write-RolloutLog ("Dashboard build failed at stage=$failureStage (exception_type=$failureType).") }
    throw
}
