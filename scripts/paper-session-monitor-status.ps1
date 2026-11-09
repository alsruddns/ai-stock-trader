function Assert-MonitorSafetyLocks($Status) {
    if ($Status.orders_enabled -or $Status.automation_state -ne 'DISABLED') {
        throw 'Paper-session monitor is not running in read-only, automation-disabled mode.'
    }
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
