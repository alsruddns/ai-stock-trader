function Get-KisUsPaperSmokeQuotePrice {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object]$Quote,
        [Parameter(Mandatory)] [string]$Symbol,
        [Parameter(Mandatory)] [ValidateSet('NAS', 'NYS', 'AMS')] [string]$Exchange
    )

    $normalizedSymbol = $Symbol.Trim().ToUpperInvariant()
    if ($Quote.market -ne 'US' -or $Quote.symbol -ne $normalizedSymbol -or
        $Quote.exchange -ne $Exchange -or $Quote.currency -ne 'USD') {
        throw 'Fresh quote did not match the requested US PAPER smoke instrument.'
    }

    $price = [decimal]0
    $parsed = [decimal]::TryParse(
        [string]$Quote.price,
        [Globalization.NumberStyles]::Number,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$price
    )
    if (-not $parsed -or $price -le 0) {
        throw 'US PAPER smoke quote price is invalid.'
    }
    return $price
}

function Get-KisUsPaperSmokeLimitPrice {
    param([Parameter(Mandatory)] [decimal]$Price)

    if ($Price -le 0) { throw 'Quote price must be positive.' }
    # Keep the one-share PAPER lifecycle order small enough for the existing
    # low-balance demo account. A lower limit also reduces immediate-fill risk.
    $quoteLimitPrice = [Math]::Floor(($Price * [decimal]0.95) * [decimal]100) / [decimal]100
    $limitPrice = [Math]::Min($quoteLimitPrice, [decimal]10.00)
    if ($limitPrice -le 0) { throw 'Quote price did not produce a valid one-share limit price.' }
    return $limitPrice
}
