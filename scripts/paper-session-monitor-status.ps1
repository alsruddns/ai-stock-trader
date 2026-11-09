function Assert-MonitorSafetyLocks($Status) {
    if ($Status.orders_enabled -or $Status.automation_state -ne 'DISABLED') {
        throw 'Paper-session monitor is not running in read-only, automation-disabled mode.'
    }
}

function Get-MonitorFeedDiagnostics($Status, [string]$SelectedFeed) {
    $feedNames = if ($SelectedFeed) { @($SelectedFeed.ToLowerInvariant()) } else { @('paper', 'real') }
    $result = [ordered]@{}
    $framesProperty = if ($Status) { $Status.PSObject.Properties['frames_received_by_feed'] } else { $null }
    $transactionsProperty = if ($Status) { $Status.PSObject.Properties['frame_transaction_counts_by_feed'] } else { $null }
    foreach ($feed in $feedNames) {
        $frameValue = if ($framesProperty) { $framesProperty.Value.PSObject.Properties[$feed] } else { $null }
        $transactionValue = if ($transactionsProperty) { $transactionsProperty.Value.PSObject.Properties[$feed] } else { $null }
        $frameCount = 0L
        $valid = $null -ne $frameValue -and $null -ne $transactionValue -and
            [long]::TryParse([string]$frameValue.Value, [ref]$frameCount) -and $frameCount -ge 0
        $transactionCounts = [ordered]@{}
        foreach ($transactionId in @('H0STCNT0', 'H0STASP0', 'HDFSCNT0', 'OTHER')) {
            $countProperty = if ($transactionValue) { $transactionValue.Value.PSObject.Properties[$transactionId] } else { $null }
            $count = 0L
            if ($null -eq $countProperty -or
                -not [long]::TryParse([string]$countProperty.Value, [ref]$count) -or $count -lt 0) {
                $valid = $false
            } else {
                $transactionCounts[$transactionId] = $count
            }
        }
        $result[$feed] = [ordered]@{
            available = [bool]$valid
            frames_received = if ($valid) { $frameCount } else { $null }
            transaction_counts = if ($valid) { $transactionCounts } else { $null }
        }
    }
    return $result
}

function Test-MonitorFeedReady($Status, [string]$SelectedFeed, [bool]$BothFeeds) {
    $connections = $Status.stream_connections
    if ($BothFeeds) {
        return $connections.paper -eq 'RUNNING' -and $connections.real -eq 'RUNNING'
    }
    if ($SelectedFeed) {
        return $connections.$SelectedFeed -eq 'RUNNING'
    }
    return @($connections.PSObject.Properties | Where-Object { $_.Value -eq 'RUNNING' }).Count -gt 0
}

function Assert-SafeRunningStatus($Status, [string]$SelectedFeed, [bool]$BothFeeds, [bool]$AllowTransientSelectedReconnect = $false) {
    Assert-MonitorSafetyLocks $Status

    $connections = $Status.stream_connections
    if ($BothFeeds) {
        if ($Status.state -ne 'RUNNING' -or $Status.last_error -or
            $connections.paper -ne 'RUNNING' -or $connections.real -ne 'RUNNING') {
            throw 'Both PAPER domestic and read-only REAL market-data feeds must be RUNNING.'
        }
        return
    }

    if ($SelectedFeed) {
        if ($connections.$SelectedFeed -ne 'RUNNING') {
            $transientStates = @('CONNECTING', 'SUBSCRIBING', 'RECONNECTING')
            if ($AllowTransientSelectedReconnect -and
                $connections.$SelectedFeed -in $transientStates -and
                $Status.enabled -ne $false -and
                $Status.state -notin @('BLOCKED', 'STOPPED') -and
                $Status.last_error -eq 'KIS_STREAM_UNAVAILABLE') {
                return
            }
            throw "The selected $SelectedFeed market-data feed must be RUNNING."
        }
        if ($Status.enabled -eq $false -or $Status.state -in @('BLOCKED', 'STOPPED')) {
            throw 'The selected feed is running while the session monitor reports a blocked or stopped state.'
        }
        # The status endpoint has one aggregate state/error for all configured feeds.
        # KIS_STREAM_UNAVAILABLE is emitted when any feed is reconnecting; it is safe
        # to ignore only when this sample explicitly selects a different RUNNING feed.
        if ($Status.last_error -and $Status.last_error -ne 'KIS_STREAM_UNAVAILABLE') {
            throw 'Paper-session monitor reported a non-feed runtime error.'
        }
        return
    }

    if ($Status.state -ne 'RUNNING' -or $Status.last_error -or
        -not (Test-MonitorFeedReady $Status '' $false)) {
        throw 'At least one KIS market-data feed must be RUNNING without a runtime error.'
    }
}
