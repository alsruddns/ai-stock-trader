$ErrorActionPreference = 'Stop'

$startLocalPath = Join-Path $PSScriptRoot 'start-local.ps1'
if (-not (Select-String -Path $startLocalPath -Pattern 'lib\\local-api-binding\.ps1' -Quiet)) {
    throw 'The local Spring launcher must apply the loopback-only API binding.'
}
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile($startLocalPath, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count -gt 0) { throw 'The local Spring launcher has PowerShell syntax errors.' }
$runnerPath = Join-Path $PSScriptRoot 'run-local-stack-task.ps1'
$runnerTokens = $null
$runnerParseErrors = $null
$runnerAst = [System.Management.Automation.Language.Parser]::ParseFile($runnerPath, [ref]$runnerTokens, [ref]$runnerParseErrors)
if ($runnerParseErrors.Count -gt 0) { throw 'The local stack runner has PowerShell syntax errors.' }
$permanentFailureAst = $runnerAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-PermanentStackStartupFailure'
}, $true)
if ($null -eq $permanentFailureAst) { throw 'The permanent startup failure classifier was not found.' }
. ([ScriptBlock]::Create($permanentFailureAst.Extent.Text))
if (-not (Test-PermanentStackStartupFailure -StartupErrorMessage 'Spring Boot exited during startup (code 1).' -BackendErrorOutput 'PostgreSQL port 5432 listens on non-loopback address(es): 0.0.0.0.')) {
    throw 'A permanent PostgreSQL binding error should stop automatic startup retries.'
}
if (Test-PermanentStackStartupFailure -StartupErrorMessage 'Spring Boot exited during startup (code 1).' -BackendErrorOutput 'Connection refused') {
    throw 'A transient database connection error should remain retryable.'
}
if (Test-PermanentStackStartupFailure -StartupErrorMessage 'Spring Boot stopped unexpectedly (code 1).' -BackendErrorOutput 'PostgreSQL port 5432 listens on non-loopback address(es): 0.0.0.0.') {
    throw 'A runtime service exit should not be misclassified as the guarded startup error.'
}
$postgresBindingPath = Join-Path $PSScriptRoot 'lib\local-postgres-binding.ps1'
if (-not (Select-String -Path $startLocalPath -Pattern 'lib\\local-postgres-binding\.ps1' -Quiet)) {
    throw 'The local Spring launcher must enforce PostgreSQL loopback binding.'
}
$tokens = $null
$parseErrors = $null
$postgresBindingAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $postgresBindingPath, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) { throw 'The PostgreSQL loopback binding helper has PowerShell syntax errors.' }
. $postgresBindingPath
Assert-LocalPostgresBinding -ListenerAddresses @('127.0.0.1', '::1')
foreach ($unsafeAddress in @('0.0.0.0', '::', '192.168.1.20')) {
    $blocked = $false
    try { Assert-LocalPostgresBinding -ListenerAddresses @($unsafeAddress) }
    catch { $blocked = $_.Exception.Message -match 'non-loopback address' }
    if (-not $blocked) { throw "PostgreSQL listener address $unsafeAddress must be rejected." }
}
$missingPostgres = $false
try { Assert-LocalPostgresBinding -ListenerAddresses @() }
catch { $missingPostgres = $_.Exception.Message -match 'No PostgreSQL listener' }
if (-not $missingPostgres) { throw 'A missing PostgreSQL listener must be rejected.' }
$invalidPort = $false
try { Assert-LocalPostgresBinding -Port 0 -ListenerAddresses @('127.0.0.1') }
catch { $invalidPort = $true }
if (-not $invalidPort) { throw 'An invalid PostgreSQL port must be rejected.' }
$bindingPath = Join-Path $PSScriptRoot 'lib\local-api-binding.ps1'
$hadServerAddress = Test-Path Env:SERVER_ADDRESS
$previousServerAddress = $env:SERVER_ADDRESS
try {
    $env:SERVER_ADDRESS = '0.0.0.0'
    . $bindingPath
    if ($env:SERVER_ADDRESS -ne '127.0.0.1') {
        throw 'An external server bind address must be overridden by the local binding policy.'
    }
} finally {
    if ($hadServerAddress) { $env:SERVER_ADDRESS = $previousServerAddress }
    else { Remove-Item Env:SERVER_ADDRESS -ErrorAction SilentlyContinue }
}

$restartPath = Join-Path $PSScriptRoot 'restart-local-stack-task.ps1'
$tokens = $null
$parseErrors = $null
$restartAst = [System.Management.Automation.Language.Parser]::ParseFile($restartPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw 'The local stack restart script has PowerShell syntax errors.' }
$restartPostgresGateAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Assert-PostgresLoopbackForRestart'
}, $true)
if ($null -eq $restartPostgresGateAst) { throw 'The PostgreSQL restart preflight was not found.' }
. (Join-Path $PSScriptRoot 'lib\local-postgres-binding.ps1')
. ([ScriptBlock]::Create($restartPostgresGateAst.Extent.Text))
Assert-PostgresLoopbackForRestart -ListenerAddresses @('127.0.0.1', '::1')
$wildcardPostgresBlocked = $false
try { Assert-PostgresLoopbackForRestart -ListenerAddresses @('0.0.0.0', '::') }
catch { $wildcardPostgresBlocked = $_.Exception.Message -match 'non-loopback address' }
if (-not $wildcardPostgresBlocked) { throw 'A non-loopback PostgreSQL listener must block restart before stopping the local APIs.' }
$missingPostgresBlocked = $false
try { Assert-PostgresLoopbackForRestart -ListenerAddresses @() }
catch { $missingPostgresBlocked = $_.Exception.Message -match 'No PostgreSQL listener' }
if (-not $missingPostgresBlocked) { throw 'A missing PostgreSQL listener must block restart before stopping the local APIs.' }
$gateAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-SessionResponsesFreshAndClosed'
}, $true)
if ($null -eq $gateAst) { throw 'The closed-session restart gate was not found.' }
. ([ScriptBlock]::Create($gateAst.Extent.Text))
$identityAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-TrackedProcessIdentity'
}, $true)
$parentageAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-TrackedProcessParentage'
}, $true)
$paperSessionAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-PaperSessionRestored'
}, $true)
$tradingLocksAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-LocalTradingLocks'
}, $true)
$frameDiagnosticsAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-PaperFrameDiagnosticsReady'
}, $true)
$quoteQualityDiagnosticsAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-PaperQuoteQualityDiagnosticsReady'
}, $true)
$marketDataDiagnosticsAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-PaperMarketDataDiagnosticsReady'
}, $true)
if ($null -eq $identityAst -or $null -eq $parentageAst -or $null -eq $paperSessionAst -or
    $null -eq $tradingLocksAst -or $null -eq $frameDiagnosticsAst -or $null -eq $quoteQualityDiagnosticsAst -or
    $null -eq $marketDataDiagnosticsAst) {
    throw 'Process ownership or paper-session restoration checks were not found.'
}
$repoRoot = Split-Path -Parent $PSScriptRoot
$script:restartScriptDirectory = $PSScriptRoot
. ([ScriptBlock]::Create($identityAst.Extent.Text))
. ([ScriptBlock]::Create($parentageAst.Extent.Text))
. ([ScriptBlock]::Create($paperSessionAst.Extent.Text))
. ([ScriptBlock]::Create($tradingLocksAst.Extent.Text))
. ([ScriptBlock]::Create($frameDiagnosticsAst.Extent.Text))
. ([ScriptBlock]::Create($quoteQualityDiagnosticsAst.Extent.Text))
. ([ScriptBlock]::Create($marketDataDiagnosticsAst.Extent.Text))

$lockedPython = [pscustomobject]@{ status = 'ok'; live_trading_enabled = $false; paper_automation_enabled = $false }
$lockedSpring = [pscustomobject]@{
    tradingMode = 'paper'; liveTrading = 'disabled'; paperAutomationEnabled = $false
    kisPaperOrdersEnabled = $false; kisRealOrdersEnabled = $false
    kisRealAccountReadsEnabled = $false; kisRealReconciliationEnabled = $false
    realProtectiveExitManagerEnabled = $false; kisRealProtectiveExitAutomationEnabled = $false
}
if (-not (Test-LocalTradingLocks $lockedPython $lockedSpring)) {
    throw 'The fully locked local configuration should permit a guarded restart.'
}
$unsafeSpringFields = @(
    'tradingMode', 'liveTrading', 'paperAutomationEnabled', 'kisPaperOrdersEnabled', 'kisRealOrdersEnabled',
    'kisRealAccountReadsEnabled', 'kisRealReconciliationEnabled',
    'realProtectiveExitManagerEnabled', 'kisRealProtectiveExitAutomationEnabled'
)
foreach ($field in $unsafeSpringFields) {
    $unsafeSpring = $lockedSpring.PSObject.Copy()
    if ($field -eq 'tradingMode') { $unsafeSpring.$field = 'real' }
    elseif ($field -eq 'liveTrading') { $unsafeSpring.$field = 'enabled' }
    else { $unsafeSpring.$field = $true }
    if (Test-LocalTradingLocks $lockedPython $unsafeSpring) {
        throw "The restart safety gate accepted an enabled Spring setting: $field."
    }
}
$unsafePython = $lockedPython.PSObject.Copy()
$unsafePython.live_trading_enabled = $true
if (Test-LocalTradingLocks $unsafePython $lockedSpring) {
    throw 'The restart safety gate accepted Python live trading enabled.'
}
$unsafePython = $lockedPython.PSObject.Copy()
$unsafePython.paper_automation_enabled = $true
if (Test-LocalTradingLocks $unsafePython $lockedSpring) {
    throw 'The restart safety gate accepted Python PAPER automation enabled.'
}
$unhealthyPython = $lockedPython.PSObject.Copy()
$unhealthyPython.status = 'error'
if (Test-LocalTradingLocks $unhealthyPython $lockedSpring) {
    throw 'The restart safety gate accepted unhealthy Python API status.'
}
$diagnosticSession = [pscustomobject]@{
    frames_received_by_feed = [pscustomobject]@{ paper = 0; real = 0 }
    frame_transaction_counts_by_feed = [pscustomobject]@{
        paper = [pscustomobject]@{ H0STCNT0 = 0; H0STASP0 = 0; HDFSCNT0 = 0; OTHER = 0 }
        real = [pscustomobject]@{ H0STCNT0 = 0; H0STASP0 = 0; HDFSCNT0 = 0; OTHER = 0 }
    }
    orderbook_quote_quality_by_feed = [pscustomobject]@{
        paper = [pscustomobject]@{ usable = 0; empty_or_nonpositive = 0; crossed = 0; stale_rejected = 0 }
        real = [pscustomobject]@{ usable = 0; empty_or_nonpositive = 0; crossed = 0; stale_rejected = 0 }
    }
    market_data_diagnostics_by_feed = [pscustomobject]@{
        paper = [pscustomobject]@{
            trade_market_class_filtered = 0; orderbook_market_class_filtered = 0
            trade_parse_errors = 0; orderbook_parse_errors = 0
            trade_symbol_mismatches = 0; orderbook_symbol_mismatches = 0
            encrypted_market_frames_rejected = 0
        }
        real = [pscustomobject]@{
            trade_market_class_filtered = 0; orderbook_market_class_filtered = 0
            trade_parse_errors = 0; orderbook_parse_errors = 0
            trade_symbol_mismatches = 0; orderbook_symbol_mismatches = 0
            encrypted_market_frames_rejected = 0
        }
    }
}
if (-not (Test-PaperFrameDiagnosticsReady $diagnosticSession)) {
    throw 'A complete per-feed frame diagnostics payload should pass.'
}
if (-not (Test-PaperQuoteQualityDiagnosticsReady $diagnosticSession)) {
    throw 'A complete per-feed quote-quality diagnostics payload should pass.'
}
if (-not (Test-PaperMarketDataDiagnosticsReady $diagnosticSession)) {
    throw 'A complete per-feed market-data processing diagnostics payload should pass.'
}
$missingMarketDataDiagnostics = $diagnosticSession.PSObject.Copy()
$missingMarketDataDiagnostics.market_data_diagnostics_by_feed = $null
if (Test-PaperMarketDataDiagnosticsReady $missingMarketDataDiagnostics) {
    throw 'Missing per-feed market-data processing diagnostics must block declaring diagnostics ready.'
}
$negativeMarketDataDiagnostics = $diagnosticSession.PSObject.Copy()
$negativeMarketDataDiagnostics.market_data_diagnostics_by_feed.paper.trade_parse_errors = -1
if (Test-PaperMarketDataDiagnosticsReady $negativeMarketDataDiagnostics) {
    throw 'Negative market-data processing counters must be rejected.'
}
$diagnosticSession.market_data_diagnostics_by_feed.paper.trade_parse_errors = 0
$missingQuoteQuality = $diagnosticSession.PSObject.Copy()
$missingQuoteQuality.orderbook_quote_quality_by_feed = $null
if (Test-PaperQuoteQualityDiagnosticsReady $missingQuoteQuality) {
    throw 'Missing per-feed quote-quality counters must block declaring diagnostics ready.'
}
$negativeQuoteQuality = $diagnosticSession.PSObject.Copy()
$negativeQuoteQuality.orderbook_quote_quality_by_feed = [pscustomobject]@{
    paper = [pscustomobject]@{ usable = 0; empty_or_nonpositive = -1; crossed = 0; stale_rejected = 0 }
    real = $diagnosticSession.orderbook_quote_quality_by_feed.real
}
if (Test-PaperQuoteQualityDiagnosticsReady $negativeQuoteQuality) {
    throw 'Negative quote-quality counters must block declaring diagnostics ready.'
}
$missingFrameCounts = $diagnosticSession.PSObject.Copy()
$missingFrameCounts.frames_received_by_feed = $null
if (Test-PaperFrameDiagnosticsReady $missingFrameCounts) {
    throw 'Missing per-feed frame counts must block declaring diagnostics ready.'
}
$missingTransactionId = $diagnosticSession.PSObject.Copy()
$missingTransactionId.frame_transaction_counts_by_feed.paper = [pscustomobject]@{
    H0STCNT0 = 0; H0STASP0 = 0; HDFSCNT0 = 0
}
if (Test-PaperFrameDiagnosticsReady $missingTransactionId) {
    throw 'Missing transaction counters must block declaring diagnostics ready.'
}

. (Join-Path $PSScriptRoot 'paper-session-monitor-status.ps1')
$diagnosticSession.frame_transaction_counts_by_feed.paper = [pscustomobject]@{
    H0STCNT0 = 0; H0STASP0 = 0; HDFSCNT0 = 0; OTHER = 0
}
$monitorDiagnostics = Get-MonitorFeedDiagnostics $diagnosticSession 'paper'
if (-not $monitorDiagnostics.paper.available -or $monitorDiagnostics.paper.frames_received -ne 0 -or
    $monitorDiagnostics.paper.transaction_counts.H0STCNT0 -ne 0 -or
    -not $monitorDiagnostics.paper.quote_quality_available -or
    $monitorDiagnostics.paper.quote_quality.usable -ne 0 -or
    -not $monitorDiagnostics.paper.processing_diagnostics_available -or
    $monitorDiagnostics.paper.processing_diagnostics.trade_parse_errors -ne 0) {
    throw 'The WebSocket monitor should record selected-feed frame and transaction counters.'
}
$bothMonitorDiagnostics = Get-MonitorFeedDiagnostics $diagnosticSession ''
if (-not $bothMonitorDiagnostics.paper.available -or -not $bothMonitorDiagnostics.real.available) {
    throw 'The WebSocket monitor should record diagnostics for both feeds when no feed is selected.'
}
$staleMonitorDiagnostics = Get-MonitorFeedDiagnostics ([pscustomobject]@{}) 'paper'
if ($staleMonitorDiagnostics.paper.available -or $null -ne $staleMonitorDiagnostics.paper.frames_received) {
    throw 'A stale runtime without frame counters must be reported as unavailable, not zero.'
}
$invalidMonitorDiagnostics = [pscustomobject]@{
    frames_received_by_feed = [pscustomobject]@{ paper = -1; real = 0 }
    frame_transaction_counts_by_feed = $diagnosticSession.frame_transaction_counts_by_feed
}
if ((Get-MonitorFeedDiagnostics $invalidMonitorDiagnostics 'paper').paper.available) {
    throw 'Negative WebSocket diagnostic counters must be rejected.'
}

$now = [DateTimeOffset]::UtcNow
$closedSessions = @(
    [pscustomobject]@{ market = 'KR'; is_open = $false; evaluated_at = $now.ToString('o') }
    [pscustomobject]@{ market = 'US'; is_open = $false; evaluated_at = $now.ToString('o') }
)
$openUs = @($closedSessions[0], [pscustomobject]@{ market = 'US'; is_open = $true; evaluated_at = $now.ToString('o') })
$staleUs = @($closedSessions[0], [pscustomobject]@{ market = 'US'; is_open = $false; evaluated_at = $now.AddSeconds(-31).ToString('o') })
$futureKr = @([pscustomobject]@{ market = 'KR'; is_open = $false; evaluated_at = $now.AddSeconds(11).ToString('o') }, $closedSessions[1])

if (-not (Test-SessionResponsesFreshAndClosed $closedSessions $now)) { throw 'Fresh closed-session responses should allow restart.' }
if (Test-SessionResponsesFreshAndClosed $openUs $now) { throw 'An open exchange must block restart.' }
if (Test-SessionResponsesFreshAndClosed $staleUs $now) { throw 'A stale exchange response must block restart.' }
if (Test-SessionResponsesFreshAndClosed $futureKr $now) { throw 'A future-dated exchange response must block restart.' }
if (Test-SessionResponsesFreshAndClosed @($closedSessions[0]) $now) { throw 'A missing exchange response must block restart.' }

$runnerProcess = [pscustomobject]@{
    CommandLine = "powershell.exe -File `"$(Join-Path $PSScriptRoot 'run-local-stack-task.ps1')`""
    ExecutablePath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    ProcessId = 100
    ParentProcessId = 1
}
$backendProcess = [pscustomobject]@{
    CommandLine = "powershell.exe -File `"$(Join-Path $PSScriptRoot 'start-local.ps1')`""
    ExecutablePath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    ProcessId = 101
    ParentProcessId = 100
}
$marketAiProcess = [pscustomobject]@{
    CommandLine = 'python.exe -m uvicorn app.main:app --host 127.0.0.1'
    ExecutablePath = Join-Path $repoRoot 'services\market-ai\.venv\Scripts\python.exe'
    ProcessId = 102
    ParentProcessId = 100
}
if (-not (Test-TrackedProcessIdentity $runnerProcess 'runner')) { throw 'Expected repository runner process was rejected.' }
if (-not (Test-TrackedProcessIdentity $backendProcess 'backend')) { throw 'Expected repository backend process was rejected.' }
if (-not (Test-TrackedProcessIdentity $marketAiProcess 'market-ai')) { throw 'Expected repository Python process was rejected.' }
$wrongPython = $marketAiProcess.PSObject.Copy()
$wrongPython.ExecutablePath = 'C:\other\python.exe'
if (Test-TrackedProcessIdentity $wrongPython 'market-ai') { throw 'Python from another path must be rejected.' }
$unrelatedRunner = $runnerProcess.PSObject.Copy()
$unrelatedRunner.CommandLine = 'powershell.exe -File C:\other\run-local-stack-task.ps1'
if (Test-TrackedProcessIdentity $unrelatedRunner 'runner') { throw 'Runner from another repository must be rejected.' }
if (-not (Test-TrackedProcessParentage $runnerProcess $backendProcess $marketAiProcess 100)) { throw 'Expected process parentage was rejected.' }
$orphanedRunnerChildren = $backendProcess.PSObject.Copy()
if (-not (Test-TrackedProcessParentage $null $orphanedRunnerChildren $marketAiProcess 100)) {
    throw 'Verified API children should remain restartable after their runner exits.'
}
$orphanBackend = $backendProcess.PSObject.Copy()
$orphanBackend.ParentProcessId = 999
if (Test-TrackedProcessParentage $runnerProcess $orphanBackend $marketAiProcess 100) { throw 'Unexpected process parentage must be rejected.' }

$previousPaperSession = [pscustomobject]@{
    enabled = $true; state = 'RUNNING'; orders_enabled = $false
    automation_state = 'DISABLED'; bar_storage_state = 'RUNNING'
}
$restoredPaperSession = $previousPaperSession.PSObject.Copy()
if (-not (Test-PaperSessionRestored $restoredPaperSession $previousPaperSession)) {
    throw 'A restored paper session monitor and bar store should pass.'
}
$stoppedPaperSession = $restoredPaperSession.PSObject.Copy()
$stoppedPaperSession.state = 'STOPPED'
if (Test-PaperSessionRestored $stoppedPaperSession $previousPaperSession) {
    throw 'An enabled monitor that failed to restart must be rejected.'
}
$missingStorage = $restoredPaperSession.PSObject.Copy()
$missingStorage.bar_storage_state = 'ERROR'
if (Test-PaperSessionRestored $missingStorage $previousPaperSession) {
    throw 'A previously running bar store that failed to restart must be rejected.'
}
$ordersEnabledSession = $restoredPaperSession.PSObject.Copy()
$ordersEnabledSession.orders_enabled = $true
if (Test-PaperSessionRestored $ordersEnabledSession $previousPaperSession) {
    throw 'Paper order submission must remain disabled after restart.'
}
$automationEnabledSession = $restoredPaperSession.PSObject.Copy()
$automationEnabledSession.automation_state = 'RUNNING'
if (Test-PaperSessionRestored $automationEnabledSession $previousPaperSession) {
    throw 'Paper automation must remain disabled after restart.'
}
$disabledMonitor = [pscustomobject]@{
    enabled = $false; state = 'DISABLED'; orders_enabled = $false
    automation_state = 'DISABLED'; bar_storage_state = 'DISABLED'
}
if (-not (Test-PaperSessionRestored $disabledMonitor $disabledMonitor)) {
    throw 'An intentionally disabled monitor should not block API restart.'
}

Write-Output 'Local stack restart session, process ownership, and monitor recovery checks passed.'
