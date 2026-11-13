function Assert-MonitorSafetyLocks($Status) {
    if ($Status.orders_enabled -or $Status.automation_state -ne 'DISABLED') {
        throw 'Paper-session monitor is not running in read-only, automation-disabled mode.'
    }
}

function Test-MonitorStatusUnavailableExpired(
    [DateTimeOffset]$UnavailableSince,
    [DateTimeOffset]$Now,
    [int]$GraceSeconds
) {
    if ($GraceSeconds -lt 1 -or $Now -lt $UnavailableSince) {
        throw 'Status endpoint recovery limits or timestamps are invalid.'
    }
    return ($Now - $UnavailableSince).TotalSeconds -gt $GraceSeconds
}

function Get-MonitorBarStorageDiagnostic($Status) {
    $allowedStates = @('RUNNING', 'DISABLED', 'ERROR', 'DRAIN_TIMEOUT', 'STARTING', 'RECOVERING')
    $stateProperty = if ($Status) { $Status.PSObject.Properties['bar_storage_state'] } else { $null }
    $pendingProperty = if ($Status) { $Status.PSObject.Properties['bar_storage_pending_count'] } else { $null }
    $retryProperty = if ($Status) { $Status.PSObject.Properties['bar_storage_retry_count'] } else { $null }
    $state = if ($stateProperty -and $stateProperty.Value -in $allowedStates) { [string]$stateProperty.Value } else { $null }
    $pendingCount = 0L
    $pendingAvailable = $null -ne $pendingProperty -and
        [long]::TryParse([string]$pendingProperty.Value, [ref]$pendingCount) -and
        $pendingCount -ge 0 -and $pendingCount -le 1000000
    $retryCount = 0L
    $retryAvailable = $null -ne $retryProperty -and
        [long]::TryParse([string]$retryProperty.Value, [ref]$retryCount) -and
        $retryCount -ge 0 -and $retryCount -le 1000000
    $available = $null -ne $state -and $pendingAvailable
    $ready = $available -and $state -eq 'RUNNING' -and $pendingCount -eq 0
    $reason = if (-not $available) { 'STATUS_UNAVAILABLE' }
        elseif ($state -ne 'RUNNING') { 'STORAGE_NOT_RUNNING' }
        elseif ($pendingCount -gt 0) { 'PENDING_BARS' }
        else { 'READY' }
    return [ordered]@{
        bar_storage_diagnostic_available = [bool]$available
        bar_storage_ready = [bool]$ready
        bar_storage_state = $state
        bar_storage_pending_count = if ($pendingAvailable) { $pendingCount } else { $null }
        bar_storage_retry_count = if ($retryAvailable) { $retryCount } else { $null }
        bar_storage_reason = $reason
    }
}

function Get-MonitorFailureCode([string]$Message) {
    if ([string]::IsNullOrWhiteSpace($Message)) { return 'MONITOR_FAILED' }
    switch -Regex ($Message) {
        'No WebSocket ticks arrived for more than \d+ seconds' { return 'WEBSOCKET_NO_TICKS' }
        'No increase in WebSocket ticks was observed during the selected sample window' { return 'NO_TICKS_IN_SAMPLE_WINDOW' }
        'The monitored exchange session did not reach its calendar-confirmed close' { return 'EXCHANGE_SESSION_CLOSE_NOT_REACHED' }
        'The final stored session did not reach complete bars, spread, and source verification within the post-close grace period' { return 'SESSION_FINALIZATION_TIMEOUT' }
        'The running market-data service does not expose a tick receive timestamp' { return 'FEED_TIMESTAMP_UNAVAILABLE' }
        'The closed bar counter moved backwards during the sample' { return 'BAR_COUNTER_RESET' }
        'Local market-data feed did not become ready' { return 'FEED_NOT_READY' }
        'Could not read the local paper-session status endpoint' { return 'SESSION_STATUS_UNAVAILABLE' }
        'durable bar storage queue did not drain' { return 'BAR_STORAGE_NOT_DRAINED' }
        'Could not verify today.*exchange session' { return 'EXCHANGE_CALENDAR_UNAVAILABLE' }
        'Paper-session monitor is not running in read-only, automation-disabled mode' { return 'SAFETY_LOCK_VIOLATION' }
        'Stored-session quality did not verify' { return 'STORED_SESSION_QUALITY_INCOMPLETE' }
        'below the required minimum' { return 'MINIMUM_BAR_COUNT_NOT_MET' }
        default { return 'MONITOR_FAILED' }
    }
}

function Get-MonitorStreamFailureDiagnostic($Status) {
    $allowedStages = @('approval_key', 'websocket_connect', 'subscription_send', 'subscription_acknowledgement', 'websocket_receive')
    $allowedCategories = @('TIMEOUT', 'NETWORK_DISCONNECT', 'WEBSOCKET_PROTOCOL', 'KIS_APPROVAL_OR_AUTH', 'SUBSCRIPTION_REJECTED', 'SUBSCRIPTION_TIMEOUT', 'UNCLASSIFIED')
    $stageProperty = if ($Status) { $Status.PSObject.Properties['last_stream_failure_stage'] } else { $null }
    $categoryProperty = if ($Status) { $Status.PSObject.Properties['last_stream_error_category'] } else { $null }
    $recordedAtProperty = if ($Status) { $Status.PSObject.Properties['last_stream_failure_at'] } else { $null }
    $environmentProperty = if ($Status) { $Status.PSObject.Properties['last_stream_failure_environment'] } else { $null }
    $failureCountsProperty = if ($Status) { $Status.PSObject.Properties['stream_failures_by_feed'] } else { $null }
    $failureEnvironment = if ($environmentProperty -and $environmentProperty.Value -in @('paper', 'real')) {
        [string]$environmentProperty.Value
    } else { $null }
    $failureCount = $null
    $failureCountAvailable = $false
    if ($failureEnvironment -and $failureCountsProperty -and $failureCountsProperty.Value) {
        $countProperty = $failureCountsProperty.Value.PSObject.Properties[$failureEnvironment]
        $parsedCount = 0L
        if ($countProperty -and [long]::TryParse([string]$countProperty.Value, [ref]$parsedCount) -and $parsedCount -ge 0) {
            $failureCount = $parsedCount
            $failureCountAvailable = $true
        }
    }
    $recordedAt = $null
    if ($recordedAtProperty -and $recordedAtProperty.Value) {
        $parsedAt = [DateTimeOffset]::MinValue
        if ([DateTimeOffset]::TryParse([string]$recordedAtProperty.Value, [ref]$parsedAt)) {
            $recordedAt = $parsedAt.ToUniversalTime().ToString('o')
        }
    }
    return [ordered]@{
        stream_failure_diagnostic_available = ($null -ne $stageProperty -and $null -ne $categoryProperty)
        stream_failure_stage = if ($stageProperty -and $stageProperty.Value -in $allowedStages) { [string]$stageProperty.Value } else { $null }
        stream_error_category = if ($categoryProperty -and $categoryProperty.Value -in $allowedCategories) { [string]$categoryProperty.Value } else { $null }
        stream_failure_at = $recordedAt
        stream_failure_environment = $failureEnvironment
        stream_failure_count_available = $failureCountAvailable
        stream_failure_count = $failureCount
    }
}

function Get-MonitorStreamRecoveryDiagnostic($Status, [string]$SelectedFeed) {
    $feed = if ($SelectedFeed) { $SelectedFeed.Trim().ToLowerInvariant() } else { 'paper' }
    if ($feed -notin @('paper', 'real')) {
        return [ordered]@{
            stream_recovery_diagnostic_available = $false
            stream_reconnecting_since = $null
            stream_last_recovered_at = $null
            stream_last_recovery_seconds = $null
        }
    }
    $sinceMap = if ($Status) { $Status.PSObject.Properties['stream_reconnecting_since_by_feed'] } else { $null }
    $recoveredMap = if ($Status) { $Status.PSObject.Properties['stream_last_recovered_at_by_feed'] } else { $null }
    $durationMap = if ($Status) { $Status.PSObject.Properties['stream_last_recovery_seconds_by_feed'] } else { $null }
    $since = if ($sinceMap -and $sinceMap.Value) { $sinceMap.Value.PSObject.Properties[$feed] } else { $null }
    $recovered = if ($recoveredMap -and $recoveredMap.Value) { $recoveredMap.Value.PSObject.Properties[$feed] } else { $null }
    $duration = if ($durationMap -and $durationMap.Value) { $durationMap.Value.PSObject.Properties[$feed] } else { $null }
    $available = $null -ne $since -and $null -ne $recovered -and $null -ne $duration
    $normalizedSince = $null
    $normalizedRecovered = $null
    $normalizedDuration = $null
    foreach ($item in @(@{ Property = $since; Target = 'since' }, @{ Property = $recovered; Target = 'recovered' })) {
        if ($item.Property -and -not [string]::IsNullOrWhiteSpace([string]$item.Property.Value)) {
            $parsedAt = [DateTimeOffset]::MinValue
            if ([DateTimeOffset]::TryParse([string]$item.Property.Value, [ref]$parsedAt)) {
                if ($item.Target -eq 'since') { $normalizedSince = $parsedAt.ToUniversalTime().ToString('o') }
                else { $normalizedRecovered = $parsedAt.ToUniversalTime().ToString('o') }
            } else { $available = $false }
        }
    }
    if ($duration -and $null -ne $duration.Value -and
        -not [string]::IsNullOrWhiteSpace([string]$duration.Value)) {
        $parsedDuration = 0.0
        $validDuration = [double]::TryParse(
            [string]$duration.Value,
            [System.Globalization.NumberStyles]::Float,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$parsedDuration
        ) -and -not [double]::IsNaN($parsedDuration) -and
            -not [double]::IsInfinity($parsedDuration) -and $parsedDuration -ge 0
        if ($validDuration) { $normalizedDuration = $parsedDuration }
        else { $available = $false }
    }
    return [ordered]@{
        stream_recovery_diagnostic_available = $available
        stream_reconnecting_since = $normalizedSince
        stream_last_recovered_at = $normalizedRecovered
        stream_last_recovery_seconds = $normalizedDuration
    }
}

function Get-MonitorFeedDiagnostics($Status, [string]$SelectedFeed) {
    $feedNames = if ($SelectedFeed) { @($SelectedFeed.ToLowerInvariant()) } else { @('paper', 'real') }
    $result = [ordered]@{}
    $framesProperty = if ($Status) { $Status.PSObject.Properties['frames_received_by_feed'] } else { $null }
    $transactionsProperty = if ($Status) { $Status.PSObject.Properties['frame_transaction_counts_by_feed'] } else { $null }
    $orderbookUpdatesProperty = if ($Status) { $Status.PSObject.Properties['orderbook_updates_by_feed'] } else { $null }
    $quoteQualityProperty = if ($Status) { $Status.PSObject.Properties['orderbook_quote_quality_by_feed'] } else { $null }
    $processingProperty = if ($Status) { $Status.PSObject.Properties['market_data_diagnostics_by_feed'] } else { $null }
    foreach ($feed in $feedNames) {
        $frameValue = if ($framesProperty) { $framesProperty.Value.PSObject.Properties[$feed] } else { $null }
        $transactionValue = if ($transactionsProperty) { $transactionsProperty.Value.PSObject.Properties[$feed] } else { $null }
        $orderbookUpdatesValue = if ($orderbookUpdatesProperty) { $orderbookUpdatesProperty.Value.PSObject.Properties[$feed] } else { $null }
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
        $orderbookUpdates = 0L
        $orderbookUpdatesValid = $null -ne $orderbookUpdatesValue -and
            [long]::TryParse([string]$orderbookUpdatesValue.Value, [ref]$orderbookUpdates) -and
            $orderbookUpdates -ge 0
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
            orderbook_updates_available = [bool]$orderbookUpdatesValid
            orderbook_updates = if ($orderbookUpdatesValid) { $orderbookUpdates } else { $null }
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
        $orderbookUpdatesDelta = Get-MonitorCounterDeltas $start.orderbook_updates_available `
            ([ordered]@{ orderbook_updates = $start.orderbook_updates }) `
            $end.orderbook_updates_available ([ordered]@{ orderbook_updates = $end.orderbook_updates }) @('orderbook_updates')
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
            orderbook_updates_available = $orderbookUpdatesDelta.available
            orderbook_updates = if ($orderbookUpdatesDelta.available) { $orderbookUpdatesDelta.counts.orderbook_updates } else { $null }
            quote_quality_available = $quoteDelta.available
            quote_quality = $quoteDelta.counts
            processing_diagnostics_available = $processingDelta.available
            processing_diagnostics = $processingDelta.counts
        }
    }
    return $result
}

function Get-MonitorSymbolMarketData($Status, [string]$SelectedFeed) {
    $feed = if ($SelectedFeed) { $SelectedFeed.Trim().ToLowerInvariant() } else { 'paper' }
    if ($feed -notin @('paper', 'real')) {
        return [pscustomobject]@{ available = $false; reason = 'INVALID_FEED'; symbols = $null }
    }
    $sourceProperty = if ($Status) { $Status.PSObject.Properties['market_data_by_watch_by_feed'] } else { $null }
    $feedProperty = if ($sourceProperty -and $sourceProperty.Value) {
        $sourceProperty.Value.PSObject.Properties[$feed]
    } else { $null }
    if (-not $feedProperty -or -not $feedProperty.Value) {
        return [pscustomobject]@{ available = $false; reason = 'FIELD_UNAVAILABLE'; symbols = $null }
    }

    $valid = $true
    $expectedMarket = if ($feed -eq 'paper') { 'KR' } else { 'US' }
    $symbols = [System.Collections.Generic.List[object]]::new()
    foreach ($property in $feedProperty.Value.PSObject.Properties) {
        $row = $property.Value
        $market = [string]$row.market
        $symbol = ([string]$row.symbol).Trim().ToUpperInvariant()
        $exchange = if ([string]::IsNullOrWhiteSpace([string]$row.exchange)) { $null } else { ([string]$row.exchange).Trim().ToUpperInvariant() }
        $keyMatches = if ($expectedMarket -eq 'KR') {
            $property.Name -match '^KR:(\d{6}):$' -and $Matches[1] -eq $symbol -and $null -eq $exchange
        } else {
            $property.Name -match '^US:([A-Z][A-Z0-9.\-]{0,14}):(NAS|NYS|AMS)$' -and
                $Matches[1] -eq $symbol -and $Matches[2] -eq $exchange
        }
        if ($market -ne $expectedMarket -or -not $keyMatches) {
            $valid = $false
            continue
        }

        $counts = [ordered]@{}
        foreach ($name in @('trade_ticks_received', 'bars_closed', 'orderbook_quotes_received', 'usable_orderbook_quotes')) {
            $count = 0L
            $countProperty = $row.PSObject.Properties[$name]
            if ($countProperty -and (-not [long]::TryParse([string]$countProperty.Value, [ref]$count) -or $count -lt 0)) {
                $valid = $false
            }
            $counts[$name] = $count
        }

        $times = [ordered]@{}
        foreach ($name in @('last_trade_at', 'last_trade_received_at', 'last_orderbook_at', 'last_orderbook_received_at')) {
            $value = $row.PSObject.Properties[$name]
            $normalized = $null
            if ($value -and -not [string]::IsNullOrWhiteSpace([string]$value.Value)) {
                $parsed = [DateTimeOffset]::MinValue
                if ([DateTimeOffset]::TryParse([string]$value.Value, [ref]$parsed)) {
                    $normalized = $parsed.ToUniversalTime().ToString('o')
                } else {
                    $valid = $false
                }
            }
            $times[('{0}_utc' -f $name)] = $normalized
        }
        if (($counts.trade_ticks_received -gt 0 -and
                (-not $times.last_trade_at_utc -or -not $times.last_trade_received_at_utc)) -or
            ($counts.orderbook_quotes_received -gt 0 -and
                (-not $times.last_orderbook_at_utc -or -not $times.last_orderbook_received_at_utc))) {
            $valid = $false
        }

        $symbols.Add([pscustomobject][ordered]@{
            key = $property.Name
            market = $market
            symbol = $symbol
            exchange = $exchange
            trade_ticks_received = $counts.trade_ticks_received
            bars_closed = $counts.bars_closed
            orderbook_quotes_received = $counts.orderbook_quotes_received
            usable_orderbook_quotes = $counts.usable_orderbook_quotes
            last_trade_at_utc = $times.last_trade_at_utc
            last_trade_received_at_utc = $times.last_trade_received_at_utc
            last_orderbook_at_utc = $times.last_orderbook_at_utc
            last_orderbook_received_at_utc = $times.last_orderbook_received_at_utc
        })
    }
    $rows = [object[]]$symbols.ToArray()
    return [pscustomobject]@{
        available = $valid
        reason = if ($valid) { $null } else { 'INVALID_SYMBOL_DIAGNOSTICS' }
        symbols = if ($valid) { $rows } else { $null }
    }
}

function Get-MonitorMinuteQualityDiagnostics($Status, [string]$SelectedFeed) {
    $feed = if ($SelectedFeed) { $SelectedFeed.Trim().ToLowerInvariant() } else { 'paper' }
    if ($feed -notin @('paper', 'real')) {
        return [pscustomobject]@{ available = $false; reason = 'INVALID_FEED'; symbols = $null }
    }
    $sourceProperty = if ($Status) {
        $Status.PSObject.Properties['market_data_minute_quality_by_watch_by_feed']
    } else { $null }
    $feedProperty = if ($sourceProperty -and $sourceProperty.Value) {
        $sourceProperty.Value.PSObject.Properties[$feed]
    } else { $null }
    if (-not $feedProperty -or -not $feedProperty.Value) {
        return [pscustomobject]@{ available = $false; reason = 'FIELD_UNAVAILABLE'; symbols = $null }
    }

    $valid = $true
    $expectedMarket = if ($feed -eq 'paper') { 'KR' } else { 'US' }
    $symbols = [System.Collections.Generic.List[object]]::new()
    foreach ($watchProperty in $feedProperty.Value.PSObject.Properties) {
        $market = $expectedMarket
        $symbol = ''
        $exchange = $null
        $keyMatches = if ($expectedMarket -eq 'KR') {
            if ($watchProperty.Name -match '^KR:(\d{6}):$') {
                $symbol = $Matches[1]
                $true
            } else { $false }
        } else {
            if ($watchProperty.Name -match '^US:([A-Z][A-Z0-9.\-]{0,14}):(NAS|NYS|AMS)$') {
                $symbol = $Matches[1]
                $exchange = $Matches[2]
                $true
            } else { $false }
        }
        if (-not $keyMatches) {
            $valid = $false
            continue
        }

        $totals = [ordered]@{
            closed_minutes = 0L; spread_minutes = 0L; minutes_with_trade_ticks = 0L
            minutes_with_orderbook_quotes = 0L
            minutes_with_quotes_without_spread = 0L; minutes_without_orderbook_quotes = 0L
            trade_ticks = 0L; orderbook_quotes = 0L; usable_orderbook_quotes = 0L
            empty_or_nonpositive_quotes = 0L; crossed_quotes = 0L; stale_quotes = 0L
        }
        $missingSpreadSamples = [System.Collections.Generic.List[object]]::new()
        foreach ($minuteProperty in $watchProperty.Value.PSObject.Properties) {
            $minute = $minuteProperty.Value
            if ($null -eq $minute.PSObject.Properties['bar_closed'] -or -not [bool]$minute.bar_closed) {
                continue
            }
            $rowMarket = [string]$minute.market
            $rowSymbol = ([string]$minute.symbol).Trim().ToUpperInvariant()
            $rowExchange = if ([string]::IsNullOrWhiteSpace([string]$minute.exchange)) {
                $null
            } else { ([string]$minute.exchange).Trim().ToUpperInvariant() }
            if ($rowMarket -ne $market -or $rowSymbol -ne $symbol -or $rowExchange -ne $exchange) {
                $valid = $false
                continue
            }
            $parsedMinute = [DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParse([string]$minuteProperty.Name, [ref]$parsedMinute)) {
                $valid = $false
                continue
            }
            $spreadProperty = $minute.PSObject.Properties['bar_spread_present']
            if (-not $spreadProperty) {
                $valid = $false
                continue
            }
            $counts = [ordered]@{}
            foreach ($name in @(
                'trade_ticks_received', 'orderbook_quotes_received', 'usable_orderbook_quotes',
                'empty_or_nonpositive_quotes', 'crossed_quotes', 'stale_quotes'
            )) {
                $count = 0L
                $countProperty = $minute.PSObject.Properties[$name]
                if (-not $countProperty -or
                    -not [long]::TryParse([string]$countProperty.Value, [ref]$count) -or $count -lt 0) {
                    $valid = $false
                    break
                }
                $counts[$name] = $count
            }
            if (-not $valid) { continue }

            $totals.closed_minutes++
            $totals.trade_ticks += $counts.trade_ticks_received
            $totals.orderbook_quotes += $counts.orderbook_quotes_received
            $totals.usable_orderbook_quotes += $counts.usable_orderbook_quotes
            $totals.empty_or_nonpositive_quotes += $counts.empty_or_nonpositive_quotes
            $totals.crossed_quotes += $counts.crossed_quotes
            $totals.stale_quotes += $counts.stale_quotes
            $hasSpread = [bool]$spreadProperty.Value
            if ($hasSpread) {
                $observedProperty = $minute.PSObject.Properties['bar_spread_observed_at']
                $observedAt = [DateTimeOffset]::MinValue
                $minuteStart = $parsedMinute.ToUniversalTime()
                if (-not $observedProperty -or
                    -not [DateTimeOffset]::TryParse([string]$observedProperty.Value, [ref]$observedAt) -or
                    $observedAt.ToUniversalTime() -lt $minuteStart -or
                    $observedAt.ToUniversalTime() -ge $minuteStart.AddMinutes(1)) {
                    $valid = $false
                    continue
                }
            }
            if ($hasSpread) { $totals.spread_minutes++ }
            if ($counts.trade_ticks_received -gt 0) { $totals.minutes_with_trade_ticks++ }
            if ($counts.orderbook_quotes_received -gt 0) {
                $totals.minutes_with_orderbook_quotes++
                if (-not $hasSpread) { $totals.minutes_with_quotes_without_spread++ }
            } else {
                $totals.minutes_without_orderbook_quotes++
            }
            if (-not $hasSpread -and $missingSpreadSamples.Count -lt 12) {
                $missingSpreadSamples.Add([pscustomobject]@{
                    minute = $parsedMinute.ToUniversalTime().ToString('o')
                    trade_ticks = $counts.trade_ticks_received
                    orderbook_quotes = $counts.orderbook_quotes_received
                    usable_orderbook_quotes = $counts.usable_orderbook_quotes
                    empty_or_nonpositive_quotes = $counts.empty_or_nonpositive_quotes
                    crossed_quotes = $counts.crossed_quotes
                    stale_quotes = $counts.stale_quotes
                })
            }
        }
        $symbols.Add([pscustomobject]@{
            market = $market
            symbol = $symbol
            exchange = $exchange
            closed_minutes = $totals.closed_minutes
            spread_minutes = $totals.spread_minutes
            spread_coverage_percent = if ($totals.closed_minutes -gt 0) {
                [Math]::Round(100.0 * $totals.spread_minutes / $totals.closed_minutes, 2)
            } else { $null }
            minutes_with_trade_ticks = $totals.minutes_with_trade_ticks
            minutes_with_orderbook_quotes = $totals.minutes_with_orderbook_quotes
            minutes_with_quotes_without_spread = $totals.minutes_with_quotes_without_spread
            minutes_without_orderbook_quotes = $totals.minutes_without_orderbook_quotes
            trade_ticks = $totals.trade_ticks
            orderbook_quotes = $totals.orderbook_quotes
            usable_orderbook_quotes = $totals.usable_orderbook_quotes
            empty_or_nonpositive_quotes = $totals.empty_or_nonpositive_quotes
            crossed_quotes = $totals.crossed_quotes
            stale_quotes = $totals.stale_quotes
            missing_spread_samples = @($missingSpreadSamples.ToArray())
        })
    }
    $symbolRows = $null
    if ($valid) { $symbolRows = [object[]]$symbols.ToArray() }
    return [pscustomobject]@{
        available = $valid
        reason = if ($valid) { $null } else { 'INVALID_MINUTE_QUALITY_DATA' }
        symbols = $symbolRows
    }
}

function Get-MonitorSymbolMarketDataDelta($InitialStatus, $CurrentStatus, [string]$SelectedFeed) {
    $initial = Get-MonitorSymbolMarketData $InitialStatus $SelectedFeed
    $current = Get-MonitorSymbolMarketData $CurrentStatus $SelectedFeed
    if (-not $initial.available -or -not $current.available) {
        return [pscustomobject]@{
            available = $false
            reason = if (-not $current.available) { $current.reason } else { $initial.reason }
            symbols = $null
        }
    }
    $initialByKey = @{}
    foreach ($row in $initial.symbols) { $initialByKey[$row.key] = $row }
    $currentByKey = @{}
    foreach ($row in $current.symbols) { $currentByKey[$row.key] = $row }
    $keys = @(@($initialByKey.Keys) + @($currentByKey.Keys) | Sort-Object -Unique)
    $valid = $true
    $rows = [System.Collections.Generic.List[object]]::new()
    foreach ($key in $keys) {
        $start = $initialByKey[$key]
        $end = $currentByKey[$key]
        $deltas = [ordered]@{}
        foreach ($name in @('trade_ticks_received', 'bars_closed', 'orderbook_quotes_received', 'usable_orderbook_quotes')) {
            $startCount = if ($start) { [long]$start.$name } else { 0L }
            $endCount = if ($end) { [long]$end.$name } else { 0L }
            if ($endCount -lt $startCount) { $valid = $false }
            $deltas[$name] = if ($endCount -ge $startCount) { $endCount - $startCount } else { $null }
        }
        $rows.Add([pscustomobject][ordered]@{
            key = $key
            market = if ($end) { $end.market } else { $start.market }
            symbol = if ($end) { $end.symbol } else { $start.symbol }
            exchange = if ($end) { $end.exchange } else { $start.exchange }
            trade_ticks_received = $deltas.trade_ticks_received
            bars_closed = $deltas.bars_closed
            orderbook_quotes_received = $deltas.orderbook_quotes_received
            usable_orderbook_quotes = $deltas.usable_orderbook_quotes
            last_trade_at_utc = if ($end) { $end.last_trade_at_utc } else { $null }
            last_trade_received_at_utc = if ($end) { $end.last_trade_received_at_utc } else { $null }
            last_orderbook_at_utc = if ($end) { $end.last_orderbook_at_utc } else { $null }
            last_orderbook_received_at_utc = if ($end) { $end.last_orderbook_received_at_utc } else { $null }
        })
    }
    return [pscustomobject]@{
        available = $valid
        reason = if ($valid) { $null } else { 'COUNTER_DECREASED' }
        symbols = if ($valid) { [object[]]$rows.ToArray() } else { $null }
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
                (-not $Status.last_error -or $Status.last_error -eq 'KIS_STREAM_UNAVAILABLE')) {
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
