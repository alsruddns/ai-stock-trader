function Get-SafeKisBrokerCode {
    [CmdletBinding()]
    param([AllowNull()] [string]$ErrorDetail)

    if ([string]::IsNullOrWhiteSpace($ErrorDetail)) { return $null }
    $match = [regex]::Match($ErrorDetail, '(?i)(?:code|msg_cd)[^A-Z0-9]*([A-Z]{3,4}\d{4,5})\b')
    if (-not $match.Success) { return $null }
    return $match.Groups[1].Value.ToUpperInvariant()
}

function Get-SafeKisTransportCategory {
    [CmdletBinding()]
    param([AllowNull()] [string]$ErrorDetail)

    if ([string]::IsNullOrWhiteSpace($ErrorDetail)) { return $null }
    $match = [regex]::Match($ErrorDetail, '(?i)\btransport category:\s*(timeout|connect|network|protocol|other|invalid_response)\b')
    if (-not $match.Success) { return $null }
    return $match.Groups[1].Value.ToLowerInvariant()
}

function Get-SafeKisUpstreamHttpStatus {
    [CmdletBinding()]
    param([AllowNull()] [string]$ErrorDetail)

    if ([string]::IsNullOrWhiteSpace($ErrorDetail)) { return $null }
    $match = [regex]::Match($ErrorDetail, '(?i)\bKIS (?:[A-Za-z ]+ request failed|rejected the quote request) \(HTTP (\d{3}), code [A-Za-z0-9_-]{1,32}')
    if (-not $match.Success) { return $null }
    return $match.Groups[1].Value
}

function Get-SafeKisResponseFields {
    [CmdletBinding()]
    param([AllowNull()] [string]$ErrorDetail)

    if ([string]::IsNullOrWhiteSpace($ErrorDetail)) { return $null }
    $match = [regex]::Match($ErrorDetail, '(?i)\bKIS rejected the quote request \(HTTP \d{3}, code [A-Za-z0-9_-]{1,32}, content-type (?:[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+|unknown), response-bytes \d{1,10}, fields ([A-Za-z_][A-Za-z0-9_]{0,31}(?:,[A-Za-z_][A-Za-z0-9_]{0,31}){0,15}|none)\)')
    if (-not $match.Success -or $match.Groups[1].Value -eq 'none') { return $null }
    return $match.Groups[1].Value
}

function Format-SafeLocalApiFailure {
    [CmdletBinding()]
    param(
        [AllowNull()] [string]$Status,
        [AllowNull()] [string]$Path,
        [AllowNull()] [string]$ErrorDetail
    )

    if ($Status -notmatch '^\d{3}$') { $Status = 'unavailable' }
    if ($Path -notmatch '^/api/v1/[A-Za-z0-9_./-]+$') { $Path = '/unknown' }
    if ($ErrorDetail) {
        try {
            $payload = $ErrorDetail | ConvertFrom-Json -ErrorAction Stop
            if ($payload.detail -is [string]) { $ErrorDetail = $payload.detail }
        } catch { }
    }
    $brokerCode = Get-SafeKisBrokerCode -ErrorDetail $ErrorDetail
    $transportCategory = Get-SafeKisTransportCategory -ErrorDetail $ErrorDetail
    $upstreamStatus = Get-SafeKisUpstreamHttpStatus -ErrorDetail $ErrorDetail
    $responseFields = Get-SafeKisResponseFields -ErrorDetail $ErrorDetail
    $diagnostic = if ($brokerCode) { ", KIS code $brokerCode" }
        elseif ($transportCategory) { ", KIS transport $transportCategory" }
        elseif ($upstreamStatus -and $responseFields) { ", KIS upstream HTTP $upstreamStatus, fields $responseFields" }
        elseif ($upstreamStatus) { ", KIS upstream HTTP $upstreamStatus" }
        elseif ($responseFields) { ", KIS response fields $responseFields" }
        elseif ($ErrorDetail -match '(?i)\bKIS\b') { ', KIS detail withheld' }
        else { '' }
    return "Local preflight request failed (HTTP $Status$diagnostic) for $Path; inspect the sanitized service logs."
}

function Assert-KisPaperQuoteFresh {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object]$Quote,
        [DateTimeOffset]$Now = [DateTimeOffset]::UtcNow
    )

    $rawRetrievedAt = $Quote.retrieved_at
    if ($null -eq $rawRetrievedAt -or [string]::IsNullOrWhiteSpace([string]$rawRetrievedAt)) {
        throw 'Quote timestamp is missing or invalid; no PAPER order was submitted.'
    }

    try {
        if ($rawRetrievedAt -is [DateTimeOffset]) {
            $quoteRetrievedAt = $rawRetrievedAt.ToUniversalTime()
        } elseif ($rawRetrievedAt -is [DateTime]) {
            if ($rawRetrievedAt.Kind -eq [DateTimeKind]::Unspecified) {
                throw 'Unspecified quote timestamp.'
            }
            $quoteRetrievedAt = [DateTimeOffset]$rawRetrievedAt.ToUniversalTime()
        } else {
            $timestampText = [string]$rawRetrievedAt
            if ($timestampText -notmatch '(Z|[+-]\d{2}:\d{2})$') {
                throw 'Quote timestamp must include a timezone.'
            }
            $quoteRetrievedAt = [DateTimeOffset]::Parse(
                $timestampText,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            ).ToUniversalTime()
        }
    } catch {
        throw 'Quote timestamp is missing or invalid; no PAPER order was submitted.'
    }

    $quoteAge = $Now.ToUniversalTime() - $quoteRetrievedAt
    if ($quoteAge -lt [TimeSpan]::FromSeconds(-5) -or $quoteAge -gt [TimeSpan]::FromSeconds(60)) {
        throw 'Quote is outside the allowed freshness window; no PAPER order was submitted.'
    }
}

function ConvertTo-KisQuoteExchange {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [ValidateSet('NAS', 'NASD', 'NYS', 'NYSE', 'AMS', 'AMEX')] [string]$Exchange)

    switch ($Exchange.ToUpperInvariant()) {
        { $_ -in @('NAS', 'NASD') } { return 'NAS' }
        { $_ -in @('NYS', 'NYSE') } { return 'NYS' }
        { $_ -in @('AMS', 'AMEX') } { return 'AMS' }
    }
    throw 'Unsupported KIS exchange mapping for quote preflight.'
}

function Assert-KisPaperOrderableFunds {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object]$Account,
        [Parameter(Mandatory)] [ValidateSet('KR', 'US')] [string]$Market,
        [Parameter(Mandatory)] [ValidateSet('KRW', 'USD')] [string]$Currency,
        [Parameter(Mandatory)] [decimal]$MinimumNotional,
        [DateTimeOffset]$Now = [DateTimeOffset]::UtcNow
    )

    if ($Account.environment -ne 'paper' -or $Account.market -ne $Market -or $Account.currency -ne $Currency) {
        throw 'PAPER account snapshot did not match the requested market and currency; no order was submitted.'
    }
    if ($null -eq $Account.orderableCash) {
        throw 'KIS did not return orderable funds; no PAPER order was submitted.'
    }

    Assert-KisPaperQuoteFresh -Quote @{ retrieved_at = $Account.retrievedAt } -Now $Now
    try {
        $orderableCash = [decimal]$Account.orderableCash
    } catch {
        throw 'KIS returned invalid orderable funds; no PAPER order was submitted.'
    }
    if ($orderableCash -lt $MinimumNotional) {
        throw 'PAPER account has insufficient orderable funds for this one-share test; no order was submitted.'
    }
}

function Assert-KisPaperSessionOpen {
    param(
        [Parameter(Mandatory)] [object]$Session,
        [Parameter(Mandatory)] [ValidateSet('KR', 'US')] [string]$Market,
        [DateTimeOffset]$Now = [DateTimeOffset]::UtcNow
    )

    try {
        $evaluatedAt = [DateTimeOffset]::Parse([string]$Session.evaluated_at)
        $closeAt = [DateTimeOffset]::Parse([string]$Session.close_at)
    } catch {
        throw 'Exchange session response is missing valid timezone-aware timestamps.'
    }
    $ageSeconds = ($Now.ToUniversalTime() - $evaluatedAt.ToUniversalTime()).TotalSeconds
    if ($Session.market -ne $Market -or $Session.is_session -ne $true -or $Session.is_open -ne $true) {
        throw 'Exchange calendar does not confirm an open session for the requested market.'
    }
    if ($ageSeconds -lt -2 -or $ageSeconds -gt 10) {
        throw 'Exchange session confirmation is stale or from the future.'
    }
    if (($closeAt.ToUniversalTime() - $Now.ToUniversalTime()).TotalMinutes -lt 5) {
        throw 'PAPER smoke order is blocked within five minutes of the exchange close.'
    }
}
