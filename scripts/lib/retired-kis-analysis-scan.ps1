$script:RetiredKisDomesticAnalysisFunctions = @(
    'finance_balance_sheet',
    'finance_income_statement',
    'finance_financial_ratio',
    'finance_profit_ratio',
    'finance_other_major_ratios',
    'finance_stability_ratio',
    'finance_growth_ratio'
)

function Get-RetiredKisDomesticAnalysisFindings {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$RepositoryRoot)

    $root = (Resolve-Path -LiteralPath $RepositoryRoot -ErrorAction Stop).Path
    $runtimeRoots = @(
        (Join-Path $root 'backend/trading-api/src/main/java'),
        (Join-Path $root 'services/market-ai/app')
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Container }
    $pattern = '\b(?:' + (($script:RetiredKisDomesticAnalysisFunctions | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')\b'
    $findings = [System.Collections.Generic.List[object]]::new()
    foreach ($runtimeRoot in $runtimeRoots) {
        $files = Get-ChildItem -LiteralPath $runtimeRoot -File -Recurse -ErrorAction Stop |
            Where-Object { $_.Extension -in @('.java', '.kt', '.py') }
        foreach ($file in $files) {
            $lines = [IO.File]::ReadAllLines($file.FullName)
            for ($index = 0; $index -lt $lines.Length; $index++) {
                foreach ($match in [regex]::Matches($lines[$index], $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
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
