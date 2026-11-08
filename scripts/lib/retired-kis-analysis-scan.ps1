$script:RetiredKisDomesticAnalysisFunctions = @(
    'finance_balance_sheet',
    'finance_income_statement',
    'finance_financial_ratio',
    'finance_profit_ratio',
    'finance_other_major_ratios',
    'finance_stability_ratio',
    'finance_growth_ratio'
)

# Also catch direct REST clients that use the endpoint path instead of the
# official helper function names. Keep these paths scoped to the seven retired
# KIS finance APIs so unrelated market data endpoints remain allowed.
$script:RetiredKisDomesticAnalysisEndpoints = @(
    'balance-sheet',
    'income-statement',
    'financial-ratio',
    'profit-ratio',
    'other-major-ratios',
    'stability-ratio',
    'growth-ratio'
)

function Get-RetiredKisDomesticAnalysisFindings {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$RepositoryRoot)

    $root = (Resolve-Path -LiteralPath $RepositoryRoot -ErrorAction Stop).Path
    $runtimeRoots = @(
        (Join-Path $root 'backend/trading-api/src/main/java'),
        (Join-Path $root 'services/market-ai/app')
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Container }
    $functionPattern = '\b(?:' + (($script:RetiredKisDomesticAnalysisFunctions | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')\b'
    $endpointPattern = '/uapi/domestic-stock/v1/finance/(?:' +
        (($script:RetiredKisDomesticAnalysisEndpoints | ForEach-Object { [regex]::Escape($_) }) -join '|') +
        ')(?=$|[?#"''\s])'
    # Catch URL builders that keep the endpoint suffix separate from the
    # shared finance-path prefix. Arbitrary computed values remain outside the
    # guarantees of this static source scan.
    $endpointFragmentPattern = '(?<![A-Za-z0-9_-])(?:' +
        (($script:RetiredKisDomesticAnalysisEndpoints | ForEach-Object { [regex]::Escape($_) }) -join '|') +
        ')(?![A-Za-z0-9_-])'
    $findings = [System.Collections.Generic.List[object]]::new()
    foreach ($runtimeRoot in $runtimeRoots) {
        $files = Get-ChildItem -LiteralPath $runtimeRoot -File -Recurse -ErrorAction Stop |
            Where-Object { $_.Extension -in @('.java', '.kt', '.py') }
        foreach ($file in $files) {
            $lines = [IO.File]::ReadAllLines($file.FullName)
            for ($index = 0; $index -lt $lines.Length; $index++) {
                $line = $lines[$index]
                $matches = @(
                    [regex]::Matches($line, $functionPattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase) |
                        ForEach-Object { [pscustomobject]@{ Value = $_.Value } }
                    [regex]::Matches($line, $endpointPattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase) |
                        ForEach-Object { [pscustomobject]@{ Value = "endpoint:$($_.Value)" } }
                )
                if ($matches.Count -eq 0) {
                    $matches += @(
                        [regex]::Matches($line, $endpointFragmentPattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase) |
                            ForEach-Object { [pscustomobject]@{ Value = "endpoint-fragment:$($_.Value)" } }
                    )
                }
                foreach ($match in $matches) {
                    $relativePath = $file.FullName.Substring($root.Length).TrimStart([IO.Path]::DirectorySeparatorChar) -replace '\\', '/'
                    $findings.Add([pscustomobject]@{
                        Path = $relativePath
                        Line = $index + 1
                        Function = $match.Value
                    })
                }
            }
        }
    }
    return @($findings)
}
