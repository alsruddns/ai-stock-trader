param(
    [string]$ApiBaseUrl = 'http://127.0.0.1:8000',
    [ValidateRange(10, 36000)] [int]$DurationSeconds = 60,
    [ValidateRange(1, 60)] [int]$PollIntervalSeconds = 15,
    [ValidateRange(30, 3600)] [int]$MaxNoTickSeconds = 300,
    [ValidateSet('paper', 'real')] [string]$Feed,
    [string]$EvidencePath,
    [switch]$RequireBothFeeds
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($EvidencePath)) {
    $EvidencePath = Join-Path $repoRoot '.local\validation\paper-websocket-samples.jsonl'
}

function Get-SessionStatus {
    try {
        return Invoke-RestMethod -Uri "$ApiBaseUrl/api/v1/paper/session" -TimeoutSec 5
    } catch {
        throw 'Could not read the local paper-session status endpoint.'
    }
}

function Assert-SafeRunningStatus($Status) {
    if ($Status.state -ne 'RUNNING' -or $Status.orders_enabled -or
        $Status.automation_state -ne 'DISABLED' -or $Status.last_error) {
        throw 'Paper-session monitor is not running in read-only, automation-disabled mode.'
    }
    $connections = $Status.stream_connections
    $running = @($connections.PSObject.Properties | Where-Object { $_.Value -eq 'RUNNING' })
    if ($RequireBothFeeds) {
        if ($connections.paper -ne 'RUNNING' -or $connections.real -ne 'RUNNING') {
            throw 'Both PAPER domestic and read-only REAL market-data feeds must be RUNNING.'
        }
    } elseif ($Feed) {
        if ($connections.$Feed -ne 'RUNNING') {
            throw "The selected $Feed market-data feed must be RUNNING."
        }
    } elseif ($running.Count -lt 1) {
        throw 'No KIS market-data WebSocket feed is RUNNING.'
    }
}

$health = Invoke-RestMethod -Uri "$ApiBaseUrl/api/v1/health" -TimeoutSec 5
if ($health.status -ne 'ok' -or $health.live_trading_enabled -or $health.paper_automation_enabled) {
    throw 'Local API must be healthy with live trading and paper automation disabled.'
}

$initial = Get-SessionStatus
Assert-SafeRunningStatus $initial
$initialTicks = if ($Feed) { [int]$initial.ticks_received_by_feed.$Feed } else { [int]$initial.ticks_received }
$initialBars = if ($Feed) { [int]$initial.bars_closed_by_feed.$Feed } else { [int]$initial.bars_closed }
$previousTicks = $initialTicks
$startedAtUtc = [DateTimeOffset]::UtcNow
$initialReceiveAt = if ($Feed) { $initial.last_tick_received_at_by_feed.$Feed } else { $initial.last_tick_received_at }
$hasReceiveTimestamp = [bool]$initialReceiveAt
$lastTickReceivedAt = if ($hasReceiveTimestamp) {
    [DateTimeOffset]::Parse([string]$initialReceiveAt).ToUniversalTime()
} else { $startedAtUtc }
$maxObservedNoTickSeconds = 0.0
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($DurationSeconds)
$latest = $initial
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$nextProgressSeconds = 60
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $remaining = [int][Math]::Ceiling(($deadline - [DateTimeOffset]::UtcNow).TotalSeconds)
    Start-Sleep -Seconds ([Math]::Min($PollIntervalSeconds, [Math]::Max(1, $remaining)))
    $latest = Get-SessionStatus
    Assert-SafeRunningStatus $latest

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
        if ($observedTickReceivedAt -lt $lastTickReceivedAt) {
            throw 'The last tick receive timestamp moved backwards during the sample.'
        }
        $lastTickReceivedAt = $observedTickReceivedAt
        $hasReceiveTimestamp = $true
    }
    $noTickSeconds = ([DateTimeOffset]::UtcNow - $lastTickReceivedAt).TotalSeconds
    $maxObservedNoTickSeconds = [Math]::Max($maxObservedNoTickSeconds, $noTickSeconds)
    if ($noTickSeconds -gt $MaxNoTickSeconds) {
        throw "No WebSocket ticks arrived for more than $MaxNoTickSeconds seconds."
    }

    if ($stopwatch.Elapsed.TotalSeconds -ge $nextProgressSeconds) {
        $tickDelta = $currentTicks - $initialTicks
        $currentBars = if ($Feed) { [int]$latest.bars_closed_by_feed.$Feed } else { [int]$latest.bars_closed }
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
$evidence = [ordered]@{
    started_at_utc = $startedAtUtc.ToString('o')
    completed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    requested_seconds = $DurationSeconds
    observed_seconds = [int]$stopwatch.Elapsed.TotalSeconds
    poll_interval_seconds = $PollIntervalSeconds
    max_no_tick_seconds = $MaxNoTickSeconds
    max_observed_no_tick_seconds = [Math]::Round($maxObservedNoTickSeconds, 1)
    selected_feed = if ($Feed) { $Feed } else { 'any' }
    ticks_delta = $tickDelta
    bars_delta = $barDelta
    feeds = $latest.stream_connections
    orders_enabled = $false
    automation_state = $latest.automation_state
    result = 'passed'
}
$evidenceDirectory = Split-Path -Parent $EvidencePath
if ($evidenceDirectory) { New-Item -ItemType Directory -Force -Path $evidenceDirectory | Out-Null }
[System.IO.File]::AppendAllText($EvidencePath,
    ($evidence | ConvertTo-Json -Compress -Depth 5) + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false))
Write-Output "Read-only PAPER monitor sample passed: feed=$(if ($Feed) { $Feed } else { 'any' }) seconds=$DurationSeconds ticks_delta=$tickDelta bars_delta=$barDelta max_no_tick_seconds=$([Math]::Round($maxObservedNoTickSeconds, 1)) orders=disabled automation=disabled evidence=$EvidencePath."
