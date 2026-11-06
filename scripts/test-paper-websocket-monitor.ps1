param(
    [string]$ApiBaseUrl = 'http://127.0.0.1:8000',
    [ValidateRange(10, 36000)] [int]$DurationSeconds = 60,
    [ValidateRange(1, 60)] [int]$PollIntervalSeconds = 15,
    [ValidateRange(30, 3600)] [int]$MaxNoTickSeconds = 300,
    [ValidateRange(0, 36000)] [int]$MinimumBarsDelta = 0,
    [ValidateSet('paper', 'real')] [string]$Feed,
    [string]$EvidencePath,
    [switch]$RequireBothFeeds
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'krx-market-session.ps1')
. (Join-Path $PSScriptRoot 'paper-session-monitor-status.ps1')
if ([string]::IsNullOrWhiteSpace($EvidencePath)) {
    $EvidencePath = Join-Path $repoRoot '.local\validation\paper-websocket-samples.jsonl'
}
$evidenceDirectory = Split-Path -Parent $EvidencePath
$progressName = '{0}.progress.jsonl' -f [IO.Path]::GetFileNameWithoutExtension($EvidencePath)
$ProgressPath = Join-Path $evidenceDirectory $progressName

function Write-ProgressRecord($Record) {
    if ($evidenceDirectory) { New-Item -ItemType Directory -Force -Path $evidenceDirectory | Out-Null }
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

function Wait-ForSafeRunningStatus {
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(120)
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
    throw 'Local market-data feed did not become ready within 120 seconds.'
}

try {
$initial = Wait-ForSafeRunningStatus
$initialTicks = if ($Feed) { [int]$initial.ticks_received_by_feed.$Feed } else { [int]$initial.ticks_received }
$initialBars = if ($Feed) { [int]$initial.bars_closed_by_feed.$Feed } else { [int]$initial.bars_closed }
$previousTicks = $initialTicks
$startedAtUtc = [DateTimeOffset]::UtcNow
$krxSessionOpenUtc = if ($Feed -eq 'paper') { Get-KrxSessionOpenUtc $startedAtUtc } else { $null }
$initialReceiveAt = if ($Feed) { $initial.last_tick_received_at_by_feed.$Feed } else { $initial.last_tick_received_at }
if ($Feed -eq 'paper' -and $initialReceiveAt) {
    $initialReceivedAtUtc = [DateTimeOffset]::Parse([string]$initialReceiveAt).ToUniversalTime()
    if (-not (Test-KrxSessionTickTimestamp $initialReceivedAtUtc $krxSessionOpenUtc)) { $initialReceiveAt = $null }
}
$hasReceiveTimestamp = [bool]$initialReceiveAt
$lastTickReceivedAt = if ($hasReceiveTimestamp) {
    [DateTimeOffset]::Parse([string]$initialReceiveAt).ToUniversalTime()
} else { $startedAtUtc }
$maxObservedNoTickSeconds = 0.0
$krxPreOpenSilenceSeconds = 0.0
$krxClosingAuctionSilenceSeconds = 0.0
$krxRegularSessionCloseReached = $false
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($DurationSeconds)
$latest = $initial
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$nextProgressSeconds = 60
$initialProgress = [ordered]@{
    recorded_at_utc = $startedAtUtc.ToString('o')
    requested_seconds = $DurationSeconds
    selected_feed = if ($Feed) { $Feed } else { 'any' }
    krx_session_phase = if ($Feed -eq 'paper') { Get-KrxSessionPhase $startedAtUtc } else { 'NOT_APPLICABLE' }
    elapsed_seconds = 0
    ticks_delta = 0
    bars_delta = 0
    max_observed_no_tick_seconds = 0.0
    feeds = $initial.stream_connections
    orders_enabled = $false
    automation_state = $initial.automation_state
    result = 'running'
}
Write-ProgressRecord $initialProgress
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $remaining = [int][Math]::Ceiling(($deadline - [DateTimeOffset]::UtcNow).TotalSeconds)
    Start-Sleep -Seconds ([Math]::Min($PollIntervalSeconds, [Math]::Max(1, $remaining)))
    $sampledAtUtc = [DateTimeOffset]::UtcNow
    $krxSessionPhase = if ($Feed -eq 'paper') { Get-KrxSessionPhase $sampledAtUtc } else { 'NOT_APPLICABLE' }
    if ($krxSessionPhase -eq 'CLOSED') {
        $krxRegularSessionCloseReached = $true
        break
    }
    $latest = Get-SessionStatus
    Assert-SafeRunningStatus $latest $Feed ([bool]$RequireBothFeeds)

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
        $isPreOpenOrPreviousSessionTick = $Feed -eq 'paper' -and -not (Test-KrxSessionTickTimestamp $observedTickReceivedAt $krxSessionOpenUtc)
        if (-not $isPreOpenOrPreviousSessionTick -and $observedTickReceivedAt -lt $lastTickReceivedAt) {
            throw 'The last tick receive timestamp moved backwards during the sample.'
        }
        if (-not $isPreOpenOrPreviousSessionTick) {
            $lastTickReceivedAt = $observedTickReceivedAt
            $hasReceiveTimestamp = $true
        }
    }
    if ($Feed -eq 'paper' -and $sampledAtUtc -ge $krxSessionOpenUtc -and $lastTickReceivedAt -lt $krxSessionOpenUtc) {
        # A previous day's tick must not make the opening feed look stale immediately.
        $lastTickReceivedAt = $krxSessionOpenUtc
    }
    $noTickSeconds = ([DateTimeOffset]::UtcNow - $lastTickReceivedAt).TotalSeconds
    if ($krxSessionPhase -eq 'PRE_OPEN') {
        $krxPreOpenSilenceSeconds = [Math]::Max($krxPreOpenSilenceSeconds, ([DateTimeOffset]::UtcNow - $startedAtUtc).TotalSeconds)
    } elseif ($krxSessionPhase -eq 'CLOSING_AUCTION') {
        # KRX does not run continuous matching during the 15:20–15:30 closing call auction.
        # The last continuous-trade tick can therefore be older than the feed-stall threshold.
        $krxClosingAuctionSilenceSeconds = [Math]::Max($krxClosingAuctionSilenceSeconds, $noTickSeconds)
    } else {
        $maxObservedNoTickSeconds = [Math]::Max($maxObservedNoTickSeconds, $noTickSeconds)
    }
    if ((Test-KrxNoTickThresholdApplies $krxSessionPhase) -and $noTickSeconds -gt $MaxNoTickSeconds) {
        throw "No WebSocket ticks arrived for more than $MaxNoTickSeconds seconds."
    }

    $currentBars = if ($Feed) { [int]$latest.bars_closed_by_feed.$Feed } else { [int]$latest.bars_closed }
    $progress = [ordered]@{
        recorded_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        requested_seconds = $DurationSeconds
        elapsed_seconds = [int]$stopwatch.Elapsed.TotalSeconds
        selected_feed = if ($Feed) { $Feed } else { 'any' }
        ticks_delta = $currentTicks - $initialTicks
        bars_delta = $currentBars - $initialBars
        minimum_bars_delta = $MinimumBarsDelta
        max_observed_no_tick_seconds = [Math]::Round($maxObservedNoTickSeconds, 1)
        krx_preopen_silence_seconds = [Math]::Round($krxPreOpenSilenceSeconds, 1)
        krx_session_phase = $krxSessionPhase
        krx_closing_auction_silence_seconds = [Math]::Round($krxClosingAuctionSilenceSeconds, 1)
        feeds = $latest.stream_connections
        orders_enabled = [bool]$latest.orders_enabled
        automation_state = $latest.automation_state
        result = 'running'
    }
    Write-ProgressRecord $progress

    if ($stopwatch.Elapsed.TotalSeconds -ge $nextProgressSeconds) {
        $tickDelta = $currentTicks - $initialTicks
        Write-Output "PAPER feed monitor running: feed=$(if ($Feed) { $Feed } else { 'any' }) elapsed_seconds=$([int]$stopwatch.Elapsed.TotalSeconds) ticks_delta=$tickDelta bars_delta=$($currentBars - $initialBars)."
        $nextProgressSeconds += 60
    }
}

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
$barDelta = $latestBars - $initialBars
if ($barDelta -lt $MinimumBarsDelta) {
    throw "Closed bar count increased by $barDelta, below the required minimum $MinimumBarsDelta."
}
$evidence = [ordered]@{
    started_at_utc = $startedAtUtc.ToString('o')
    completed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    requested_seconds = $DurationSeconds
    observed_seconds = [int]$stopwatch.Elapsed.TotalSeconds
    poll_interval_seconds = $PollIntervalSeconds
    max_no_tick_seconds = $MaxNoTickSeconds
    max_observed_no_tick_seconds = [Math]::Round($maxObservedNoTickSeconds, 1)
    krx_preopen_silence_seconds = [Math]::Round($krxPreOpenSilenceSeconds, 1)
    krx_closing_auction_silence_seconds = [Math]::Round($krxClosingAuctionSilenceSeconds, 1)
    krx_regular_session_close_reached = $krxRegularSessionCloseReached
    selected_feed = if ($Feed) { $Feed } else { 'any' }
    ticks_delta = $tickDelta
    bars_delta = $barDelta
    minimum_bars_delta = $MinimumBarsDelta
    feeds = $latest.stream_connections
    orders_enabled = $false
    automation_state = $latest.automation_state
    result = 'passed'
}
Write-ProgressRecord ([ordered]@{
    recorded_at_utc = $evidence.completed_at_utc
    requested_seconds = $DurationSeconds
    elapsed_seconds = $evidence.observed_seconds
    selected_feed = $evidence.selected_feed
    ticks_delta = $tickDelta
    bars_delta = $barDelta
    minimum_bars_delta = $MinimumBarsDelta
    max_observed_no_tick_seconds = $evidence.max_observed_no_tick_seconds
    krx_preopen_silence_seconds = $evidence.krx_preopen_silence_seconds
    krx_closing_auction_silence_seconds = $evidence.krx_closing_auction_silence_seconds
    krx_regular_session_close_reached = $evidence.krx_regular_session_close_reached
    feeds = $latest.stream_connections
    orders_enabled = $false
    automation_state = $latest.automation_state
    result = 'passed'
})
[System.IO.File]::AppendAllText($EvidencePath,
    ($evidence | ConvertTo-Json -Compress -Depth 5) + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false))
Write-Output "Read-only PAPER monitor sample passed: feed=$(if ($Feed) { $Feed } else { 'any' }) seconds=$DurationSeconds ticks_delta=$tickDelta bars_delta=$barDelta max_no_tick_seconds=$([Math]::Round($maxObservedNoTickSeconds, 1)) orders=disabled automation=disabled evidence=$EvidencePath."
} catch {
    if ($startedAtUtc) {
        $elapsedSeconds = if ($stopwatch) { [int]$stopwatch.Elapsed.TotalSeconds } else { 0 }
        $failureProgress = [ordered]@{
            recorded_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
            requested_seconds = $DurationSeconds
            elapsed_seconds = $elapsedSeconds
            selected_feed = if ($Feed) { $Feed } else { 'any' }
            max_observed_no_tick_seconds = if ($maxObservedNoTickSeconds) { [Math]::Round($maxObservedNoTickSeconds, 1) } else { 0.0 }
            krx_preopen_silence_seconds = if ($krxPreOpenSilenceSeconds) { [Math]::Round($krxPreOpenSilenceSeconds, 1) } else { 0.0 }
            krx_session_phase = if ($Feed -eq 'paper') { Get-KrxSessionPhase ([DateTimeOffset]::UtcNow) } else { 'NOT_APPLICABLE' }
            krx_closing_auction_silence_seconds = if ($krxClosingAuctionSilenceSeconds) { [Math]::Round($krxClosingAuctionSilenceSeconds, 1) } else { 0.0 }
            feeds = if ($latest) { $latest.stream_connections } else { $null }
            orders_enabled = if ($latest) { [bool]$latest.orders_enabled } else { $false }
            automation_state = if ($latest) { $latest.automation_state } else { $null }
            failure_type = $_.Exception.GetType().Name
            result = 'failed'
        }
        try { Write-ProgressRecord $failureProgress } catch { }
    }
    throw
}
