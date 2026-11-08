function Get-LeanBacktestCostInputs {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [ValidateSet('KR', 'US')] [string]$Market,
        [Parameter(Mandatory)] [string]$EnvironmentFile,
        [string]$BuyCommissionBps,
        [string]$SellCommissionBps,
        [string]$SellTaxBps,
        [string]$SlippageBps
    )

    $prefix = "BACKTEST_${Market}_"
    $environmentKeys = [ordered]@{
        buy_commission = "${prefix}BUY_COMMISSION_BPS"
        sell_commission = "${prefix}SELL_COMMISSION_BPS"
        sell_tax = "${prefix}SELL_TAX_BPS"
        slippage = "${prefix}SLIPPAGE_BPS"
    }
    $explicit = [ordered]@{
        buy_commission = $BuyCommissionBps
        sell_commission = $SellCommissionBps
        sell_tax = $SellTaxBps
        slippage = $SlippageBps
    }
    $explicitCount = @($explicit.Values | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
    if ($explicitCount -gt 0 -and $explicitCount -lt $explicit.Count) {
        throw 'Pass all four explicit cost values together, or leave them all blank to use .env_local.'
    }

    $localValues = @{}
    if (Test-Path -LiteralPath $EnvironmentFile) {
        foreach ($line in Get-Content -LiteralPath $EnvironmentFile) {
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
            $separator = $trimmed.IndexOf('=')
            if ($separator -lt 1) { continue }
            $name = $trimmed.Substring(0, $separator).Trim()
            if ($environmentKeys.Values -notcontains $name) { continue }
            $value = $trimmed.Substring($separator + 1).Trim().Trim('"').Trim("'")
            if ($value) { $localValues[$name] = $value }
        }
    }

    $configuredCount = @($environmentKeys.Values | Where-Object { $localValues.ContainsKey($_) }).Count
    if ($explicitCount -eq 0 -and $configuredCount -gt 0 -and $configuredCount -lt $environmentKeys.Count) {
        throw "Incomplete $Market backtest cost assumptions in .env_local. Configure all four directional commission, sell tax, and slippage values or leave all four blank for diagnostic parity only."
    }
    $source = if ($explicitCount -eq $explicit.Count) { 'explicit_parameters' }
        elseif ($configuredCount -eq $environmentKeys.Count) { 'env_local' }
        else { 'diagnostic_defaults' }

    $values = @{}
    foreach ($costName in $environmentKeys.Keys) {
        $raw = if ($source -eq 'explicit_parameters') { $explicit[$costName] }
            elseif ($source -eq 'env_local') { $localValues[$environmentKeys[$costName]] }
            else {
                switch ($costName) {
                    'buy_commission' { '5' }
                    'sell_commission' { '5' }
                    'sell_tax' { '0' }
                    'slippage' { '5' }
                }
            }
        $parsed = [decimal]0
        if (-not [decimal]::TryParse([string]$raw, [Globalization.NumberStyles]::Float,
                [cultureinfo]::InvariantCulture, [ref]$parsed) -or $parsed -lt 0 -or $parsed -gt 10000) {
            throw "The $Market ${costName} cost assumption must be a decimal basis-point value from zero through 10000."
        }
        $values[$costName] = $parsed.ToString([cultureinfo]::InvariantCulture)
    }

    return [pscustomobject]@{
        Source = $source
        BuyCommissionBps = $values.buy_commission
        SellCommissionBps = $values.sell_commission
        SellTaxBps = $values.sell_tax
        SlippageBps = $values.slippage
    }
}
