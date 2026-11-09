function Assert-MonitorSafetyLocks($Status) {
    if ($Status.orders_enabled -or $Status.automation_state -ne 'DISABLED') {
        throw 'Paper-session monitor is not running in read-only, automation-disabled mode.'
    }
}

function Get-MonitorFailureCode([string]$Message) {
    if ([string]::IsNullOrWhiteSpace($Message)) { return 'MONITOR_FAILED' }
    switch -Regex ($Message) {
        'No WebSocket ticks arrived for more than \d+ seconds' { return 'WEBSOCKET_NO_TICKS' }
        'No increase in WebSocket ticks was observed during the selected sample window' { return 'NO_TICKS_IN_SAMPLE_WINDOW' }
        'Local market-data feed did not become ready' { return 'FEED_NOT_READY' }
        'Could not read the local paper-session status endpoint' { return 'SESSION_STATUS_UNAVAILABLE' }
        'Could not verify today.*exchange session' { return 'EXCHANGE_CALENDAR_UNAVAILABLE' }
        'Stored-session quality did not verify' { return 'STORED_SESSION_QUALITY_INCOMPLETE' }
        'below the required minimum' { return 'MINIMUM_BAR_COUNT_NOT_MET' }
        default { return 'MONITOR_FAILED' }
    }
}

function Get-MonitorFeedDiagnostics($Status, [string]$SelectedFeed) {
    $feedNames = if ($SelectedFeed) { @($SelectedFeed.ToLowerInvariant()) } else { @('paper', 'real') }
    $result = [ordered]@{}
    $framesProperty = if ($Status) { $Status.PSObject.Properties['frames_received_by_feed'] } else { $null }
    $transactionsProperty = if ($Status) { $Status.PSObject.Properties['frame_transaction_counts_by_feed'] } else { $null }
    $quoteQualityProperty = if ($Status) { $Status.PSObject.Properties['orderbook_quote_quality_by_feed'] } else { $null }
    $processingProperty = if ($Status) { $Status.PSObject.Properties['market_data_diagnostics_by_feed'] } else { $null }
    foreach ($feed in $feedNames) {
        $frameValue = if ($framesProperty) { $framesProperty.Value.PSObject.Properties[$feed] } else { $null }
        $transactionValue = if ($transactionsProperty) { $transactionsProperty.Value.PSObject.Properties[$feed] } else { $null }
        $quoteQualityValue = if ($quoteQualityProperty) { $quoteQualityProperty.Value.PSObject.Properties[$feed] } else { $null }
        $processingValue = if ($processingProperty) { $processingProperty.Value.PSObject.Properties[$feed] } else { $null }
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
        $quoteQualityCounts = [ordered]@{}
        $quoteQualityValid = $null -ne $quoteQualityValue
        foreach ($counterName in @('usable', 'empty_or_nonpositive', 'crossed', 'stale_rejected')) {
            $counterProperty = if ($quoteQualityValue) { $quoteQualityValue.Value.PSObject.Properties[$counterName] } else { $null }
            $count = 0L
            if ($null -eq $counterProperty -or
                -not [long]::TryParse([string]$counterProperty.Value, [ref]$count) -or $count -lt 0) {
                $quoteQualityValid = $false
            } else {
                $quoteQualityCounts[$counterName] = $count
            }
        }
        $processingCounts = [ordered]@{}
        $processingValid = $null -ne $processingValue
        foreach ($counterName in @(
            'trade_market_class_filtered', 'orderbook_market_class_filtered',
            'trade_parse_errors', 'orderbook_parse_errors',
            'trade_symbol_mismatches', 'orderbook_symbol_mismatches',
            'encrypted_market_frames_rejected'
        )) {
            $counterProperty = if ($processingValue) { $processingValue.Value.PSObject.Properties[$counterName] } else { $null }
            $count = 0L
            if ($null -eq $counterProperty -or
                -not [long]::TryParse([string]$counterProperty.Value, [ref]$count) -or $count -lt 0) {
                $processingValid = $false
            } else {
                $processingCounts[$counterName] = $count
            }
        }
        $result[$feed] = [ordered]@{
            available = [bool]$valid
            frames_received = if ($valid) { $frameCount } else { $null }
            transaction_counts = if ($valid) { $transactionCounts } else { $null }
            quote_quality_available = [bool]$quoteQualityValid
            quote_quality = if ($quoteQualityValid) { $quoteQualityCounts } else { $null }
            processing_diagnostics_available = [bool]$processingValid
            processing_diagnostics = if ($processingValid) { $processingCounts } else { $null }
        }
    }
    return $result
}

function Get-MonitorCounterDeltas(
    [bool]$InitialAvailable, $InitialCounts,
    [bool]$CurrentAvailable, $CurrentCounts,
    [string[]]$CounterNames
) {
    $values = [ordered]@{}
    $available = $InitialAvailable -and $CurrentAvailable -and $null -ne $InitialCounts -and $null -ne $CurrentCounts
    foreach ($name in $CounterNames) {
        $initialProperty = if ($InitialCounts) { $InitialCounts.PSObject.Properties[$name] } else { $null }
        $currentProperty = if ($CurrentCounts) { $CurrentCounts.PSObject.Properties[$name] } else { $null }
        if ($null -eq $initialProperty -and $InitialCounts -is [System.Collections.IDictionary] -and $InitialCounts.Contains($name)) {
            $initialValue = $InitialCounts[$name]
            $hasInitial = $true
        } else {
            $initialValue = if ($initialProperty) { $initialProperty.Value } else { $null }
            $hasInitial = $null -ne $initialProperty
        }
        if ($null -eq $currentProperty -and $CurrentCounts -is [System.Collections.IDictionary] -and $CurrentCounts.Contains($name)) {
            $currentValue = $CurrentCounts[$name]
            $hasCurrent = $true
        } else {
            $currentValue = if ($currentProperty) { $currentProperty.Value } else { $null }
            $hasCurrent = $null -ne $currentProperty
        }
        $initialCount = 0L
        $currentCount = 0L
        if (-not $hasInitial -or -not $hasCurrent -or
            -not [long]::TryParse([string]$initialValue, [ref]$initialCount) -or
            -not [long]::TryParse([string]$currentValue, [ref]$currentCount) -or
            $initialCount -lt 0 -or $currentCount -lt $initialCount) {
            $available = $false
            break
        }
        $values[$name] = $currentCount - $initialCount
    }
    return [ordered]@{
        available = [bool]$available
        counts = if ($available) { $values } else { $null }
    }
}

function Get-MonitorFeedDiagnosticsDelta($InitialStatus, $CurrentStatus, [string]$SelectedFeed) {
    $initial = Get-MonitorFeedDiagnostics $InitialStatus $SelectedFeed
    $current = Get-MonitorFeedDiagnostics $CurrentStatus $SelectedFeed
    $result = [ordered]@{}
    foreach ($feed in $initial.Keys) {
        $start = $initial[$feed]
        $end = $current[$feed]
        $frameDelta = Get-MonitorCounterDeltas $start.available `
            ([ordered]@{ frames_received = $start.frames_received }) `
            $end.available ([ordered]@{ frames_received = $end.frames_received }) @('frames_received')
        $transactionDelta = Get-MonitorCounterDeltas $start.available $start.transaction_counts `
            $end.available $end.transaction_counts @('H0STCNT0', 'H0STASP0', 'HDFSCNT0', 'OTHER')
        $quoteDelta = Get-MonitorCounterDeltas $start.quote_quality_available $start.quote_quality `
            $end.quote_quality_available $end.quote_quality @('usable', 'empty_or_nonpositive', 'crossed', 'stale_rejected')
        $processingDelta = Get-MonitorCounterDeltas $start.processing_diagnostics_available $start.processing_diagnostics `
            $end.processing_diagnostics_available $end.processing_diagnostics @(
                'trade_market_class_filtered', 'orderbook_market_class_filtered',
                'trade_parse_errors', 'orderbook_parse_errors',
                'trade_symbol_mismatches', 'orderbook_symbol_mismatches',
                'encrypted_market_frames_rejected'
            )
        $result[$feed] = [ordered]@{
            frames_available = $frameDelta.available
            frames_received = if ($frameDelta.available) { $frameDelta.counts.frames_received } else { $null }
            transaction_counts_available = $transactionDelta.available
            transaction_counts = $transactionDelta.counts
            quote_quality_available = $quoteDelta.available
            quote_quality = $quoteDelta.counts
            processing_diagnostics_available = $processingDelta.available
            processing_diagnostics = $processingDelta.counts
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
