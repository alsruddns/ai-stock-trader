param(
    [string]$ApiBaseUrl = 'http://127.0.0.1:8000',
    [ValidateRange(10, 300)] [int]$DurationSeconds = 60,
    [switch]$RequireBothFeeds
)

$ErrorActionPreference = 'Stop'

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
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($DurationSeconds)
$latest = $initial
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $remaining = [int][Math]::Ceiling(($deadline - [DateTimeOffset]::UtcNow).TotalSeconds)
    Start-Sleep -Seconds ([Math]::Min(5, [Math]::Max(1, $remaining)))
    $latest = Get-SessionStatus
    Assert-SafeRunningStatus $latest
}

$tickDelta = [int]$latest.ticks_received - [int]$initial.ticks_received
if ($tickDelta -le 0) {
    throw 'No increase in WebSocket ticks was observed during the selected sample window.'
}
$barDelta = [int]$latest.bars_closed - [int]$initial.bars_closed
Write-Output "Read-only PAPER monitor sample passed: seconds=$DurationSeconds ticks_delta=$tickDelta bars_delta=$barDelta orders=disabled automation=disabled."
