$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envFile)) { $envFile = Join-Path $repoRoot '.env' }
if (-not (Test-Path -LiteralPath $envFile)) {
    throw 'Repository .env_local or .env file is missing. Copy .env.example to .env_local and configure local PostgreSQL first.'
}

$originalEnvironment = @{}
foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $originalEnvironment[[string]$entry.Key] = [string]$entry.Value
}

try {
foreach ($line in Get-Content -LiteralPath $envFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
        continue
    }

    $parts = $trimmed.Split('=', 2)
    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}

# Persistence integration tests must never inherit usable KIS credentials or order switches.
$env:KIS_PAPER_APP_KEY = ''
$env:KIS_PAPER_APP_SECRET = ''
$env:KIS_PAPER_ACCOUNT_NUMBER = ''
$env:KIS_REAL_APP_KEY = ''
$env:KIS_REAL_APP_SECRET = ''
$env:KIS_REAL_ACCOUNT_NUMBER = ''
$env:KIS_MARKET_DATA_ENABLED = 'false'
$env:KIS_ACCOUNT_READ_ENABLED = 'false'
$env:KIS_PAPER_ACCOUNT_READ_ENABLED = 'false'
$env:KIS_REAL_ACCOUNT_READ_ENABLED = 'false'
$env:KIS_PAPER_ORDERS_ENABLED = 'false'
$env:KIS_REAL_ORDERS_ENABLED = 'false'
$env:KIS_REAL_RECONCILIATION_ENABLED = 'false'
$env:TRADING_MODE = 'paper'
$env:LIVE_TRADING_ENABLED = 'false'

$env:RUN_LOCAL_POSTGRES_INTEGRATION_TESTS = 'true'
$apiDirectory = Join-Path $repoRoot 'backend\trading-api'
Push-Location $apiDirectory
try {
    & .\mvnw.cmd '-Dtest=KisOrderIntentPostgresIntegrationTest,KisRestRateLimiterPostgresIntegrationTest,KisRealDailyRiskBaselinePostgresIntegrationTest,KisRealFillSnapshotPostgresIntegrationTest,KisRealSessionPositionBaselinePostgresIntegrationTest' test
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL integration tests failed with exit code $LASTEXITCODE."
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
