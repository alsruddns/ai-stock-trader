[CmdletBinding()]
param([switch]$PaperOnly)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envPath)) { $envPath = Join-Path $repoRoot '.env' }
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Missing .env_local or .env. Copy .env.example to .env_local and set your local PostgreSQL password.'
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

if ([string]::IsNullOrWhiteSpace($env:KIS_DOMESTIC_SYMBOL_MASTER_PATH)) {
    $env:KIS_DOMESTIC_SYMBOL_MASTER_PATH = Join-Path $repoRoot '.local\reference-data\kis-domestic-symbols.csv'
}

. (Join-Path $PSScriptRoot 'lib\local-api-binding.ps1')
. (Join-Path $PSScriptRoot 'lib\local-postgres-binding.ps1')

$postgresPort = 5432
if (-not [string]::IsNullOrWhiteSpace($env:POSTGRES_PORT)) {
    $parsedPostgresPort = 0
    if (-not [int]::TryParse($env:POSTGRES_PORT, [ref]$parsedPostgresPort) -or
        $parsedPostgresPort -lt 1 -or $parsedPostgresPort -gt 65535) {
        throw 'POSTGRES_PORT must be an integer between 1 and 65535.'
    }
    $postgresPort = $parsedPostgresPort
}
Assert-LocalPostgresBinding -Port $postgresPort

if ($PaperOnly) {
    $env:TRADING_MODE = 'paper'
    $env:LIVE_TRADING_ENABLED = 'false'
    $env:KIS_REAL_ORDERS_ENABLED = 'false'
    $env:REAL_TRADING_APPROVAL_CONFIRMED = 'false'
}

if (-not $env:POSTGRES_PASSWORD -or $env:POSTGRES_PASSWORD -eq 'replace_with_your_local_postgres_password') {
    throw 'Set POSTGRES_PASSWORD in .env_local (or .env) to the password for your local PostgreSQL user.'
}

Push-Location (Join-Path $repoRoot 'backend/trading-api')
try {
    & .\mvnw.cmd spring-boot:run
    if ($LASTEXITCODE -ne 0) {
        throw "Spring Boot exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
