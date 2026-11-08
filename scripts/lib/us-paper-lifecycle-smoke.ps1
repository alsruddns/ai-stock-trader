function Get-KisUsPaperSmokeLimitPrice {
    param([Parameter(Mandatory)] [decimal]$Price)

    if ($Price -le 0) { throw 'Quote price must be positive.' }
    $limitPrice = [Math]::Floor(($Price * [decimal]0.95) * [decimal]100) / [decimal]100
    if ($limitPrice -le 0) { throw 'Quote price did not produce a valid one-share limit price.' }
    return $limitPrice
}
