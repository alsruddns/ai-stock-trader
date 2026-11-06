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

function Assert-ExchangeSessionQuality($Quality, [string]$Market, [string]$SessionDate, [string[]]$Symbols, [int]$ExpectedBars) {
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
        if ($row.status -ne 'COMPLETE' -or -not [bool]$row.full_session_complete -or
            [int]$row.expected_bars -ne $ExpectedBars -or [int]$row.completed_minutes -ne $ExpectedBars -or
            [int]$row.observed_bars -ne $ExpectedBars -or [int]$row.observed_completed_bars -ne $ExpectedBars -or
            [int]$row.missing_bars -ne 0 -or [int]$row.off_grid_bars -ne 0 -or
            [int]$row.duplicate_bars -ne 0 -or [int]$row.spread_bars -ne $ExpectedBars -or
            [double]$row.spread_coverage_percent -ne 100) {
            throw "Stored market-data quality is incomplete for $Market $symbol."
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
        }
    }
    return @($verified)
}

function Get-ExchangeSessionBarsDelta([int]$CurrentBars, [int]$InitialBars, $SessionOpenBarsBaseline) {
    $baseline = if ($null -ne $SessionOpenBarsBaseline) { [int]$SessionOpenBarsBaseline } else { $InitialBars }
    if ($CurrentBars -lt $baseline) {
        throw 'The closed bar counter moved backwards during the sample.'
    }
    return [pscustomobject]@{ baseline = $baseline; delta = $CurrentBars - $baseline }
}
