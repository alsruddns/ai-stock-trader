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
