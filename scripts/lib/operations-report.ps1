function Invoke-AiTraderOptionalJsonRequest {
    param(
        [Parameter(Mandatory)][string]$Url,
        [ValidateRange(1, 60)][int]$TimeoutSec = 5
    )

    try {
        $data = Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec
        return [pscustomobject]@{
            data = $data
            state = 'AVAILABLE'
            http_status = $null
            error_type = $null
        }
    }
    catch {
        $httpStatus = $null
        try {
            if ($null -ne $_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
                $parsedStatus = [int]$_.Exception.Response.StatusCode
                if ($parsedStatus -gt 0) { $httpStatus = $parsedStatus }
            }
        } catch { }
        $state = if ($null -ne $httpStatus) { 'HTTP_ERROR' } else { 'UNREACHABLE' }
        return [pscustomobject]@{
            data = $null
            state = $state
            http_status = $httpStatus
            error_type = $_.Exception.GetType().Name
        }
    }
}

function Test-AiTraderNonNegativeInteger {
    param([AllowNull()]$Value)

    if ($null -eq $Value -or $Value -is [bool]) { return $false }
    try {
        $number = [decimal]$Value
        return $number -ge 0 -and $number -le [int]::MaxValue -and [decimal]::Truncate($number) -eq $number
    } catch {
        return $false
    }
}

function Get-AiTraderTrainingReadinessSummary {
    param(
        [Parameter(Mandatory)][string]$PythonApiBaseUrl,
        [AllowNull()]$PaperSession,
        [string[]]$OperationalMarkets = @('KR')
    )

    if ('KR' -notin $OperationalMarkets) {
        return [pscustomobject]@{ state = 'OUT_OF_SCOPE'; httpStatus = $null; errorType = $null; symbols = @() }
    }
    if ($null -eq $PaperSession -or $null -eq $PaperSession.decisions) {
        return [pscustomobject]@{ state = 'SKIPPED_NO_DECISIONS'; httpStatus = $null; errorType = $null; symbols = @() }
    }

    $symbols = @($PaperSession.decisions.PSObject.Properties |
        ForEach-Object { $_.Value } |
        Where-Object { $_.market -eq 'KR' -and $_.symbol -match '^\d{6}$' } |
        ForEach-Object { ([string]$_.symbol).Trim() } |
        Select-Object -Unique)
    if ($symbols.Count -lt 1) {
        return [pscustomobject]@{ state = 'SKIPPED_NO_SYMBOLS'; httpStatus = $null; errorType = $null; symbols = @() }
    }
    if ($symbols.Count -gt 50) {
        return [pscustomobject]@{ state = 'SKIPPED_SYMBOL_LIMIT'; httpStatus = $null; errorType = $null; symbols = @() }
    }

    $rows = [System.Collections.Generic.List[object]]::new()
    $endpointStates = [System.Collections.Generic.List[string]]::new()
    $httpStatuses = [System.Collections.Generic.List[int]]::new()
    foreach ($symbol in $symbols) {
        $url = "$PythonApiBaseUrl/api/v1/market/training-readiness/KR/$([Uri]::EscapeDataString($symbol))"
        $response = Invoke-AiTraderOptionalJsonRequest -Url $url -TimeoutSec 15
        if ($response.state -ne 'AVAILABLE') {
            $endpointStates.Add([string]$response.state)
            if ($null -ne $response.http_status) { $httpStatuses.Add([int]$response.http_status) }
            $rows.Add([ordered]@{ symbol = $symbol; state = [string]$response.state; errorType = $response.error_type })
            continue
        }

        $data = $response.data
        $preflight = if ($null -ne $data) { $data.training_preflight } else { $null }
        $countsValid = $null -ne $data -and
            (Test-AiTraderNonNegativeInteger $data.eligible_sessions) -and
            (Test-AiTraderNonNegativeInteger $data.minimum_required_sessions) -and
            (Test-AiTraderNonNegativeInteger $data.additional_sessions_needed) -and
            (Test-AiTraderNonNegativeInteger $data.consecutive_eligible_sessions)
        $flagsValid = $null -ne $data -and ($data.minimum_met -is [bool]) -and
            $null -ne $preflight -and ($preflight.ready -is [bool])
        $preflightCountsValid = $null -ne $preflight -and
            (($null -eq $preflight.sample_count) -or (Test-AiTraderNonNegativeInteger $preflight.sample_count)) -and
            (($null -eq $preflight.positive_label_count) -or (Test-AiTraderNonNegativeInteger $preflight.positive_label_count)) -and
            (($null -eq $preflight.negative_label_count) -or (Test-AiTraderNonNegativeInteger $preflight.negative_label_count))
        if ($preflightCountsValid -and $null -ne $preflight.sample_count) {
            $preflightCountsValid = $null -ne $preflight.positive_label_count -and
                $null -ne $preflight.negative_label_count -and
                ([decimal]$preflight.positive_label_count + [decimal]$preflight.negative_label_count -eq [decimal]$preflight.sample_count)
        } elseif ($preflightCountsValid) {
            $preflightCountsValid = $null -eq $preflight.positive_label_count -and $null -eq $preflight.negative_label_count
        }
        $reasonsValid = $null -ne $preflight -and $preflight.reasons -is [array]
        if ($reasonsValid) {
            foreach ($reason in $preflight.reasons) {
                if ($reason -isnot [string] -or $reason.Length -gt 80 -or $reason -notmatch '^[A-Za-z0-9_]+$') {
                    $reasonsValid = $false
                    break
                }
            }
        }
        $relationshipsValid = $false
        if ($countsValid -and $flagsValid) {
            $eligible = [decimal]$data.eligible_sessions
            $minimum = [decimal]$data.minimum_required_sessions
            $additional = [decimal]$data.additional_sessions_needed
            $consecutive = [decimal]$data.consecutive_eligible_sessions
            $expectedAdditional = [Math]::Max([decimal]0, $minimum - $eligible)
            $relationshipsValid = $consecutive -le $eligible -and
                $additional -eq $expectedAdditional -and
                ([bool]$data.minimum_met -eq ($eligible -ge $minimum))
            if ($relationshipsValid -and $preflight.ready) {
                $relationshipsValid = [bool]$data.minimum_met -and $null -ne $preflight.sample_count -and
                    $preflight.reasons.Count -eq 0
            }
        }
        if (-not $countsValid -or -not $flagsValid -or -not $preflightCountsValid -or -not $reasonsValid -or -not $relationshipsValid) {
            $endpointStates.Add('INVALID_RESPONSE')
            $rows.Add([ordered]@{ symbol = $symbol; state = 'INVALID_RESPONSE'; errorType = 'InvalidResponse' })
            continue
        }

        $endpointStates.Add('AVAILABLE')
        $preflightReasons = @($preflight.reasons | Select-Object -First 10)
        $rows.Add([ordered]@{
            symbol = $symbol
            state = 'AVAILABLE'
            eligibleSessions = [int]$data.eligible_sessions
            minimumRequiredSessions = [int]$data.minimum_required_sessions
            additionalSessionsNeeded = [int]$data.additional_sessions_needed
            consecutiveEligibleSessions = [int]$data.consecutive_eligible_sessions
            minimumMet = [bool]$data.minimum_met
            trainingPreflightReady = [bool]$preflight.ready
            sampleCount = if (Test-AiTraderNonNegativeInteger $preflight.sample_count) { [int]$preflight.sample_count } else { $null }
            positiveLabelCount = if (Test-AiTraderNonNegativeInteger $preflight.positive_label_count) { [int]$preflight.positive_label_count } else { $null }
            negativeLabelCount = if (Test-AiTraderNonNegativeInteger $preflight.negative_label_count) { [int]$preflight.negative_label_count } else { $null }
            reasons = $preflightReasons
        })
    }

    $uniqueStates = @($endpointStates | Select-Object -Unique)
    $state = if ($uniqueStates.Count -eq 1) { $uniqueStates[0] } else { 'PARTIAL' }
    $httpStatus = if ($httpStatuses.Count -eq $symbols.Count -and @($httpStatuses | Select-Object -Unique).Count -eq 1) {
        $httpStatuses[0]
    } else { $null }
    $failedRows = @($rows | Where-Object { $_.state -ne 'AVAILABLE' })
    $errorTypes = @($failedRows | ForEach-Object { $_.errorType } | Where-Object { $_ } | Select-Object -Unique)
    $errorType = if ($errorTypes.Count -eq 0) { $null } elseif ($errorTypes.Count -eq 1) { $errorTypes[0] } else { 'Multiple' }
    return [pscustomobject]@{
        state = $state
        httpStatus = $httpStatus
        errorType = $errorType
        symbols = @($rows)
    }
}

function Set-AiTraderAtomicJsonFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Value,
        [ValidateRange(1, 100)][int]$Depth = 12
    )

    $directory = Split-Path -Parent $Path
    if ([string]::IsNullOrWhiteSpace($directory) -or -not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw 'The operations report output directory must already exist.'
    }
    $temporaryPath = Join-Path $directory ('.{0}.{1}.tmp' -f [IO.Path]::GetFileName($Path), [guid]::NewGuid().ToString('N'))
    $backupPath = "$temporaryPath.bak"
    try {
        $json = $Value | ConvertTo-Json -Depth $Depth
        [IO.File]::WriteAllText($temporaryPath, $json, [Text.Encoding]::UTF8)
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [IO.File]::Replace($temporaryPath, $Path, $backupPath)
        } else {
            [IO.File]::Move($temporaryPath, $Path)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
        if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
            Remove-Item -LiteralPath $backupPath -Force
        }
    }
}

function New-AiTraderOperationsReportRunPath {
    param(
        [Parameter(Mandatory)][string]$Directory,
        [DateTimeOffset]$Timestamp = [DateTimeOffset]::UtcNow
    )

    $stamp = $Timestamp.ToUniversalTime().ToString('yyyy-MM-ddTHHmmssfffZ', [Globalization.CultureInfo]::InvariantCulture)
    $runId = [guid]::NewGuid().ToString('N').Substring(0, 8)
    return Join-Path $Directory ("operations-run-{0}-{1}.json" -f $stamp, $runId)
}

function Get-AiTraderMarketDataQualitySummary {
    param(
        [Parameter(Mandatory)]$EndpointStatus,
        [Parameter(Mandatory)]$MarketDataQuality,
        [string[]]$OperationalMarkets = @('KR')
    )

    $markets = @($OperationalMarkets | ForEach-Object { ([string]$_).Trim().ToUpperInvariant() } | Select-Object -Unique)
    if ($markets.Count -eq 0 -or @($markets | Where-Object { $_ -notin @('KR', 'US') }).Count -gt 0) {
        $markets = @('KR')
    }
    $qualityEndpointStates = $markets | ForEach-Object {
        $EndpointStatus["python.market_data_quality_$_"].state
    }
    if (@($qualityEndpointStates | Where-Object { $_ -in @('HTTP_ERROR', 'UNREACHABLE') }).Count -gt 0) {
        return [ordered]@{
            sessionCompleteness = 'UNAVAILABLE'
            spreadCoverage = 'UNAVAILABLE'
            spreadObservationTimeCoverage = 'UNAVAILABLE'
            sessionsReported = 0
            incompleteSessions = 0
            sessionsMissingSpreads = 0
            sessionsMissingSpreadObservationTimes = 0
        }
    }
    if (@($qualityEndpointStates | Where-Object { $_ -ne 'AVAILABLE' }).Count -gt 0) {
        return [ordered]@{
            sessionCompleteness = 'UNVERIFIED'
            spreadCoverage = 'UNVERIFIED'
            spreadObservationTimeCoverage = 'UNVERIFIED'
            sessionsReported = 0
            incompleteSessions = 0
            sessionsMissingSpreads = 0
            sessionsMissingSpreadObservationTimes = 0
        }
    }

    $sessionList = [System.Collections.Generic.List[object]]::new()
    foreach ($market in $markets) {
        $marketData = $MarketDataQuality[$market]
        if ($marketData -and $marketData.sessions) {
            foreach ($session in @($marketData.sessions)) { $sessionList.Add($session) }
        }
    }
    $sessions = $sessionList.ToArray()
    if ($sessions.Count -eq 0) {
        return [ordered]@{
            sessionCompleteness = 'UNVERIFIED'
            spreadCoverage = 'UNVERIFIED'
            spreadObservationTimeCoverage = 'UNVERIFIED'
            sessionsReported = 0
            incompleteSessions = 0
            sessionsMissingSpreads = 0
            sessionsMissingSpreadObservationTimes = 0
        }
    }

    $incompleteSessions = 0
    $sessionsMissingSpreads = 0
    $sessionsMissingSpreadObservationTimes = 0
    foreach ($session in $sessions) {
        if ($session.full_session_complete -isnot [bool] -or -not $session.full_session_complete) {
            $incompleteSessions++
        }
        $observedBars = 0
        $spreadBars = -1
        $spreadObservationTimeBars = -1
        $hasObservedBars = [int]::TryParse([string]$session.observed_completed_bars, [ref]$observedBars)
        $hasSpreadBars = [int]::TryParse([string]$session.spread_bars, [ref]$spreadBars)
        $hasSpreadObservationTimeBars = [int]::TryParse(
            [string]$session.spread_observation_time_bars, [ref]$spreadObservationTimeBars)
        if (-not $hasObservedBars -or -not $hasSpreadBars -or
                $observedBars -lt 1 -or $spreadBars -ne $observedBars) {
            $sessionsMissingSpreads++
        }
        if (-not $hasObservedBars -or -not $hasSpreadObservationTimeBars -or
                $observedBars -lt 1 -or $spreadObservationTimeBars -ne $observedBars) {
            $sessionsMissingSpreadObservationTimes++
        }
    }
    return [ordered]@{
        sessionCompleteness = if ($incompleteSessions -eq 0) { 'COMPLETE' } else { 'INCOMPLETE' }
        spreadCoverage = if ($sessionsMissingSpreads -eq 0) { 'COMPLETE' } else { 'INCOMPLETE' }
        spreadObservationTimeCoverage = if ($sessionsMissingSpreadObservationTimes -eq 0) { 'COMPLETE' } else { 'INCOMPLETE' }
        sessionsReported = $sessions.Count
        incompleteSessions = $incompleteSessions
        sessionsMissingSpreads = $sessionsMissingSpreads
        sessionsMissingSpreadObservationTimes = $sessionsMissingSpreadObservationTimes
    }
}

function Get-AiTraderSessionMonitorScheduleSummary {
    try {
        $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
            $_.TaskName -match '^AIStockTrader-(KRX|US)-Session-Monitor-\d{8}$'
        })
    }
    catch {
        return [ordered]@{
            state = 'UNAVAILABLE'
            errorType = $_.Exception.GetType().Name
            timeZoneId = [TimeZoneInfo]::Local.Id
            registeredCount = 0
            enabledCount = 0
            krRegisteredCount = 0
            usRegisteredCount = 0
            krEnabledCount = 0
            usEnabledCount = 0
            tasks = @()
        }
    }

    $rows = @()
    foreach ($task in $tasks) {
        $market = if ($task.TaskName -like 'AIStockTrader-KRX-*') { 'KR' } else { 'US' }
        $logonType = if ($task.Principal -and $task.Principal.LogonType) { [string]$task.Principal.LogonType } else { 'UNKNOWN' }
        $requiresInteractiveLogon = if ($logonType -eq 'UNKNOWN') { $null } else { $logonType -eq 'Interactive' }
        try {
            $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -ErrorAction Stop
            $rows += [ordered]@{
                market = $market
                taskName = $task.TaskName
                state = [string]$task.State
                enabled = [bool]$task.Settings.Enabled
                principalLogonType = $logonType
                requiresInteractiveLogon = $requiresInteractiveLogon
                wakeToRun = [bool]$task.Settings.WakeToRun
                startWhenAvailable = [bool]$task.Settings.StartWhenAvailable
                restartCount = [int]$task.Settings.RestartCount
                restartInterval = [string]$task.Settings.RestartInterval
                nextRunTime = if ($info.NextRunTime -and $info.NextRunTime.Year -gt 2000) { $info.NextRunTime.ToString('o') } else { $null }
                lastRunTime = if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) { $info.LastRunTime.ToString('o') } else { $null }
                lastTaskResult = [int]$info.LastTaskResult
            }
        }
        catch {
            $rows += [ordered]@{
                market = $market
                taskName = $task.TaskName
                state = 'UNKNOWN'
                enabled = [bool]$task.Settings.Enabled
                principalLogonType = $logonType
                requiresInteractiveLogon = $requiresInteractiveLogon
                wakeToRun = [bool]$task.Settings.WakeToRun
                startWhenAvailable = [bool]$task.Settings.StartWhenAvailable
                restartCount = [int]$task.Settings.RestartCount
                restartInterval = [string]$task.Settings.RestartInterval
                nextRunTime = $null
                lastRunTime = $null
                lastTaskResult = $null
            }
        }
    }

    return [ordered]@{
        state = if ($rows.Count -eq 0) { 'UNVERIFIED' } elseif (@($rows | Where-Object { $_.state -eq 'UNKNOWN' }).Count -gt 0) { 'PARTIAL' } else { 'AVAILABLE' }
        errorType = $null
        timeZoneId = [TimeZoneInfo]::Local.Id
        registeredCount = $rows.Count
        enabledCount = @($rows | Where-Object { $_.enabled }).Count
        krRegisteredCount = @($rows | Where-Object { $_.market -eq 'KR' }).Count
        usRegisteredCount = @($rows | Where-Object { $_.market -eq 'US' }).Count
        krEnabledCount = @($rows | Where-Object { $_.market -eq 'KR' -and $_.enabled }).Count
        usEnabledCount = @($rows | Where-Object { $_.market -eq 'US' -and $_.enabled }).Count
        tasks = @($rows | Sort-Object market, taskName)
    }
}

function Get-AiTraderDomesticSessionMonitorSummary {
    param([Parameter(Mandatory)][string]$Path)

    $unverified = [ordered]@{
        state = 'UNVERIFIED'; recordedAt = $null; sessionDate = $null; validationScope = $null
        sessionCloseReached = $null; sessionQualityStatus = $null; ticksDelta = $null; barsDelta = $null
        orderbookFramesDelta = $null; orderbookUpdatesDelta = $null
        maxNoTickSeconds = $null; failureStage = $null; failureCode = $null
        maxStatusUnavailableSeconds = $null; statusReadRetryCount = $null
        statusReadFailureCategories = @(); statusReadLastFailureCategory = $null
        barStorageDiagnosticAvailable = $false; barStorageReady = $null; barStorageState = $null
        barStoragePendingCount = $null; barStorageRetryCount = $null; barStorageReason = 'STATUS_UNAVAILABLE'
        symbolMarketDataStatus = 'UNAVAILABLE'; symbolMarketData = @()
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $unverified }
    try { $lines = @(Get-Content -LiteralPath $Path -Tail 1 -ErrorAction Stop) }
    catch { return $unverified }

    $knownFailureCodes = @(
        'WEBSOCKET_NO_TICKS', 'NO_TICKS_IN_SAMPLE_WINDOW', 'EXCHANGE_SESSION_CLOSE_NOT_REACHED',
        'SESSION_FINALIZATION_TIMEOUT', 'FEED_TIMESTAMP_UNAVAILABLE', 'BAR_COUNTER_RESET',
        'FEED_NOT_READY', 'SESSION_STATUS_UNAVAILABLE', 'EXCHANGE_CALENDAR_UNAVAILABLE',
        'BAR_STORAGE_NOT_DRAINED',
        'SAFETY_LOCK_VIOLATION', 'STORED_SESSION_QUALITY_INCOMPLETE',
        'MINIMUM_BAR_COUNT_NOT_MET', 'MONITOR_FAILED'
    )
    $knownStages = @('startup_readiness', 'exchange_calendar', 'stream_monitoring', 'post_close_validation', 'session_finalization', 'completion_validation')
    if ($lines.Count -ne 1) { return $unverified }
    try { $record = $lines[0] | ConvertFrom-Json -ErrorAction Stop }
    catch { return $unverified }
    if (-not $record -or $record.market_session -ne 'KR' -or $record.selected_feed -ne 'paper' -or
        $record.result -notin @('running', 'passed', 'failed')) { return $unverified }
    $recordedAt = $null
    try { $recordedAt = [DateTimeOffset]::Parse([string]$record.recorded_at_utc).ToString('o') }
    catch { return $unverified }
    $sessionDate = [string]$record.market_session_date
    if ($sessionDate -notmatch '^\d{4}-\d{2}-\d{2}$') { return $unverified }

    $ticks = 0
    $hasTicks = [int]::TryParse([string]$record.ticks_delta, [ref]$ticks) -and $ticks -ge 0
    $bars = 0
    $hasBars = [int]::TryParse([string]$record.bars_delta, [ref]$bars) -and $bars -ge 0
    $maxNoTick = 0.0
    $hasMaxNoTick = [double]::TryParse([string]$record.max_observed_no_tick_seconds,
        [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$maxNoTick) -and
        $maxNoTick -ge 0
    $statusReadRetryCount = 0
    $hasStatusReadRetryCount = [int]::TryParse([string]$record.status_read_retry_count, [ref]$statusReadRetryCount) -and
        $statusReadRetryCount -ge 0 -and $statusReadRetryCount -le 1000
    $maxStatusUnavailableSeconds = 0.0
    $hasMaxStatusUnavailable = [double]::TryParse([string]$record.max_status_unavailable_seconds,
        [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$maxStatusUnavailableSeconds) -and
        -not [double]::IsNaN($maxStatusUnavailableSeconds) -and -not [double]::IsInfinity($maxStatusUnavailableSeconds) -and
        $maxStatusUnavailableSeconds -ge 0 -and $maxStatusUnavailableSeconds -le 600
    $knownStatusReadFailureCategories = @(
        'TIMEOUT', 'CONNECTION_FAILURE', 'DNS_FAILURE', 'TLS_FAILURE', 'CONNECTION_CLOSED',
        'NETWORK_FAILURE', 'REQUEST_FAILURE'
    )
    $statusReadFailureCategories = [System.Collections.Generic.List[object]]::new()
    $statusReadCounts = $record.status_read_failure_categories
    if ($statusReadCounts) {
        foreach ($property in $statusReadCounts.PSObject.Properties) {
            if ($property.Name -notin $knownStatusReadFailureCategories -and
                $property.Name -notmatch '^HTTP_[45]\d\d$') { continue }
            $count = 0
            if ([int]::TryParse([string]$property.Value, [ref]$count) -and $count -gt 0 -and $count -le 1000) {
                $statusReadFailureCategories.Add([pscustomobject]@{ category = $property.Name; count = $count })
            }
        }
    }
    $statusReadLastFailureCategory = [string]$record.status_read_last_failure_category
    if ($statusReadLastFailureCategory -notin $knownStatusReadFailureCategories -and
        $statusReadLastFailureCategory -notmatch '^HTTP_[45]\d\d$') {
        $statusReadLastFailureCategory = $null
    }
    $knownBarStorageStates = @('RUNNING', 'DISABLED', 'ERROR', 'DRAIN_TIMEOUT', 'STARTING', 'RECOVERING')
    $barStorageState = if ([string]$record.bar_storage_state -in $knownBarStorageStates) {
        [string]$record.bar_storage_state
    } else { $null }
    $barStoragePendingCount = 0L
    $hasBarStoragePendingCount = [long]::TryParse([string]$record.bar_storage_pending_count, [ref]$barStoragePendingCount) -and
        $barStoragePendingCount -ge 0 -and $barStoragePendingCount -le 1000000
    $barStorageRetryCount = 0L
    $hasBarStorageRetryCount = [long]::TryParse([string]$record.bar_storage_retry_count, [ref]$barStorageRetryCount) -and
        $barStorageRetryCount -ge 0 -and $barStorageRetryCount -le 1000000
    $knownBarStorageFailureKinds = @(
        'BAR_OUTBOX_FULL', 'BAR_OUTBOX_WRITE_FAILURE', 'BAR_OUTBOX_ACK_FAILURE',
        'POSTGRES_CONNECTION_FAILURE', 'POSTGRES_TRANSACTION_RETRYABLE', 'POSTGRES_WRITE_FAILURE'
    )
    $barStorageFailureKind = if ([string]$record.bar_storage_failure_kind -in $knownBarStorageFailureKinds) {
        [string]$record.bar_storage_failure_kind
    } else { $null }
    $barStorageAvailable = $null -ne $barStorageState -and $hasBarStoragePendingCount
    $barStorageReady = if ($barStorageAvailable) {
        $barStorageState -eq 'RUNNING' -and $barStoragePendingCount -eq 0
    } else { $null }
    $barStorageReason = if (-not $barStorageAvailable) { 'STATUS_UNAVAILABLE' }
        elseif ($barStorageState -ne 'RUNNING') { 'STORAGE_NOT_RUNNING' }
        elseif ($barStoragePendingCount -gt 0) { 'PENDING_BARS' }
        else { 'READY' }
    $symbolMarketDataStatus = 'UNAVAILABLE'
    $symbolMarketData = @()
    $symbolDelta = $record.symbol_market_data_delta
    if ($symbolDelta -and $symbolDelta.available -eq $true) {
        $safeRows = [System.Collections.Generic.List[object]]::new()
        $symbolRowsValid = $true
        foreach ($row in @($symbolDelta.symbols)) {
            $market = ([string]$row.market).Trim().ToUpperInvariant()
            $symbol = ([string]$row.symbol).Trim().ToUpperInvariant()
            $exchange = if ([string]::IsNullOrWhiteSpace([string]$row.exchange)) { $null } else { ([string]$row.exchange).Trim().ToUpperInvariant() }
            if ($market -ne 'KR' -or $null -ne $exchange -or $symbol -notmatch '^\d{6}$' -or
                [string]$row.key -ne "KR:${symbol}:") {
                $symbolRowsValid = $false
                continue
            }

            $counts = @{}
            foreach ($counter in @('trade_ticks_received', 'bars_closed', 'orderbook_quotes_received', 'usable_orderbook_quotes')) {
                $count = 0L
                if (-not [long]::TryParse([string]$row.$counter, [ref]$count) -or $count -lt 0) {
                    $symbolRowsValid = $false
                    break
                }
                $counts[$counter] = $count
            }
            if (-not $symbolRowsValid) { continue }

            $times = @{}
            foreach ($timeField in @('last_trade_at_utc', 'last_trade_received_at_utc', 'last_orderbook_at_utc', 'last_orderbook_received_at_utc')) {
                $normalized = $null
                if (-not [string]::IsNullOrWhiteSpace([string]$row.$timeField)) {
                    $parsed = [DateTimeOffset]::MinValue
                    if ([DateTimeOffset]::TryParse([string]$row.$timeField, [ref]$parsed)) {
                        $normalized = $parsed.ToUniversalTime().ToString('o')
                    } else {
                        $symbolRowsValid = $false
                    }
                }
                $times[$timeField] = $normalized
            }
            if (($counts.trade_ticks_received -gt 0 -and
                    (-not $times.last_trade_at_utc -or -not $times.last_trade_received_at_utc)) -or
                ($counts.orderbook_quotes_received -gt 0 -and
                    (-not $times.last_orderbook_at_utc -or -not $times.last_orderbook_received_at_utc))) {
                $symbolRowsValid = $false
            }
            if (-not $symbolRowsValid) { continue }

            $safeRows.Add([pscustomobject]@{
                key = [string]$row.key
                market = 'KR'
                symbol = $symbol
                tradeTicksReceived = $counts.trade_ticks_received
                barsClosed = $counts.bars_closed
                orderbookQuotesReceived = $counts.orderbook_quotes_received
                usableOrderbookQuotes = $counts.usable_orderbook_quotes
                lastTradeAtUtc = $times.last_trade_at_utc
                lastTradeReceivedAtUtc = $times.last_trade_received_at_utc
                lastOrderbookAtUtc = $times.last_orderbook_at_utc
                lastOrderbookReceivedAtUtc = $times.last_orderbook_received_at_utc
            })
        }
        if ($symbolRowsValid -and $safeRows.Count -gt 0) {
            $symbolMarketDataStatus = 'AVAILABLE'
            $symbolMarketData = [object[]]$safeRows.ToArray()
        } else {
            $symbolMarketDataStatus = 'UNVERIFIED'
        }
    }
    $paperDiagnostics = if ($record.feed_diagnostics_delta) { $record.feed_diagnostics_delta.PSObject.Properties['paper'] } else { $null }
    $paperDiagnosticValue = if ($paperDiagnostics) { $paperDiagnostics.Value } else { $null }
    $transactionCounts = if ($paperDiagnosticValue) { $paperDiagnosticValue.transaction_counts } else { $null }
    $orderbookFramesValue = 0
    $hasOrderbookFrames = $paperDiagnosticValue -and $paperDiagnosticValue.transaction_counts_available -eq $true -and
        $transactionCounts -and [int]::TryParse([string]$transactionCounts.H0STASP0, [ref]$orderbookFramesValue) -and $orderbookFramesValue -ge 0
    $orderbookFrames = if ($hasOrderbookFrames) { $orderbookFramesValue } else { $null }
    $orderbookUpdates = 0
    $hasOrderbookUpdates = $paperDiagnosticValue -and $paperDiagnosticValue.orderbook_updates_available -eq $true -and
        [int]::TryParse([string]$paperDiagnosticValue.orderbook_updates, [ref]$orderbookUpdates) -and $orderbookUpdates -ge 0
    $rawAcknowledgements = if ($paperDiagnosticValue) { $paperDiagnosticValue.subscription_acknowledgements } else { $null }
    $acceptedAcknowledgements = 0L
    $requiredAcknowledgements = 0L
    $hasSubscriptionAcknowledgements = $paperDiagnosticValue -and
        $paperDiagnosticValue.subscription_acknowledgements_available -eq $true -and
        $rawAcknowledgements -and
        [long]::TryParse([string]$rawAcknowledgements.accepted_count, [ref]$acceptedAcknowledgements) -and
        [long]::TryParse([string]$rawAcknowledgements.required_count, [ref]$requiredAcknowledgements) -and
        $acceptedAcknowledgements -ge 0 -and $acceptedAcknowledgements -le 1000000 -and
        $requiredAcknowledgements -ge 0 -and $requiredAcknowledgements -le 1000000 -and
        $acceptedAcknowledgements -le $requiredAcknowledgements -and
        ($rawAcknowledgements.complete -is [bool]) -and
        ([bool]$rawAcknowledgements.complete -eq ($acceptedAcknowledgements -eq $requiredAcknowledgements))
    return [ordered]@{
        state = ([string]$record.result).ToUpperInvariant()
        recordedAt = $recordedAt
        sessionDate = $sessionDate
        validationScope = if ($record.validation_scope -in @('full_session', 'bounded_sample')) { [string]$record.validation_scope } else { $null }
        sessionCloseReached = if ($record.session_close_reached -is [bool]) { [bool]$record.session_close_reached } else { $null }
        sessionQualityStatus = if ($record.session_quality_status -in @('verified', 'incomplete', 'not_checked')) { [string]$record.session_quality_status } else { $null }
        ticksDelta = if ($hasTicks) { $ticks } else { $null }
        barsDelta = if ($hasBars) { $bars } else { $null }
        orderbookFramesDelta = if ($hasOrderbookFrames) { $orderbookFrames } else { $null }
        orderbookUpdatesDelta = if ($hasOrderbookUpdates) { $orderbookUpdates } else { $null }
        subscriptionAcknowledgementsAvailable = [bool]$hasSubscriptionAcknowledgements
        subscriptionAcknowledgements = if ($hasSubscriptionAcknowledgements) {
            [ordered]@{
                acceptedCount = $acceptedAcknowledgements
                requiredCount = $requiredAcknowledgements
                complete = [bool]$rawAcknowledgements.complete
            }
        } else { $null }
        maxNoTickSeconds = if ($hasMaxNoTick) { [Math]::Round($maxNoTick, 1) } else { $null }
        failureStage = if ($record.failure_stage -in $knownStages) { [string]$record.failure_stage } else { $null }
        failureCode = if ($record.failure_code -in $knownFailureCodes) { [string]$record.failure_code } else { $null }
        maxStatusUnavailableSeconds = if ($hasMaxStatusUnavailable) { [Math]::Round($maxStatusUnavailableSeconds, 1) } else { $null }
        statusReadRetryCount = if ($hasStatusReadRetryCount) { $statusReadRetryCount } else { $null }
        statusReadFailureCategories = @($statusReadFailureCategories.ToArray())
        statusReadLastFailureCategory = $statusReadLastFailureCategory
        barStorageDiagnosticAvailable = [bool]$barStorageAvailable
        barStorageReady = $barStorageReady
        barStorageState = $barStorageState
        barStoragePendingCount = if ($hasBarStoragePendingCount) { $barStoragePendingCount } else { $null }
        barStorageRetryCount = if ($hasBarStorageRetryCount) { $barStorageRetryCount } else { $null }
        barStorageFailureKind = $barStorageFailureKind
        barStorageReason = $barStorageReason
        symbolMarketDataStatus = $symbolMarketDataStatus
        symbolMarketData = $symbolMarketData
    }
}

function Get-AiTraderLocalStackTaskSummary {
    $taskName = 'AIStockTrader-LocalStack'
    try {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        if (-not $task) {
            return [ordered]@{
                state = 'UNVERIFIED'
                errorType = $null
                principalLogonType = 'UNKNOWN'
                requiresInteractiveLogon = $null
                enabled = $false
                wakeToRun = $false
                startWhenAvailable = $false
                nextRunTime = $null
                lastRunTime = $null
                lastTaskResult = $null
            }
        }
        $info = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction Stop
        $logonType = if ($task.Principal -and $task.Principal.LogonType) { [string]$task.Principal.LogonType } else { 'UNKNOWN' }
        return [ordered]@{
            state = [string]$task.State
            errorType = $null
            principalLogonType = $logonType
            requiresInteractiveLogon = if ($logonType -eq 'UNKNOWN') { $null } else { $logonType -eq 'Interactive' }
            enabled = [bool]$task.Settings.Enabled
            wakeToRun = [bool]$task.Settings.WakeToRun
            startWhenAvailable = [bool]$task.Settings.StartWhenAvailable
            nextRunTime = if ($info.NextRunTime -and $info.NextRunTime.Year -gt 2000) { $info.NextRunTime.ToString('o') } else { $null }
            lastRunTime = if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) { $info.LastRunTime.ToString('o') } else { $null }
            lastTaskResult = [int]$info.LastTaskResult
        }
    }
    catch {
        return [ordered]@{
            state = 'UNAVAILABLE'
            errorType = $_.Exception.GetType().Name
            principalLogonType = 'UNKNOWN'
            requiresInteractiveLogon = $null
            enabled = $false
            wakeToRun = $false
            startWhenAvailable = $false
            nextRunTime = $null
            lastRunTime = $null
            lastTaskResult = $null
        }
    }
}

function Get-AiTraderValidationTaskScheduleSummary {
    $taskDefinitions = @(
        @{ Pattern = '^AIStockTrader-(KRX|US)-Paper-Lifecycle-Smoke-(\d{8})$'; Kind = 'PAPER_LIFECYCLE'; MarketGroup = 1; DateGroup = 2 },
        @{ Pattern = '^AIStockTrader-P4-Model-(Train|Holdout)-(KR|US)-([A-Z0-9]+)-(\d{8})$'; Kind = 'P4_MODEL'; ModeGroup = 1; MarketGroup = 2; SymbolGroup = 3; DateGroup = 4 }
    )
    try {
        $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
            $name = $_.TaskName
            @($taskDefinitions | Where-Object { $name -match $_.Pattern }).Count -gt 0
        })
    }
    catch {
        return [ordered]@{
            state = 'UNAVAILABLE'
            errorType = $_.Exception.GetType().Name
            timeZoneId = [TimeZoneInfo]::Local.Id
            registeredCount = 0
            paperLifecycleCount = 0
            modelTrainingCount = 0
            modelHoldoutCount = 0
            tasks = @()
        }
    }

    $rows = @()
    foreach ($task in $tasks) {
        $definition = $taskDefinitions | Where-Object { $task.TaskName -match $_.Pattern } | Select-Object -First 1
        if ($null -eq $definition) { continue }
        $matchedTask = [regex]::Match($task.TaskName, $definition.Pattern)
        $kind = $definition.Kind
        $market = $matchedTask.Groups[$definition.MarketGroup].Value
        if ($market -eq 'KRX') { $market = 'KR' }
        if ($kind -eq 'P4_MODEL') { $kind = 'P4_MODEL_' + $matchedTask.Groups[$definition.ModeGroup].Value.ToUpperInvariant() }
        $logonType = if ($task.Principal -and $task.Principal.LogonType) { [string]$task.Principal.LogonType } else { 'UNKNOWN' }
        $row = [ordered]@{
            kind = $kind
            market = $market
            symbol = if ($definition.SymbolGroup) { $matchedTask.Groups[$definition.SymbolGroup].Value } else { $null }
            taskDate = $matchedTask.Groups[$definition.DateGroup].Value
            taskName = [string]$task.TaskName
            state = [string]$task.State
            enabled = [bool]$task.Settings.Enabled
            principalLogonType = $logonType
            requiresInteractiveLogon = if ($logonType -eq 'UNKNOWN') { $null } else { $logonType -eq 'Interactive' }
            wakeToRun = [bool]$task.Settings.WakeToRun
            startWhenAvailable = [bool]$task.Settings.StartWhenAvailable
            nextRunTime = $null
            lastRunTime = $null
            lastTaskResult = $null
        }
        try {
            $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -ErrorAction Stop
            $row.nextRunTime = if ($info.NextRunTime -and $info.NextRunTime.Year -gt 2000) { $info.NextRunTime.ToString('o') } else { $null }
            $row.lastRunTime = if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) { $info.LastRunTime.ToString('o') } else { $null }
            $row.lastTaskResult = [int]$info.LastTaskResult
        }
        catch {
            $row.state = 'UNKNOWN'
        }
        $rows += $row
    }

    return [ordered]@{
        state = if ($rows.Count -eq 0) { 'UNVERIFIED' } elseif (@($rows | Where-Object { $_.state -eq 'UNKNOWN' }).Count -gt 0) { 'PARTIAL' } else { 'AVAILABLE' }
        errorType = $null
        timeZoneId = [TimeZoneInfo]::Local.Id
        registeredCount = $rows.Count
        paperLifecycleCount = @($rows | Where-Object { $_.kind -eq 'PAPER_LIFECYCLE' }).Count
        modelTrainingCount = @($rows | Where-Object { $_.kind -eq 'P4_MODEL_TRAIN' }).Count
        modelHoldoutCount = @($rows | Where-Object { $_.kind -eq 'P4_MODEL_HOLDOUT' }).Count
        tasks = @($rows | Sort-Object kind, market, symbol, taskName)
    }
}

function Get-AiTraderPaperPreflightSummary {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [ordered]@{
            state = 'MISSING'; checkedAtUtc = $null; result = $null; stage = $null
            market = $null; symbol = $null; currency = $null
            quoteFreshnessCheckPassed = $null; orderableFundsCheckPassed = $null
            orderSent = $false; cancelSent = $false
        }
    }

    try {
        $jsonText = Get-Content -LiteralPath $Path -Raw
        $raw = $jsonText | ConvertFrom-Json
        $market = [string]$raw.market
        $symbol = [string]$raw.symbol
        $currency = [string]$raw.currency
        $result = [string]$raw.result
        $stage = [string]$raw.stage
        # Windows PowerShell 5.1 ConvertFrom-Json eagerly converts ISO timestamps
        # to local DateTime values and drops the original offset. Read this one
        # allowlisted string from the JSON text so timezone validation stays strict.
        $timestampMatch = [regex]::Match($jsonText, '"checked_at_utc"\s*:\s*"([^"]{1,64})"')
        if (-not $timestampMatch.Success) { throw 'Paper preflight timestamp is missing.' }
        $timestampText = $timestampMatch.Groups[1].Value
        $timestamp = [DateTimeOffset]::MinValue
        $validSymbol = if ($market -eq 'KR') { $symbol -match '^\d{6}$' } else { $symbol -match '^[A-Z][A-Z0-9.-]{0,15}$' }
        $valid = $raw.schema_version -eq 1 -and $raw.mode -eq 'PAPER_READ_ONLY_PREFLIGHT' `
            -and $market -in @('KR', 'US') -and $validSymbol `
            -and (($market -eq 'KR' -and $currency -eq 'KRW') -or ($market -eq 'US' -and $currency -eq 'USD')) `
            -and $result -in @('PASSED', 'FAILED') `
            -and $stage -in @('Configuration', 'LocalServiceState', 'Quote', 'QuoteValidation', 'Account', 'OrderableFunds', 'Complete') `
            -and $raw.order_sent -is [bool] -and -not $raw.order_sent `
            -and $raw.cancel_sent -is [bool] -and -not $raw.cancel_sent `
            -and ($null -eq $raw.quote_freshness_check_passed -or $raw.quote_freshness_check_passed -is [bool]) `
            -and ($null -eq $raw.orderable_funds_check_passed -or $raw.orderable_funds_check_passed -is [bool]) `
            -and $timestampText -match '(Z|[+-]\d{2}:\d{2})$' `
            -and [DateTimeOffset]::TryParse($timestampText, [Globalization.CultureInfo]::InvariantCulture, `
                [Globalization.DateTimeStyles]::None, [ref]$timestamp) `
            -and $timestamp -le [DateTimeOffset]::UtcNow.AddMinutes(2)
        if (-not $valid -or ($result -eq 'PASSED' -and
                ($stage -ne 'Complete' -or -not $raw.quote_freshness_check_passed -or -not $raw.orderable_funds_check_passed))) {
            throw 'Paper preflight evidence failed allowlist validation.'
        }
        return [ordered]@{
            state = 'AVAILABLE'
            checkedAtUtc = $timestamp.ToUniversalTime().ToString('o')
            result = $result
            stage = $stage
            market = $market
            symbol = $symbol
            currency = $currency
            quoteFreshnessCheckPassed = $raw.quote_freshness_check_passed
            orderableFundsCheckPassed = $raw.orderable_funds_check_passed
            orderSent = $false
            cancelSent = $false
        }
    }
    catch {
        return [ordered]@{
            state = 'INVALID'; checkedAtUtc = $null; result = $null; stage = $null
            market = $null; symbol = $null; currency = $null
            quoteFreshnessCheckPassed = $null; orderableFundsCheckPassed = $null
            orderSent = $false; cancelSent = $false
        }
    }
}
