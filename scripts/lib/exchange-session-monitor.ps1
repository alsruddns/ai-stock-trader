function ConvertTo-ExchangeSessionWindow($Response) {
    if (-not $Response -or -not $Response.is_session -or
        [string]::IsNullOrWhiteSpace([string]$Response.open_at) -or
        [string]::IsNullOrWhiteSpace([string]$Response.close_at)) {
        throw 'The exchange calendar did not confirm a trading session with open and close times.'
    }
    try {
        $openUtc = [DateTimeOffset]::Parse([string]$Response.open_at).ToUniversalTime()
        $closeUtc = [DateTimeOffset]::Parse([string]$Response.close_at).ToUniversalTime()
    } catch {
        throw 'The exchange calendar returned invalid session times.'
    }
    if ($closeUtc -le $openUtc -or $Response.market -notin @('KR', 'US')) {
        throw 'The exchange calendar returned an invalid session window.'
    }
    return [pscustomobject]@{
        market = [string]$Response.market
        session_date = [string]$Response.session_date
        open_utc = $openUtc
        close_utc = $closeUtc
    }
}

function Assert-ExpectedExchangeSessionDate(
    [Parameter(Mandatory)][ValidatePattern('^\d{4}-\d{2}-\d{2}$')][string]$ExpectedSessionDate,
    [Parameter(Mandatory)][string]$ActualSessionDate,
    [string]$Context = 'session monitor'
) {
    try {
        $expected = [DateTime]::ParseExact($ExpectedSessionDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
        $actual = [DateTime]::ParseExact($ActualSessionDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
    } catch {
        throw "Expected or actual exchange session date is invalid for $Context."
    }
    if ($expected.Date -ne $actual.Date) {
        throw "Exchange session date mismatch for $Context (expected $ExpectedSessionDate, actual $ActualSessionDate)."
    }
}

function Get-ExchangeSessionMonitorStart([string]$Market, [DateTimeOffset]$OpenUtc) {
    if ($Market -notin @('KR', 'US')) { throw 'Market must be KR or US.' }
    $leadMinutes = if ($Market -eq 'KR') { 15 } else { 5 }
    $koreaZone = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
    return [TimeZoneInfo]::ConvertTime($OpenUtc.ToUniversalTime().AddMinutes(-$leadMinutes), $koreaZone)
}

function New-ExchangeSessionMonitorTaskSettings {
    New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
        -ExecutionTimeLimit (New-TimeSpan -Hours 8) -MultipleInstances IgnoreNew `
        -RestartCount 4 -RestartInterval (New-TimeSpan -Minutes 1)
}

function Get-ExchangeSessionPhase([DateTimeOffset]$Timestamp, $SessionWindow) {
    if (-not $SessionWindow) { return 'NOT_APPLICABLE' }
    $instant = $Timestamp.ToUniversalTime()
    if ($instant -lt $SessionWindow.open_utc) { return 'PRE_OPEN' }
    if ($instant -ge $SessionWindow.close_utc) { return 'CLOSED' }
    if ($SessionWindow.market -eq 'KR' -and
        $instant -ge $SessionWindow.close_utc.AddMinutes(-10)) {
        return 'CLOSING_AUCTION'
    }
    return 'CONTINUOUS'
}

function Test-ExchangeSessionTickTimestamp([DateTimeOffset]$TickTimestamp, $SessionWindow) {
    if (-not $SessionWindow) { return $true }
    $instant = $TickTimestamp.ToUniversalTime()
    return $instant -ge $SessionWindow.open_utc -and $instant -lt $SessionWindow.close_utc
}

function Test-ExchangeSessionNoTickThresholdApplies([string]$SessionPhase) {
    return $SessionPhase -in @('CONTINUOUS', 'NOT_APPLICABLE')
}

function Test-ExchangeSessionMonitorShouldDeferNoTickFailure(
    [double]$ObservedNoTickSeconds,
    [int]$MaximumNoTickSeconds,
    [bool]$RequiresCompleteSession
) {
    if ($ObservedNoTickSeconds -lt 0 -or $MaximumNoTickSeconds -lt 1) {
        throw 'No-tick monitor limits must be positive and observed duration cannot be negative.'
    }
    return $RequiresCompleteSession -and $ObservedNoTickSeconds -gt $MaximumNoTickSeconds
}

function Update-ExchangeSessionOpenBarsBaseline(
    [bool]$AwaitingSessionOpenBaseline,
    [DateTimeOffset]$SampledAt,
    [DateTimeOffset]$SessionOpen,
    [int]$CurrentBars,
    $LastPreOpenBars,
    [int]$InitialBars
) {
    if (-not $AwaitingSessionOpenBaseline) {
        return [pscustomobject]@{ awaiting = $false; baseline = $null }
    }
    if ($SampledAt.ToUniversalTime() -lt $SessionOpen.ToUniversalTime()) {
        return [pscustomobject]@{ awaiting = $true; baseline = $CurrentBars }
    }
    $baseline = if ($null -ne $LastPreOpenBars) { [int]$LastPreOpenBars } else { $InitialBars }
    return [pscustomobject]@{ awaiting = $false; baseline = $baseline }
}

function Assert-ExchangeSessionQuality($Quality, [string]$Market, [string]$SessionDate, [string[]]$Symbols, [int]$ExpectedBars, [string]$ExpectedSource) {
    if (-not $Quality -or $Quality.market -ne $Market -or $Quality.session_date -ne $SessionDate) {
        throw 'Stored market-data quality did not match the monitored exchange session.'
    }
    $reports = @($Quality.sessions)
    if ($reports.Count -ne $Symbols.Count -or $Symbols.Count -lt 1) {
        throw 'Stored market-data quality did not cover every monitored symbol.'
    }
    $verified = foreach ($symbol in $Symbols) {
        $matches = @($reports | Where-Object { $_.symbol -eq $symbol })
        if ($matches.Count -ne 1) {
            throw "Stored market-data quality is missing a unique result for $Market $symbol."
        }
        $row = $matches[0]
        if ($row.market -ne $Market -or $row.session_date -ne $SessionDate -or $row.symbol -ne $symbol) {
            throw "Stored market-data quality row did not match $Market $symbol $SessionDate."
        }
        if ($row.status -ne 'COMPLETE' -or -not [bool]$row.full_session_complete -or
            [int]$row.expected_bars -ne $ExpectedBars -or [int]$row.completed_minutes -ne $ExpectedBars -or
            [int]$row.observed_bars -ne $ExpectedBars -or [int]$row.observed_completed_bars -ne $ExpectedBars -or
            [int]$row.missing_bars -ne 0 -or [int]$row.off_grid_bars -ne 0 -or
            [int]$row.duplicate_bars -ne 0 -or [int]$row.spread_bars -ne $ExpectedBars -or
            [double]$row.spread_coverage_percent -ne 100) {
            throw "Stored market-data quality is incomplete for $Market $symbol."
        }
        $sourceCounts = $row.source_counts
        if (-not $sourceCounts) {
            throw "Stored market-data provenance is missing for $Market $symbol."
        }
        $sourceProperties = @($sourceCounts.PSObject.Properties)
        if ($sourceProperties.Count -ne 1 -or
            $sourceProperties[0].Name -ne $ExpectedSource -or
            [int]$sourceProperties[0].Value -ne $ExpectedBars) {
            throw "Stored market-data provenance is incomplete or unexpected for $Market $symbol."
        }
        [pscustomobject]@{
            market = $Market
            symbol = $symbol
            session_date = $SessionDate
            status = 'COMPLETE'
            expected_bars = $ExpectedBars
            observed_bars = [int]$row.observed_bars
            spread_bars = [int]$row.spread_bars
            spread_coverage_percent = [double]$row.spread_coverage_percent
            source_counts = @{ $ExpectedSource = [int]$sourceProperties[0].Value }
        }
    }
    return @($verified)
}

function Get-SessionQualityDiagnostics($Quality) {
    $reports = if ($Quality) { @($Quality.sessions) } else { @() }
    $diagnostics = foreach ($row in $reports) {
        $sourceCounts = [ordered]@{}
        if ($row.source_counts) {
            foreach ($property in $row.source_counts.PSObject.Properties) {
                $count = 0
                if ($property.Name -match '^KIS_[A-Z0-9_]+$' -and
                    [int]::TryParse([string]$property.Value, [ref]$count) -and $count -ge 0) {
                    $sourceCounts[$property.Name] = $count
                }
            }
        }
        [ordered]@{
            market = [string]$row.market
            session_date = [string]$row.session_date
            symbol = ([string]$row.symbol).Trim().ToUpperInvariant()
            status = [string]$row.status
            full_session_complete = [bool]$row.full_session_complete
            expected_bars = $row.expected_bars
            observed_bars = $row.observed_bars
            completed_minutes = $row.completed_minutes
            observed_completed_bars = $row.observed_completed_bars
            missing_bars = $row.missing_bars
            off_grid_bars = $row.off_grid_bars
            duplicate_bars = $row.duplicate_bars
            spread_bars = $row.spread_bars
            spread_coverage_percent = $row.spread_coverage_percent
            source_counts = $sourceCounts
        }
    }
    return @($diagnostics)
}

function Get-ExchangeSessionBarsDelta([int]$CurrentBars, [int]$InitialBars, $SessionOpenBarsBaseline) {
    $baseline = if ($null -ne $SessionOpenBarsBaseline) { [int]$SessionOpenBarsBaseline } else { $InitialBars }
    if ($CurrentBars -lt $baseline) {
        throw 'The closed bar counter moved backwards during the sample.'
    }
    return [pscustomobject]@{ baseline = $baseline; delta = $CurrentBars - $baseline }
}

function Wait-ForExchangeSessionFinalization {
    param(
    [int]$TimeoutSeconds,
    [int]$PollIntervalSeconds,
    [scriptblock]$Probe,
    [scriptblock]$Sleep = $null
    )
    if ($TimeoutSeconds -lt 0 -or $PollIntervalSeconds -lt 1 -or -not $Probe) {
        throw 'Finalization wait requires a nonnegative timeout, positive polling interval, and a probe.'
    }
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $probeResult = $null
    do {
        $probeResult = & $Probe
        if ($probeResult -and [bool]$probeResult.ready) { break }
        $remaining = $TimeoutSeconds - $watch.Elapsed.TotalSeconds
        if ($remaining -le 0) { break }
        $sleepSeconds = [int][Math]::Min($PollIntervalSeconds, [Math]::Ceiling($remaining))
        if ($Sleep) { & $Sleep $sleepSeconds } else { Start-Sleep -Seconds $sleepSeconds }
    } while ($watch.Elapsed.TotalSeconds -lt $TimeoutSeconds)
    $watch.Stop()
    return [pscustomobject]@{
        ready = [bool]($probeResult -and $probeResult.ready)
        elapsed_seconds = [Math]::Round($watch.Elapsed.TotalSeconds, 1)
        result = $probeResult
    }
}

function Test-ExchangeSessionCompletion {
    param(
        [int]$ObservedCounterBars,
        [int]$MinimumCounterBars,
        [bool]$RequiresCompleteSession,
        [bool]$StoredSessionQualityVerified,
        [double]$MaxObservedNoTickSeconds = 0,
        [int]$MaxNoTickSeconds = 300,
        [bool]$BarStorageReady = $false
    )
    if ($MaxObservedNoTickSeconds -lt 0 -or $MaxNoTickSeconds -lt 1) {
        throw 'Session completion no-tick limits must be positive and observed duration cannot be negative.'
    }
    if ($RequiresCompleteSession) {
        return $StoredSessionQualityVerified -and $BarStorageReady -and
            $MaxObservedNoTickSeconds -le $MaxNoTickSeconds
    }
    return $ObservedCounterBars -ge $MinimumCounterBars
}

function Get-ExchangeSessionValidationScope([bool]$RequiresCompleteSession) {
    if ($RequiresCompleteSession) { return 'full_session' }
    return 'bounded_sample'
}
