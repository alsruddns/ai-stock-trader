param(
    [string]$ApiBaseUrl = 'http://127.0.0.1:18000'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envPath)) { $envPath = Join-Path $repoRoot '.env' }
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Missing local .env_local or .env file.'
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

$paperOrdersLocked = [string]::IsNullOrWhiteSpace($env:KIS_PAPER_ORDERS_ENABLED) -or
    $env:KIS_PAPER_ORDERS_ENABLED -eq 'false'
$paperAutomationLocked = [string]::IsNullOrWhiteSpace($env:PAPER_AUTOTRADING_ENABLED) -or
    $env:PAPER_AUTOTRADING_ENABLED -eq 'false'
if ($env:KIS_ENVIRONMENT -ne 'paper' -or
    $env:TRADING_MODE -ne 'paper' -or
    $env:LIVE_TRADING_ENABLED -ne 'false' -or
    -not $paperOrdersLocked -or
    $env:KIS_REAL_ORDERS_ENABLED -ne 'false' -or
    $env:KIS_REAL_RECONCILIATION_ENABLED -ne 'false' -or
    -not $paperAutomationLocked) {
    throw 'Python KIS smoke test requires PAPER mode with live, automated, and broker order switches disabled.'
}
if ($env:KIS_MARKET_DATA_ENABLED -ne 'true' -or
    $env:KIS_ACCOUNT_READ_ENABLED -ne 'true' -or
    $env:KIS_PAPER_ACCOUNT_READ_ENABLED -ne 'true') {
    throw 'Enable PAPER KIS REST quotes and both read-only account gates in .env_local before running this smoke test.'
}
if (-not $env:KIS_PAPER_APP_KEY -or -not $env:KIS_PAPER_APP_SECRET -or
    -not $env:KIS_PAPER_ACCOUNT_NUMBER) {
    throw 'Configure the PAPER KIS key, secret, and account number in .env_local. Values are never displayed.'
}
$accountNumber = $env:KIS_PAPER_ACCOUNT_NUMBER -replace '-', ''
if ($accountNumber -notmatch '^\d{10}$') {
    throw 'KIS_PAPER_ACCOUNT_NUMBER must contain an eight-digit account number and two-digit product code.'
}

function Invoke-LocalReadOnlyGet {
    param([Parameter(Mandatory)] [string]$Path)
    try {
        return Invoke-RestMethod -Uri "$ApiBaseUrl$Path" -TimeoutSec 30
    } catch {
        throw "Read-only Python KIS smoke failed at '$Path'. Inspect the sanitized local service log."
    }
}

function Assert-Quote {
    param([Parameter(Mandatory)] $Quote, [Parameter(Mandatory)] [string]$Market,
          [Parameter(Mandatory)] [string]$Symbol)
    if ($Quote.market -ne $Market -or $Quote.symbol -ne $Symbol -or
        [decimal]$Quote.price -le 0 -or -not $Quote.retrieved_at -or
        $Quote.source -ne 'KIS_REST_SNAPSHOT') {
        throw "Python KIS $Market quote response did not match the expected read-only contract."
    }
}

$health = Invoke-LocalReadOnlyGet '/api/v1/health'
if ($health.status -ne 'ok' -or
    -not $health.kis_market_data_enabled -or
    $health.live_trading_enabled -or
    $health.paper_automation_enabled) {
    throw 'Python API is not ready for a locked PAPER read-only KIS smoke test.'
}

$krQuote = Invoke-LocalReadOnlyGet '/api/v1/market/quotes/KR/005930'
$usQuote = Invoke-LocalReadOnlyGet '/api/v1/market/quotes/US/AAPL?exchange=NAS'
$krBalance = Invoke-LocalReadOnlyGet '/api/v1/accounts/paper/KR/balance'
$usBalance = Invoke-LocalReadOnlyGet '/api/v1/accounts/paper/US/balance?exchange=NASD'
Assert-Quote $krQuote 'KR' '005930'
Assert-Quote $usQuote 'US' 'AAPL'
if ($krBalance.environment -ne 'paper' -or $krBalance.market -ne 'KR' -or
    $usBalance.environment -ne 'paper' -or $usBalance.market -ne 'US' -or
    $null -eq $krBalance.positions -or $null -eq $usBalance.positions) {
    throw 'Python KIS account responses did not match the expected PAPER read-only contract.'
}

$responsesJson = @($krQuote, $usQuote, $krBalance, $usBalance) | ConvertTo-Json -Depth 12 -Compress
if ($responsesJson.Contains($env:KIS_PAPER_APP_KEY) -or
    $responsesJson.Contains($env:KIS_PAPER_APP_SECRET) -or
    $responsesJson.Contains($accountNumber)) {
    throw 'Python KIS API response unexpectedly included local credential data.'
}

Write-Output 'Python KIS PAPER read-only smoke passed: KR/US REST quotes and account balances.'
