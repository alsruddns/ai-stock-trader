[CmdletBinding()]
param(
    [switch]$EnableReadOnlyGetForThisRun,
    [ValidateSet('KR', 'US', 'ALL')] [string]$Market = 'KR',
    [string]$KoreanSymbol = '005930',
    [string]$Symbol = 'AAPL',
    [ValidateSet('NAS', 'NYS', 'AMS')] [string]$Exchange = 'NAS'
)

$ErrorActionPreference = 'Stop'
if ($Market -ne 'KR') {
    throw 'Current KIS market-data validation scope is domestic KR only. No US or ALL request was sent.'
}
$originalEnvironment = @{}
foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $originalEnvironment[[string]$entry.Key] = [string]$entry.Value
}

try {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $envPath = Join-Path $repoRoot '.env_local'
    if (-not (Test-Path -LiteralPath $envPath)) { $envPath = Join-Path $repoRoot '.env' }
    if (-not (Test-Path -LiteralPath $envPath)) { throw 'Missing local .env_local or .env file.' }

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

    if (-not $EnableReadOnlyGetForThisRun) {
        throw 'Pass -EnableReadOnlyGetForThisRun to opt in to isolated REAL market-data GETs.'
    }
    if ($env:TRADING_MODE -ne 'paper' -or $env:LIVE_TRADING_ENABLED -ne 'false' -or
        $env:KIS_PAPER_ORDERS_ENABLED -ne 'false' -or $env:KIS_REAL_ORDERS_ENABLED -ne 'false' -or
        $env:KIS_REAL_RECONCILIATION_ENABLED -ne 'false' -or $env:KIS_REAL_ACCOUNT_READ_ENABLED -ne 'false' -or
        $env:PAPER_AUTOTRADING_ENABLED -ne 'false') {
        throw 'REAL quote probe requires PAPER mode with live, order, reconciliation, account-read, and automation gates disabled.'
    }
    if ($env:KIS_MARKET_DATA_ENABLED -ne 'true' -or -not $env:KIS_REAL_APP_KEY -or
        -not $env:KIS_REAL_APP_SECRET -or -not $env:POSTGRES_PASSWORD) {
        throw 'Configure KIS market data, REAL app credentials, and PostgreSQL locally. Values are never displayed.'
    }
    if ($env:KIS_SHARED_RATE_LIMIT_ENABLED -eq 'false') {
        throw 'The shared PostgreSQL KIS rate limiter must remain enabled.'
    }

    # Process-only opt-in. The child never receives account or PAPER credentials.
    $env:KIS_ENVIRONMENT = 'paper'
    $env:KIS_REAL_MARKET_DATA_ENABLED = 'true'
    $env:KIS_SHARED_RATE_LIMIT_ENABLED = 'true'
    $env:KIS_REAL_ACCOUNT_NUMBER = ''
    $env:KIS_PAPER_APP_KEY = ''
    $env:KIS_PAPER_APP_SECRET = ''
    $env:KIS_PAPER_ACCOUNT_NUMBER = ''
    $python = Join-Path $repoRoot 'services\market-ai\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Repository Python virtual environment is missing.' }
    $probe = Join-Path $PSScriptRoot 'probe-kis-real-us-quote.py'
    & $python $probe --market $Market --kr-symbol $KoreanSymbol --symbol $Symbol --exchange $Exchange
    if ($LASTEXITCODE -ne 0) { throw 'REAL quote probe failed; inspect its sanitized JSON result.' }
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
