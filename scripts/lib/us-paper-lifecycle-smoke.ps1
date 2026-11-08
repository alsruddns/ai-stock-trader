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
