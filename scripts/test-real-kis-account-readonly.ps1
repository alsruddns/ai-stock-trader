param(
    [switch]$EnableReadOnlyGetForThisRun
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
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
if ($env:TRADING_MODE -ne 'paper' -or $env:LIVE_TRADING_ENABLED -ne 'false' -or
    $env:KIS_REAL_ORDERS_ENABLED -ne 'false' -or $env:KIS_REAL_RECONCILIATION_ENABLED -ne 'false') {
    throw 'REAL account smoke test requires paper mode with REAL orders, live trading, and REAL reconciliation disabled.'
}

$env:RUN_KIS_REAL_ACCOUNT_INTEGRATION_TESTS = 'true'
Push-Location (Join-Path $repoRoot 'backend/trading-api')
try {
    & .\mvnw.cmd '-Dtest=KisRealAccountIntegrationTest' test
    if ($LASTEXITCODE -ne 0) {
        throw "Read-only KIS REAL account integration test failed with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
