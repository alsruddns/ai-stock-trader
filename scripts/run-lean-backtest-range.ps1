param(
    [Parameter(Mandatory = $true)]
    [string[]]$CsvFiles,

    [Parameter(Mandatory = $true)]
    [ValidateSet('KR', 'US')]
    [string]$Market,

    [Parameter(Mandatory = $true)]
    [string]$Symbol,

    [Parameter(Mandatory = $true)]
    [string[]]$Sessions,

    [int]$ExpectedBars = 0,

    [string]$ModelId,

    [string]$OutputFile,

    [string]$BuyCommissionBps,

    [string]$SellCommissionBps,

    [string]$SellTaxBps,

    [string]$SlippageBps
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\domestic-only-validation.ps1')
Assert-DomesticOnlyValidation -Market $Market -Operation 'LEAN multi-session historical backtest validation'
. (Join-Path $PSScriptRoot 'lib\lean-backtest-range.ps1')

if ($CsvFiles.Count -lt 2 -or $CsvFiles.Count -ne $Sessions.Count) {
    throw 'Provide at least two CSV files and exactly one matching session date for each file.'
}

$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$singleSessionRunner = Join-Path $PSScriptRoot 'run-lean-backtest.ps1'
$inputs = for ($index = 0; $index -lt $CsvFiles.Count; $index++) {
    $sessionDate = [datetime]::MinValue
    if (-not [datetime]::TryParseExact($Sessions[$index], 'yyyy-MM-dd',
            [cultureinfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$sessionDate)) {
        throw "Invalid session date: $($Sessions[$index]). Use yyyy-MM-dd."
    }
    [pscustomobject]@{
        File = (Resolve-Path -LiteralPath $CsvFiles[$index]).Path
        Session = $Sessions[$index]
        Date = $sessionDate
    }
}

$sessionResults = [System.Collections.Generic.List[object]]::new()
foreach ($input in ($inputs | Sort-Object Date)) {
    $arguments = @{
        CsvFile = $input.File
        Market = $Market
        Symbol = $Symbol
        Session = $input.Session
    }
    if ($ExpectedBars -gt 0) { $arguments.ExpectedBars = $ExpectedBars }
    if (-not [string]::IsNullOrWhiteSpace($ModelId)) { $arguments.ModelId = $ModelId }
    if (-not [string]::IsNullOrWhiteSpace($BuyCommissionBps)) { $arguments.BuyCommissionBps = $BuyCommissionBps }
    if (-not [string]::IsNullOrWhiteSpace($SellCommissionBps)) { $arguments.SellCommissionBps = $SellCommissionBps }
    if (-not [string]::IsNullOrWhiteSpace($SellTaxBps)) { $arguments.SellTaxBps = $SellTaxBps }
    if (-not [string]::IsNullOrWhiteSpace($SlippageBps)) { $arguments.SlippageBps = $SlippageBps }

    $json = & $singleSessionRunner @arguments | Out-String
    if ($LASTEXITCODE -ne 0) { throw "LEAN single-session replay failed for $($input.Session)." }
    try { $sessionResults.Add(($json | ConvertFrom-Json -ErrorAction Stop)) }
    catch { throw "LEAN single-session runner returned invalid JSON for $($input.Session)." }
}

$summary = New-LeanBacktestRangeSummary -Market $Market -Symbol $Symbol -SessionResults $sessionResults.ToArray()
$summaryJson = ConvertTo-Json -InputObject $summary -Depth 10
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $destination = [System.IO.Path]::GetFullPath($OutputFile)
    $parent = Split-Path -Parent $destination
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    $temporary = "$destination.tmp"
    try {
        [System.IO.File]::WriteAllText($temporary, $summaryJson + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temporary -Destination $destination -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}
$summaryJson
