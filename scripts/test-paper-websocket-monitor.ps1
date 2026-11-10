param(
    [string]$ApiBaseUrl = 'http://127.0.0.1:18000',
    [ValidateRange(10, 36000)] [int]$DurationSeconds = 60,
    [ValidateRange(1, 60)] [int]$PollIntervalSeconds = 15,
    [ValidateRange(30, 3600)] [int]$MaxNoTickSeconds = 300,
    [ValidateRange(0, 36000)] [int]$MinimumBarsDelta = 0,
    [ValidateRange(1, 300)] [int]$StartupTimeoutSeconds = 120,
    [ValidateRange(0, 600)] [int]$PostCloseGraceSeconds = 180,
    [ValidateSet('paper', 'real')] [string]$Feed,
    [string]$EvidencePath,
    [switch]$RequireBothFeeds,
    [switch]$RequireSessionClose
)

$ErrorActionPreference = 'Stop'
if ($Feed -eq 'real') {
    throw 'Current market-session validation scope is domestic KR only. No US feed was monitored.'
}
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'paper-session-monitor-status.ps1')
. (Join-Path $PSScriptRoot 'lib\exchange-session-monitor.ps1')
if ([string]::IsNullOrWhiteSpace($EvidencePath)) {
    $EvidencePath = Join-Path $repoRoot '.local\validation\paper-websocket-samples.jsonl'
}
$evidenceDirectory = Split-Path -Parent $EvidencePath
$progressName = '{0}.progress.jsonl' -f [IO.Path]::GetFileNameWithoutExtension($EvidencePath)
$ProgressPath = Join-Path $evidenceDirectory $progressName

function Write-ProgressRecord($Record, $Status = $null) {
    if ($evidenceDirectory) { New-Item -ItemType Directory -Force -Path $evidenceDirectory | Out-Null }
    $streamFailureDiagnostic = Get-MonitorStreamFailureDiagnostic $Status
    foreach ($name in $streamFailureDiagnostic.Keys) {
        $Record[$name] = $streamFailureDiagnostic[$name]
    }
    $Record.symbol_market_data = Get-MonitorSymbolMarketData $Status $Feed
    if ($script:monitorInitialStatus) {
        $Record.symbol_market_data_delta = Get-MonitorSymbolMarketDataDelta `
            $script:monitorInitialStatus $Status $Feed
    }
    [System.IO.File]::AppendAllText($ProgressPath,
        ($Record | ConvertTo-Json -Compress -Depth 5) + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false))
}

function Get-SessionStatus {
    try {
        return Invoke-RestMethod -Uri "$ApiBaseUrl/api/v1/paper/session" -TimeoutSec 5
    } catch {
        throw 'Could not read the local paper-session status endpoint.'
    }
}

function Get-SessionQualityEvidence($Status, $Session, [string]$BaseUrl, [string]$SelectedFeed, [int]$ExpectedBars) {
    $market = [string]$Session.market
    $sessionDate = [string]$Session.session_date
    $symbols = @($Status.decisions.PSObject.Properties |
        ForEach-Object { $_.Value } |
        Where-Object { $_.market -eq $market -and $_.symbol } |
        ForEach-Object { ([string]$_.symbol).Trim().ToUpperInvariant() } |
        Select-Object -Unique)
    if ($symbols.Count -lt 1 -or $symbols.Count -gt 50) {
        throw 'The local session monitor has no bounded watchlist for final stored-data verification.'
    }
    $query = @('session_date=' + [Uri]::EscapeDataString($sessionDate)) + @($symbols | ForEach-Object {
        'symbols=' + [Uri]::EscapeDataString($_)
    })
    $qualityUrl = "${BaseUrl}/api/v1/market/session-quality/${market}?" + ($query -join '&')
    $expectedSource = if ($SelectedFeed -eq 'paper') { 'KIS_PAPER_WEBSOCKET' } else { 'KIS_REAL_US_WEBSOCKET' }
    $quality = Invoke-RestMethod -Uri $qualityUrl -TimeoutSec 10
    $diagnostics = @(Get-SessionQualityDiagnostics $quality)
    $verified = @()
    $validationStatus = 'incomplete'
    try {
        $verified = @(Assert-ExchangeSessionQuality $quality $market $sessionDate $symbols $ExpectedBars $expectedSource)
        $validationStatus = 'verified'
    } catch {
        # Preserve safe quality counts so a strict readiness failure is diagnosable.
    }
    return [pscustomobject]@{
        diagnostics = $diagnostics
        verified = $verified
        validation_status = $validationStatus
    }
}

function Get-SelectedExchangeSessionWindow {
    if (-not $Feed) { return $null }
    $market = if ($Feed -eq 'paper') { 'KR' } else { 'US' }
    try {
        $response = Invoke-RestMethod -Uri "$ApiBaseUrl/api/v1/market/sessions/$market" -TimeoutSec 5
        return ConvertTo-ExchangeSessionWindow $response
    } catch {
        throw "Could not verify today's $market exchange session from the local market calendar."
    }
}

function Wait-ForSafeRunningStatus {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $health = $null
        try { $health = Invoke-RestMethod -Uri "$ApiBaseUrl/api/v1/health" -TimeoutSec 5 } catch { }
        if (-not $health) { Start-Sleep -Seconds 2; continue }
        if ($health.status -ne 'ok' -or $health.live_trading_enabled -or $health.paper_automation_enabled) {
            throw 'Local API must be healthy with live trading and paper automation disabled.'
        }

        $status = $null
        try { $status = Get-SessionStatus } catch { }
        if (-not $status) { Start-Sleep -Seconds 2; continue }
        Assert-MonitorSafetyLocks $status
        if (-not (Test-MonitorFeedReady $status $Feed ([bool]$RequireBothFeeds))) {
            Start-Sleep -Seconds 2
            continue
        }
        Assert-SafeRunningStatus $status $Feed ([bool]$RequireBothFeeds)
        return $status
    }
    throw "Local market-data feed did not become ready within $StartupTimeoutSeconds seconds."
}

$monitorRunStartedAtUtc = [DateTimeOffset]::UtcNow
$monitorStage = 'startup_readiness'
try {
$initial = Wait-ForSafeRunningStatus
$script:monitorInitialStatus = $initial
$monitorStage = 'exchange_calendar'
$exchangeSession = Get-SelectedExchangeSessionWindow
$initialTicks = if ($Feed) { [int]$initial.ticks_received_by_feed.$Feed } else { [int]$initial.ticks_received }
$initialBars = if ($Feed) { [int]$initial.bars_closed_by_feed.$Feed } else { [int]$initial.bars_closed }
$previousTicks = $initialTicks
$startedAtUtc = [DateTimeOffset]::UtcNow
$sessionOpenBarsBaseline = $null
$awaitingSessionOpenBaseline = [bool]($exchangeSession -and $startedAtUtc -lt $exchangeSession.open_utc)
$lastPreOpenBars = if ($awaitingSessionOpenBaseline) { $initialBars } else { $null }
$initialReceiveAt = if ($Feed) { $initial.last_tick_received_at_by_feed.$Feed } else { $initial.last_tick_received_at }
if ($Feed -and $initialReceiveAt) {
    $initialReceivedAtUtc = [DateTimeOffset]::Parse([string]$initialReceiveAt).ToUniversalTime()
    if (-not (Test-ExchangeSessionTickTimestamp $initialReceivedAtUtc $exchangeSession)) { $initialReceiveAt = $null }
}
$hasReceiveTimestamp = [bool]$initialReceiveAt
$lastTickReceivedAt = if ($hasReceiveTimestamp) {
    [DateTimeOffset]::Parse([string]$initialReceiveAt).ToUniversalTime()
} else { $startedAtUtc }
$maxObservedNoTickSeconds = 0.0
$preOpenSilenceSeconds = 0.0
$closingAuctionSilenceSeconds = 0.0
$sessionCloseReached = $false
$sessionQualityEvidence = @()
$sessionQualityDiagnostics = @()
$sessionQualityDiagnosticsRecordedAtUtc = $null
$sessionQualityStatus = 'not_checked'
$sessionStatusAvailable = $true
$postCloseWaitSeconds = 0.0
$monitorStage = 'stream_monitoring'
$validationScope = Get-ExchangeSessionValidationScope ([bool]$RequireSessionClose)
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($DurationSeconds)
$latest = $initial
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$nextProgressSeconds = 60
$initialProgress = [ordered]@{
    validation_scope = $validationScope
    recorded_at_utc = $startedAtUtc.ToString('o')
    requested_seconds = $DurationSeconds
    selected_feed = if ($Feed) { $Feed } else { 'any' }
    session_phase = Get-ExchangeSessionPhase $startedAtUtc $exchangeSession
    market_session = if ($exchangeSession) { $exchangeSession.market } else { $null }
    market_session_date = if ($exchangeSession) { $exchangeSession.session_date } else { $null }
    market_session_open_utc = if ($exchangeSession) { $exchangeSession.open_utc.ToString('o') } else { $null }
    market_session_close_utc = if ($exchangeSession) { $exchangeSession.close_utc.ToString('o') } else { $null }
    elapsed_seconds = 0
    ticks_delta = 0
    bars_delta = 0
    max_observed_no_tick_seconds = 0.0
    feeds = $initial.stream_connections
    feed_diagnostics = Get-MonitorFeedDiagnostics $initial $Feed
    feed_diagnostics_delta = Get-MonitorFeedDiagnosticsDelta $initial $initial $Feed
    orders_enabled = $false
    automation_state = $initial.automation_state
    result = 'running'
}
Write-ProgressRecord $initialProgress $initial
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $remaining = [int][Math]::Ceiling(($deadline - [DateTimeOffset]::UtcNow).TotalSeconds)
    Start-Sleep -Seconds ([Math]::Min($PollIntervalSeconds, [Math]::Max(1, $remaining)))
    $sampledAtUtc = [DateTimeOffset]::UtcNow
    $sessionPhase = Get-ExchangeSessionPhase $sampledAtUtc $exchangeSession
    if ($sessionPhase -eq 'CLOSED') {
        $latest = Get-SessionStatus
        Assert-SafeRunningStatus $latest $Feed ([bool]$RequireBothFeeds)
        $sessionCloseReached = $true
        break
    }
    $latest = Get-SessionStatus
    Assert-SafeRunningStatus $latest $Feed ([bool]$RequireBothFeeds) ([bool]$Feed)

    $currentTicks = if ($Feed) { [int]$latest.ticks_received_by_feed.$Feed } else { [int]$latest.ticks_received }
    if ($currentTicks -lt $previousTicks) {
        throw 'WebSocket tick counter reset during the sample; the monitored service likely restarted.'
    }
    if ($currentTicks -gt $previousTicks) {
        $previousTicks = $currentTicks
    }
    $currentReceiveAt = if ($Feed) { $latest.last_tick_received_at_by_feed.$Feed } else { $latest.last_tick_received_at }
    if ($currentReceiveAt) {
        $observedTickReceivedAt = [DateTimeOffset]::Parse([string]$currentReceiveAt).ToUniversalTime()
        $isOutsideSelectedSession = $Feed -and
            -not (Test-ExchangeSessionTickTimestamp $observedTickReceivedAt $exchangeSession)
        if (-not $isOutsideSelectedSession -and $observedTickReceivedAt -lt $lastTickReceivedAt) {
            throw 'The last tick receive timestamp moved backwards during the sample.'
        }
        if (-not $isOutsideSelectedSession) {
            $lastTickReceivedAt = $observedTickReceivedAt
            $hasReceiveTimestamp = $true
        }
    }
    if ($exchangeSession -and $sampledAtUtc -ge $exchangeSession.open_utc -and
        $lastTickReceivedAt -lt $exchangeSession.open_utc) {
        # A premarket or previous-session tick must not make the open look stale immediately.
        $lastTickReceivedAt = $exchangeSession.open_utc
    }
    $noTickSeconds = ([DateTimeOffset]::UtcNow - $lastTickReceivedAt).TotalSeconds
    if ($sessionPhase -eq 'PRE_OPEN') {
        $preOpenSilenceSeconds = [Math]::Max($preOpenSilenceSeconds, ([DateTimeOffset]::UtcNow - $startedAtUtc).TotalSeconds)
    } elseif ($sessionPhase -eq 'CLOSING_AUCTION') {
        # KRX does not run continuous matching during the 15:20–15:30 closing call auction.
        # The last continuous-trade tick can therefore be older than the feed-stall threshold.
        $closingAuctionSilenceSeconds = [Math]::Max($closingAuctionSilenceSeconds, $noTickSeconds)
    } else {
        $maxObservedNoTickSeconds = [Math]::Max($maxObservedNoTickSeconds, $noTickSeconds)
    }
    if ((Test-ExchangeSessionNoTickThresholdApplies $sessionPhase) -and $noTickSeconds -gt $MaxNoTickSeconds) {
        $deferNoTickFailure = Test-ExchangeSessionMonitorShouldDeferNoTickFailure `
            $noTickSeconds $MaxNoTickSeconds ([bool]$RequireSessionClose)
        if (-not $deferNoTickFailure) {
            throw "No WebSocket ticks arrived for more than $MaxNoTickSeconds seconds."
        }
    }

    $currentBars = if ($Feed) { [int]$latest.bars_closed_by_feed.$Feed } else { [int]$latest.bars_closed }
    if ($exchangeSession) {
        # Keep the last pre-open count. The first poll after open may already include
        # the opening minute, which must count toward a complete regular session.
        $baselineState = Update-ExchangeSessionOpenBarsBaseline `
            $awaitingSessionOpenBaseline $sampledAtUtc $exchangeSession.open_utc `
            $currentBars $lastPreOpenBars $initialBars
        $awaitingSessionOpenBaseline = $baselineState.awaiting
        if ($null -ne $baselineState.baseline) {
            $sessionOpenBarsBaseline = $baselineState.baseline
        }
        if ($sampledAtUtc -lt $exchangeSession.open_utc) {
            $lastPreOpenBars = $currentBars
        }
    }
    $sessionBarCount = Get-ExchangeSessionBarsDelta $currentBars $initialBars $sessionOpenBarsBaseline
    $sessionBarsDelta = $sessionBarCount.delta
    $progress = [ordered]@{
        validation_scope = $validationScope
        recorded_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        requested_seconds = $DurationSeconds
        elapsed_seconds = [int]$stopwatch.Elapsed.TotalSeconds
        selected_feed = if ($Feed) { $Feed } else { 'any' }
        ticks_delta = $currentTicks - $initialTicks
        bars_delta = $sessionBarsDelta
        session_bars_baseline = $sessionBarCount.baseline
        minimum_bars_delta = $MinimumBarsDelta
        max_observed_no_tick_seconds = [Math]::Round($maxObservedNoTickSeconds, 1)
        preopen_silence_seconds = [Math]::Round($preOpenSilenceSeconds, 1)
        session_phase = $sessionPhase
        market_session = if ($exchangeSession) { $exchangeSession.market } else { $null }
        market_session_date = if ($exchangeSession) { $exchangeSession.session_date } else { $null }
        market_session_open_utc = if ($exchangeSession) { $exchangeSession.open_utc.ToString('o') } else { $null }
        market_session_close_utc = if ($exchangeSession) { $exchangeSession.close_utc.ToString('o') } else { $null }
        closing_auction_silence_seconds = [Math]::Round($closingAuctionSilenceSeconds, 1)
        feeds = $latest.stream_connections
        feed_diagnostics = Get-MonitorFeedDiagnostics $latest $Feed
        feed_diagnostics_delta = Get-MonitorFeedDiagnosticsDelta $initial $latest $Feed
        orders_enabled = [bool]$latest.orders_enabled
        automation_state = $latest.automation_state
        result = 'running'
    }
    Write-ProgressRecord $progress $latest

    if ($stopwatch.Elapsed.TotalSeconds -ge $nextProgressSeconds) {
        $tickDelta = $currentTicks - $initialTicks
        Write-Output "KIS feed monitor running: scope=$validationScope feed=$(if ($Feed) { $Feed } else { 'any' }) elapsed_seconds=$([int]$stopwatch.Elapsed.TotalSeconds) ticks_delta=$tickDelta bars_delta=$sessionBarsDelta."
        $nextProgressSeconds += 60
    }
}

if ($RequireSessionClose -and -not $sessionCloseReached) {
    throw 'The monitored exchange session did not reach its calendar-confirmed close.'
}
if ($RequireSessionClose) {
    $monitorStage = 'post_close_validation'
    $sessionQualityProbe = {
        $status = $latest
        try {
            $status = Get-SessionStatus
            $sessionStatusAvailable = $true
        } catch {
            $sessionStatusAvailable = $false
            $sessionQualityStatus = 'unavailable'
            Write-ProgressRecord ([ordered]@{
                validation_scope = $validationScope
                recorded_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
                requested_seconds = $DurationSeconds
                elapsed_seconds = [int]$stopwatch.Elapsed.TotalSeconds
                selected_feed = if ($Feed) { $Feed } else { 'any' }
                session_phase = 'CLOSED'
                market_session = $exchangeSession.market
                market_session_date = $exchangeSession.session_date
                session_status_available = $false
                session_quality_ready = $false
                session_quality_status = $sessionQualityStatus
                session_quality_diagnostics = $sessionQualityDiagnostics
                session_quality_diagnostics_recorded_at_utc = $sessionQualityDiagnosticsRecordedAtUtc
                feeds = if ($status) { $status.stream_connections } else { $null }
                feed_diagnostics = if ($status) { Get-MonitorFeedDiagnostics $status $Feed } else { $null }
                feed_diagnostics_delta = if ($status) { Get-MonitorFeedDiagnosticsDelta $initial $status $Feed } else { $null }
                orders_enabled = if ($status) { [bool]$status.orders_enabled } else { $false }
                automation_state = if ($status) { $status.automation_state } else { $null }
                result = 'session_status_unavailable'
            }) $status
            return [pscustomobject]@{
                ready = $false
                status = $status
                status_available = $false
                quality = @()
                quality_diagnostics = $sessionQualityDiagnostics
                quality_diagnostics_recorded_at_utc = $sessionQualityDiagnosticsRecordedAtUtc
                quality_status = $sessionQualityStatus
            }
        }
        Assert-SafeRunningStatus $status $Feed ([bool]$RequireBothFeeds)
        $qualityEvidence = @()
        $qualityDiagnostics = $sessionQualityDiagnostics
        $qualityStatus = $sessionQualityStatus
        try {
            $qualitySnapshot = Get-SessionQualityEvidence $status $exchangeSession $ApiBaseUrl $Feed $MinimumBarsDelta
            $qualityEvidence = @($qualitySnapshot.verified)
            $qualityDiagnostics = @($qualitySnapshot.diagnostics)
            $qualityStatus = [string]$qualitySnapshot.validation_status
            $sessionQualityDiagnostics = $qualityDiagnostics
            $sessionQualityDiagnosticsRecordedAtUtc = [DateTimeOffset]::UtcNow.ToString('o')
            $sessionQualityStatus = $qualityStatus
        } catch {
            # The final minute and storage-quality view can settle just after the exchange close.
            $qualityStatus = 'unavailable'
            $sessionQualityStatus = $qualityStatus
        }
        $currentBars = if ($Feed) { [int]$status.bars_closed_by_feed.$Feed } else { [int]$status.bars_closed }
        $barCount = Get-ExchangeSessionBarsDelta $currentBars $initialBars $sessionOpenBarsBaseline
        Write-ProgressRecord ([ordered]@{
            validation_scope = $validationScope
            recorded_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
            requested_seconds = $DurationSeconds
            elapsed_seconds = [int]$stopwatch.Elapsed.TotalSeconds
            selected_feed = if ($Feed) { $Feed } else { 'any' }
            session_phase = 'CLOSED'
            market_session = $exchangeSession.market
            market_session_date = $exchangeSession.session_date
            bars_delta = $barCount.delta
            session_status_available = $true
            session_quality_ready = ($qualityEvidence.Count -gt 0)
            session_quality_status = $qualityStatus
            session_quality_diagnostics = $qualityDiagnostics
            session_quality_diagnostics_recorded_at_utc = $sessionQualityDiagnosticsRecordedAtUtc
            feeds = $status.stream_connections
            feed_diagnostics = Get-MonitorFeedDiagnostics $status $Feed
            feed_diagnostics_delta = Get-MonitorFeedDiagnosticsDelta $initial $status $Feed
            orders_enabled = [bool]$status.orders_enabled
            automation_state = $status.automation_state
            result = if ($qualityEvidence.Count -gt 0) { 'post_close_ready' } else { 'finalizing' }
        }) $status
        return [pscustomobject]@{
            ready = ($qualityEvidence.Count -gt 0)
            status = $status
            status_available = $true
            quality = $qualityEvidence
            quality_diagnostics = $qualityDiagnostics
            quality_diagnostics_recorded_at_utc = $sessionQualityDiagnosticsRecordedAtUtc
            quality_status = $qualityStatus
        }
    }.GetNewClosure()
    $finalization = Wait-ForExchangeSessionFinalization `
        $PostCloseGraceSeconds $PollIntervalSeconds $sessionQualityProbe
    $postCloseWaitSeconds = $finalization.elapsed_seconds
    $latest = $finalization.result.status
    $sessionStatusAvailable = [bool]$finalization.result.status_available
    $sessionQualityEvidence = @($finalization.result.quality)
    $sessionQualityDiagnostics = @($finalization.result.quality_diagnostics)
    $sessionQualityDiagnosticsRecordedAtUtc = $finalization.result.quality_diagnostics_recorded_at_utc
    $sessionQualityStatus = [string]$finalization.result.quality_status
    if (-not $finalization.ready) {
        if (-not $sessionStatusAvailable) {
            throw 'Could not read the local paper-session status endpoint.'
        }
        throw 'The final stored session did not reach complete bars, spread, and source verification within the post-close grace period.'
    }
}

$monitorStage = 'completion_validation'
$stopwatch.Stop()
$latestTicks = if ($Feed) { [int]$latest.ticks_received_by_feed.$Feed } else { [int]$latest.ticks_received }
$tickDelta = $latestTicks - $initialTicks
if ($tickDelta -le 0) {
    throw 'No increase in WebSocket ticks was observed during the selected sample window.'
}
if (-not $hasReceiveTimestamp) {
    throw 'The running market-data service does not expose a tick receive timestamp; restart it with the current code before monitoring.'
}
$latestBars = if ($Feed) { [int]$latest.bars_closed_by_feed.$Feed } else { [int]$latest.bars_closed }
$sessionBarCount = Get-ExchangeSessionBarsDelta $latestBars $initialBars $sessionOpenBarsBaseline
$barDelta = $sessionBarCount.delta
$verifiedSessionBars = 0
if ($sessionQualityEvidence.Count -gt 0) {
    $verifiedSessionBars = [int](($sessionQualityEvidence | Measure-Object -Property observed_bars -Sum).Sum)
}
if ($RequireSessionClose -and $maxObservedNoTickSeconds -gt $MaxNoTickSeconds) {
    throw "No WebSocket ticks arrived for more than $MaxNoTickSeconds seconds during full-session validation."
}
if (-not (Test-ExchangeSessionCompletion $barDelta $MinimumBarsDelta ([bool]$RequireSessionClose) `
        ($verifiedSessionBars -gt 0) $maxObservedNoTickSeconds $MaxNoTickSeconds)) {
    if ($RequireSessionClose) {
        throw 'Stored-session quality did not verify a complete session with full spread and source coverage.'
    }
    throw "Closed bar count increased by $barDelta, below the required minimum $MinimumBarsDelta."
}
$evidence = [ordered]@{
    validation_scope = $validationScope
    started_at_utc = $startedAtUtc.ToString('o')
    completed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    requested_seconds = $DurationSeconds
    observed_seconds = [int]$stopwatch.Elapsed.TotalSeconds
    post_close_wait_seconds = $postCloseWaitSeconds
    poll_interval_seconds = $PollIntervalSeconds
    max_no_tick_seconds = $MaxNoTickSeconds
    max_observed_no_tick_seconds = [Math]::Round($maxObservedNoTickSeconds, 1)
    preopen_silence_seconds = [Math]::Round($preOpenSilenceSeconds, 1)
    closing_auction_silence_seconds = [Math]::Round($closingAuctionSilenceSeconds, 1)
    market_session = if ($exchangeSession) { $exchangeSession.market } else { $null }
    market_session_date = if ($exchangeSession) { $exchangeSession.session_date } else { $null }
    market_session_open_utc = if ($exchangeSession) { $exchangeSession.open_utc.ToString('o') } else { $null }
    market_session_close_utc = if ($exchangeSession) { $exchangeSession.close_utc.ToString('o') } else { $null }
    session_close_reached = $sessionCloseReached
    session_status_available = $sessionStatusAvailable
    session_bars_baseline = $sessionBarCount.baseline
    selected_feed = if ($Feed) { $Feed } else { 'any' }
    ticks_delta = $tickDelta
    bars_delta = $barDelta
    minimum_bars_delta = $MinimumBarsDelta
    counter_bars_delta_matches_requirement = ($barDelta -ge $MinimumBarsDelta)
    stored_session_bars_verified = $verifiedSessionBars
    session_quality_status = $sessionQualityStatus
    session_quality_diagnostics = $sessionQualityDiagnostics
    session_quality_diagnostics_recorded_at_utc = $sessionQualityDiagnosticsRecordedAtUtc
    session_quality = $sessionQualityEvidence
    feeds = $latest.stream_connections
    feed_diagnostics = Get-MonitorFeedDiagnostics $latest $Feed
    feed_diagnostics_delta = Get-MonitorFeedDiagnosticsDelta $initial $latest $Feed
    symbol_market_data = Get-MonitorSymbolMarketData $latest $Feed
    symbol_market_data_delta = Get-MonitorSymbolMarketDataDelta $initial $latest $Feed
    minute_quality_diagnostics = Get-MonitorMinuteQualityDiagnostics $latest $Feed
    stream_failure_diagnostic_available = (Get-MonitorStreamFailureDiagnostic $latest).stream_failure_diagnostic_available
    stream_failure_stage = (Get-MonitorStreamFailureDiagnostic $latest).stream_failure_stage
    stream_error_category = (Get-MonitorStreamFailureDiagnostic $latest).stream_error_category
    stream_failure_at = (Get-MonitorStreamFailureDiagnostic $latest).stream_failure_at
    orders_enabled = $false
    automation_state = $latest.automation_state
    result = 'passed'
}
Write-ProgressRecord ([ordered]@{
    validation_scope = $evidence.validation_scope
    recorded_at_utc = $evidence.completed_at_utc
    requested_seconds = $DurationSeconds
    elapsed_seconds = $evidence.observed_seconds
    selected_feed = $evidence.selected_feed
    ticks_delta = $tickDelta
    bars_delta = $barDelta
    minimum_bars_delta = $MinimumBarsDelta
    session_quality_status = $sessionQualityStatus
    session_quality_diagnostics = $sessionQualityDiagnostics
    session_quality_diagnostics_recorded_at_utc = $sessionQualityDiagnosticsRecordedAtUtc
    session_quality = $sessionQualityEvidence
    max_observed_no_tick_seconds = $evidence.max_observed_no_tick_seconds
    preopen_silence_seconds = $evidence.preopen_silence_seconds
    closing_auction_silence_seconds = $evidence.closing_auction_silence_seconds
    market_session = $evidence.market_session
    market_session_date = $evidence.market_session_date
    market_session_open_utc = $evidence.market_session_open_utc
    market_session_close_utc = $evidence.market_session_close_utc
    session_close_reached = $evidence.session_close_reached
    session_status_available = $sessionStatusAvailable
    session_bars_baseline = $evidence.session_bars_baseline
    feeds = $latest.stream_connections
    feed_diagnostics = Get-MonitorFeedDiagnostics $latest $Feed
    feed_diagnostics_delta = Get-MonitorFeedDiagnosticsDelta $initial $latest $Feed
    symbol_market_data = Get-MonitorSymbolMarketData $latest $Feed
    symbol_market_data_delta = Get-MonitorSymbolMarketDataDelta $initial $latest $Feed
    minute_quality_diagnostics = Get-MonitorMinuteQualityDiagnostics $latest $Feed
    orders_enabled = $false
    automation_state = $latest.automation_state
    result = 'passed'
}) $latest
[System.IO.File]::AppendAllText($EvidencePath,
    ($evidence | ConvertTo-Json -Compress -Depth 5) + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false))
$validationLabel = if ($RequireSessionClose) { 'full-session validation' } else { 'bounded sample' }
Write-Output "Read-only KIS $validationLabel passed: feed=$(if ($Feed) { $Feed } else { 'any' }) seconds=$DurationSeconds ticks_delta=$tickDelta bars_delta=$barDelta max_no_tick_seconds=$([Math]::Round($maxObservedNoTickSeconds, 1)) orders=disabled automation=disabled evidence=$EvidencePath."
} catch {
    $elapsedSeconds = if ($stopwatch) {
        [int]$stopwatch.Elapsed.TotalSeconds
    } else {
        [int]([DateTimeOffset]::UtcNow - $monitorRunStartedAtUtc).TotalSeconds
    }
    $failureProgress = [ordered]@{
        validation_scope = $validationScope
        recorded_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        started_at_utc = if ($startedAtUtc) { $startedAtUtc.ToString('o') } else { $monitorRunStartedAtUtc.ToString('o') }
        requested_seconds = $DurationSeconds
        elapsed_seconds = $elapsedSeconds
        selected_feed = if ($Feed) { $Feed } else { 'any' }
        failure_stage = $monitorStage
        max_observed_no_tick_seconds = if ($maxObservedNoTickSeconds) { [Math]::Round($maxObservedNoTickSeconds, 1) } else { 0.0 }
        preopen_silence_seconds = if ($preOpenSilenceSeconds) { [Math]::Round($preOpenSilenceSeconds, 1) } else { 0.0 }
        session_phase = if ($exchangeSession) { Get-ExchangeSessionPhase ([DateTimeOffset]::UtcNow) $exchangeSession } else { 'NOT_APPLICABLE' }
        market_session = if ($exchangeSession) { $exchangeSession.market } else { $null }
        market_session_date = if ($exchangeSession) { $exchangeSession.session_date } else { $null }
        market_session_open_utc = if ($exchangeSession) { $exchangeSession.open_utc.ToString('o') } else { $null }
        market_session_close_utc = if ($exchangeSession) { $exchangeSession.close_utc.ToString('o') } else { $null }
        session_status_available = $sessionStatusAvailable
        session_quality_status = $sessionQualityStatus
        session_quality_diagnostics = $sessionQualityDiagnostics
        session_quality_diagnostics_recorded_at_utc = $sessionQualityDiagnosticsRecordedAtUtc
        closing_auction_silence_seconds = if ($closingAuctionSilenceSeconds) { [Math]::Round($closingAuctionSilenceSeconds, 1) } else { 0.0 }
        feeds = if ($latest) { $latest.stream_connections } else { $null }
        feed_diagnostics = if ($latest) { Get-MonitorFeedDiagnostics $latest $Feed } else { $null }
        feed_diagnostics_delta = if ($latest) { Get-MonitorFeedDiagnosticsDelta $initial $latest $Feed } else { $null }
        minute_quality_diagnostics = if ($latest) {
            Get-MonitorMinuteQualityDiagnostics $latest $Feed
        } else { [pscustomobject]@{ available = $false; reason = 'STATUS_UNAVAILABLE'; symbols = $null } }
        orders_enabled = if ($latest) { [bool]$latest.orders_enabled } else { $false }
        automation_state = if ($latest) { $latest.automation_state } else { $null }
        failure_type = $_.Exception.GetType().Name
        failure_code = Get-MonitorFailureCode ([string]$_.Exception.Message)
        result = 'failed'
    }
    try { Write-ProgressRecord $failureProgress $latest } catch { }
    throw
}
