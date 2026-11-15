param(
    [switch]$EnableReadOnlyGetForThisRun,
    [ValidateSet(
        'readsDomesticRealBalanceWithoutPlacingOrders',
        'readsDomesticRealOrderableCashForSymbolWithoutPlacingOrders',
        'preflightsDomesticRealBuyWithFreshQuoteWithoutSubmittingAnOrder',
        'readsDomesticRealizedPnlFieldNamesWithoutPrintingFinancialValues',
        'readsDomesticSessionExecutionsWithoutPrintingTradeDetails'
    )]
    [string]$TestMethod,
    [switch]$RequireSelectedTestPass
)

$ErrorActionPreference = 'Stop'

$originalEnvironment = @{}
foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $originalEnvironment[[string]$entry.Key] = [string]$entry.Value
}

try {
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'lib\real-kis-surefire-report.ps1')
. (Join-Path $PSScriptRoot 'lib\readonly-kis-execution-gates.ps1')
$envPath = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envPath)) { $envPath = Join-Path $repoRoot '.env' }
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Missing .env_local or .env. Configure the local PostgreSQL connection and REAL KIS credentials first.'
}

foreach ($line in Get-Content -LiteralPath $envPath) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $separator = $trimmed.IndexOf('=')
    if ($separator -lt 1) { continue }
    $name = $trimmed.Substring(0, $separator).Trim()
    $value = $trimmed.Substring($separator + 1).Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}

if ($env:KIS_ACCOUNT_READ_ENABLED -ne 'true' -or $env:KIS_REAL_ACCOUNT_READ_ENABLED -ne 'true') {
    if (-not $EnableReadOnlyGetForThisRun) {
        throw 'Set both read-only gates to true in .env_local or pass -EnableReadOnlyGetForThisRun for a process-only opt-in.'
    }
    $env:KIS_ACCOUNT_READ_ENABLED = 'true'
    $env:KIS_REAL_ACCOUNT_READ_ENABLED = 'true'
}
if (-not $env:KIS_REAL_APP_KEY -or -not $env:KIS_REAL_APP_SECRET -or -not $env:KIS_REAL_ACCOUNT_NUMBER) {
    throw 'Configure KIS_REAL_APP_KEY, KIS_REAL_APP_SECRET, and KIS_REAL_ACCOUNT_NUMBER in .env_local. Values are never displayed.'
}
$realAccountNumber = $env:KIS_REAL_ACCOUNT_NUMBER -replace '-', ''
if ($realAccountNumber -notmatch '^\d{10}$') {
    throw 'KIS_REAL_ACCOUNT_NUMBER must contain an eight-digit account number and two-digit product code.'
}
if (-not $env:POSTGRES_PASSWORD) {
    throw 'Set POSTGRES_PASSWORD in .env_local before running the Spring-backed KIS integration test.'
}
if ($RequireSelectedTestPass -and [string]::IsNullOrWhiteSpace($TestMethod)) {
    throw '-RequireSelectedTestPass requires one explicit -TestMethod.'
}
if ($env:TRADING_MODE -ne 'paper') {
    throw 'REAL account smoke test requires paper mode.'
}
$executionGateValues = @{}
foreach ($name in $script:ReadOnlyKisExecutionGateNames) {
    $value = [Environment]::GetEnvironmentVariable($name, 'Process')
    $executionGateValues[$name] = $value
}
Assert-KisReadOnlyExecutionGatesDisabled -Values $executionGateValues
foreach ($name in $script:ReadOnlyKisExecutionGateNames) {
    [Environment]::SetEnvironmentVariable($name, 'false', 'Process')
}

$env:RUN_KIS_REAL_ACCOUNT_INTEGRATION_TESTS = 'true'
$env:KIS_TEST_US_ACCOUNT_READS = 'false'
$testOverrides = Get-KisReadOnlyAccountTestOverrides -TestMethod $TestMethod `
    -ConfiguredOrderableUnitPrice $env:KIS_REAL_ACCOUNT_ORDERABLE_TEST_UNIT_PRICE
foreach ($name in $testOverrides.Keys) {
    [Environment]::SetEnvironmentVariable($name, [string]$testOverrides[$name], 'Process')
}
Push-Location (Join-Path $repoRoot 'backend/trading-api')
try {
    $testSelector = if ([string]::IsNullOrWhiteSpace($TestMethod)) {
        '-Dtest=KisRealAccountIntegrationTest'
    } else {
        "-Dtest=KisRealAccountIntegrationTest#$TestMethod"
    }
    $testStartedAtUtc = [DateTime]::UtcNow
    & .\mvnw.cmd $testSelector test
    if ($LASTEXITCODE -ne 0) {
        throw "Read-only KIS REAL account integration test failed with code $LASTEXITCODE."
    }
    if ($RequireSelectedTestPass) {
        $reportPath = Join-Path (Get-Location) 'target\surefire-reports\TEST-com.aistocktrader.kis.KisRealAccountIntegrationTest.xml'
        Assert-SelectedSurefireTestPassed -ReportPath $reportPath -TestMethod $TestMethod -RunStartedAtUtc $testStartedAtUtc
    }
}
finally {
    Pop-Location
}
}
finally {
    $currentEnvironment = [Environment]::GetEnvironmentVariables('Process')
    foreach ($name in $currentEnvironment.Keys) {
        if (-not $originalEnvironment.ContainsKey([string]$name)) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
    }
    foreach ($name in $originalEnvironment.Keys) {
        Set-Item -LiteralPath "Env:$name" -Value $originalEnvironment[$name]
    }
}
