function Invoke-AiTraderOptionalJsonRequest {
    param([Parameter(Mandatory)][string]$Url)

    try {
        $data = Invoke-RestMethod -Uri $Url -TimeoutSec 5
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
        [Parameter(Mandatory)]$MarketDataQuality
    )

    $qualityEndpointStates = @('KR', 'US') | ForEach-Object {
        $EndpointStatus["python.market_data_quality_$_"].state
    }
    if (@($qualityEndpointStates | Where-Object { $_ -in @('HTTP_ERROR', 'UNREACHABLE') }).Count -gt 0) {
        return [ordered]@{
            sessionCompleteness = 'UNAVAILABLE'
            spreadCoverage = 'UNAVAILABLE'
            sessionsReported = 0
            incompleteSessions = 0
            sessionsMissingSpreads = 0
        }
    }
    if (@($qualityEndpointStates | Where-Object { $_ -ne 'AVAILABLE' }).Count -gt 0) {
        return [ordered]@{
            sessionCompleteness = 'UNVERIFIED'
            spreadCoverage = 'UNVERIFIED'
            sessionsReported = 0
            incompleteSessions = 0
            sessionsMissingSpreads = 0
        }
    }

    $sessions = @('KR', 'US') | ForEach-Object {
        $marketData = $MarketDataQuality[$_]
        if ($marketData -and $marketData.sessions) { @($marketData.sessions) }
    }
    if ($sessions.Count -eq 0) {
        return [ordered]@{
            sessionCompleteness = 'UNVERIFIED'
            spreadCoverage = 'UNVERIFIED'
            sessionsReported = 0
            incompleteSessions = 0
            sessionsMissingSpreads = 0
        }
    }

    $incompleteSessions = 0
    $sessionsMissingSpreads = 0
    foreach ($session in $sessions) {
        if ($session.full_session_complete -isnot [bool] -or -not $session.full_session_complete) {
            $incompleteSessions++
        }
        $observedBars = 0
        $spreadBars = -1
        if (-not [int]::TryParse([string]$session.observed_completed_bars, [ref]$observedBars) -or
                -not [int]::TryParse([string]$session.spread_bars, [ref]$spreadBars) -or
                $observedBars -lt 1 -or $spreadBars -ne $observedBars) {
            $sessionsMissingSpreads++
        }
    }
    return [ordered]@{
        sessionCompleteness = if ($incompleteSessions -eq 0) { 'COMPLETE' } else { 'INCOMPLETE' }
        spreadCoverage = if ($sessionsMissingSpreads -eq 0) { 'COMPLETE' } else { 'INCOMPLETE' }
        sessionsReported = $sessions.Count
        incompleteSessions = $incompleteSessions
        sessionsMissingSpreads = $sessionsMissingSpreads
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
            krRegisteredCount = 0
            usRegisteredCount = 0
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
        krRegisteredCount = @($rows | Where-Object { $_.market -eq 'KR' }).Count
        usRegisteredCount = @($rows | Where-Object { $_.market -eq 'US' }).Count
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
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $unverified }
    try { $lines = @(Get-Content -LiteralPath $Path -Tail 1 -ErrorAction Stop) }
    catch { return $unverified }

    $knownFailureCodes = @(
        'WEBSOCKET_NO_TICKS', 'NO_TICKS_IN_SAMPLE_WINDOW', 'EXCHANGE_SESSION_CLOSE_NOT_REACHED',
        'SESSION_FINALIZATION_TIMEOUT', 'FEED_TIMESTAMP_UNAVAILABLE', 'BAR_COUNTER_RESET',
        'FEED_NOT_READY', 'SESSION_STATUS_UNAVAILABLE', 'EXCHANGE_CALENDAR_UNAVAILABLE',
        'SAFETY_LOCK_VIOLATION', 'STORED_SESSION_QUALITY_INCOMPLETE',
        'MINIMUM_BAR_COUNT_NOT_MET', 'MONITOR_FAILED'
    )
    $knownStages = @('startup_readiness', 'exchange_calendar', 'stream_monitoring', 'session_finalization', 'completion_validation')
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
        maxNoTickSeconds = if ($hasMaxNoTick) { [Math]::Round($maxNoTick, 1) } else { $null }
        failureStage = if ($record.failure_stage -in $knownStages) { [string]$record.failure_stage } else { $null }
        failureCode = if ($record.failure_code -in $knownFailureCodes) { [string]$record.failure_code } else { $null }
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
