param(
    [Parameter(Mandatory)] [ValidateSet('KR', 'US')] [string]$Market,
    [Parameter(Mandatory)] [string]$Symbol,
    [Parameter(Mandatory)] [decimal]$LimitPrice,
    [string]$Exchange = 'NASD',
    [string]$SpringApiBaseUrl = 'http://127.0.0.1:8080',
    [string]$MarketAiApiBaseUrl = 'http://127.0.0.1:8000',
    [switch]$ConfirmPaperOrderAndCancel
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmPaperOrderAndCancel) {
    throw 'No request was sent. Re-run with -ConfirmPaperOrderAndCancel only when you intend to submit and immediately cancel one KIS PAPER share.'
}
if ($LimitPrice -le 0) { throw 'LimitPrice must be positive.' }

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envPath)) { $envPath = Join-Path $repoRoot '.env' }
if (-not (Test-Path -LiteralPath $envPath)) { throw 'Missing ignored .env_local or .env file.' }

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

if ($env:KIS_ENVIRONMENT -ne 'paper' -or $env:TRADING_MODE -ne 'paper' -or
    $env:LIVE_TRADING_ENABLED -ne 'false' -or $env:KIS_PAPER_ORDERS_ENABLED -ne 'true' -or
    $env:KIS_REAL_ORDERS_ENABLED -ne 'false' -or $env:KIS_REAL_RECONCILIATION_ENABLED -ne 'false' -or
    $env:PAPER_AUTOTRADING_ENABLED -ne 'false') {
    throw 'Requires KIS PAPER mode, PAPER order switch enabled, and live orders, REAL reconciliation, and paper automation disabled.'
}
if ($env:KIS_MARKET_DATA_ENABLED -ne 'true' -or $env:KIS_ACCOUNT_READ_ENABLED -ne 'true' -or
    $env:KIS_PAPER_ACCOUNT_READ_ENABLED -ne 'true') {
    throw 'Read-only quote and PAPER account gates must be enabled for the preflight.'
}
if (-not $env:KIS_PAPER_APP_KEY -or -not $env:KIS_PAPER_APP_SECRET -or
    -not $env:KIS_PAPER_ACCOUNT_NUMBER -or -not $env:POSTGRES_PASSWORD) {
    throw 'PAPER credentials and PostgreSQL password must be configured locally; their values are not displayed.'
}
if (($env:KIS_PAPER_ACCOUNT_NUMBER -replace '-', '') -notmatch '^\d{10}$') {
    throw 'KIS_PAPER_ACCOUNT_NUMBER must contain eight account digits and a two-digit product code.'
}
if ($Market -eq 'KR' -and $Symbol -notmatch '^\d{6}$') { throw 'KR Symbol must contain six digits.' }
if ($Market -eq 'US' -and $Symbol -notmatch '^[A-Za-z][A-Za-z0-9.-]{0,15}$') { throw 'US Symbol is invalid.' }
if ($Market -eq 'US' -and $Exchange -notin @('NAS', 'NASD', 'NYS', 'NYSE', 'AMS', 'AMEX')) {
    throw 'US Exchange must be NAS, NYS, AMS, or their KIS aliases.'
}

function Get-LocalApi([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 15 }
    catch { throw 'Local preflight request failed; inspect the sanitized service logs.' }
}

function Get-Intent([string]$Id) {
    return Get-LocalApi "$SpringApiBaseUrl/api/v1/kis/orders/paper/$Id"
}

$operations = Get-LocalApi "$SpringApiBaseUrl/api/v1/operations/summary"
if ($operations.tradingMode -ne 'paper' -or $operations.liveTrading -ne 'disabled' -or
    -not $operations.kisPaperOrdersEnabled -or $operations.kisRealOrdersEnabled -or
    $operations.kisRealReconciliationEnabled -or $operations.paperAutomationEnabled) {
    throw 'Running Spring service does not report the required PAPER-only order settings.'
}

$normalizedSymbol = $Symbol.ToUpperInvariant()
$quotePath = if ($Market -eq 'KR') { "/api/v1/market/quotes/KR/$normalizedSymbol" }
    else { "/api/v1/market/quotes/US/$normalizedSymbol`?exchange=$Exchange" }
$quote = Get-LocalApi "$MarketAiApiBaseUrl$quotePath"
if ([decimal]$quote.price -le 0 -or $quote.market -ne $Market -or $quote.symbol -ne $normalizedSymbol) {
    throw 'Fresh market quote did not match the requested instrument.'
}
if ($LimitPrice -gt ([decimal]$quote.price * [decimal]0.95)) {
    throw 'LimitPrice must be at least 5% below the latest quote to reduce immediate-fill risk.'
}

$currency = if ($Market -eq 'KR') { 'KRW' } else { 'USD' }
$notional = $LimitPrice
$configuredMaximum = if ($Market -eq 'KR') { [decimal]$env:MAX_ORDER_NOTIONAL_KRW }
    else { [decimal]$env:MAX_ORDER_NOTIONAL_USD }
if ($configuredMaximum -le 0) {
    $configuredMaximum = if ($Market -eq 'KR') { [decimal]1000000 } else { [decimal]100 }
}
$smokeMaximum = if ($Market -eq 'KR') { [decimal]100000 } else { [decimal]100 }
if ($notional -gt $configuredMaximum -or $notional -gt $smokeMaximum) {
    throw 'One-share test order exceeds the configured or hard PAPER smoke notional cap.'
}

$idempotencyKey = [guid]::NewGuid().ToString()
$order = @{
    market = $Market
    symbol = $normalizedSymbol
    side = 'BUY'
    quantity = 1
    limitPrice = $LimitPrice.ToString([Globalization.CultureInfo]::InvariantCulture)
    currency = $currency
    strategyId = 'P6_PAPER_SMOKE'
    modelId = $null
}
$submitUri = if ($Market -eq 'US') {
    "$SpringApiBaseUrl/api/v1/kis/orders/paper`?exchange=$Exchange"
} else { "$SpringApiBaseUrl/api/v1/kis/orders/paper" }

try {
    $intent = Invoke-RestMethod -Method Post -Uri $submitUri `
        -Headers @{ 'Idempotency-Key' = $idempotencyKey } -ContentType 'application/json' `
        -Body ($order | ConvertTo-Json -Compress) -TimeoutSec 20
} catch {
    # A timeout can happen after KIS received the request. Query this key; never submit a replacement.
    try { $intent = Get-Intent $idempotencyKey }
    catch { throw "Submission outcome is unknown. Do not submit again; inspect PAPER order key $idempotencyKey in the local dashboard/KIS paper account." }
}

if ($intent.environment -ne 'paper' -or $intent.idempotencyKey -ne $idempotencyKey) {
    throw "Order response did not match this PAPER request. Inspect order key $idempotencyKey; do not resubmit."
}
if ($intent.status -eq 'REJECTED') {
    $safeResponseCode = if ($intent.responseCode -match '^[A-Za-z0-9_-]{1,32}$') { $intent.responseCode } else { 'unavailable' }
    Write-Output "KIS PAPER rejected the one-share request; no accepted order remains. Code: $safeResponseCode. Key: $idempotencyKey"
    exit 1
}
if ($intent.status -eq 'FILLED') {
    Write-Output "KIS PAPER filled the one-share request before cancellation. Review the simulated position. Key: $idempotencyKey"
    exit 2
}
if ($intent.status -notin @('ACCEPTED', 'PARTIALLY_FILLED', 'CANCEL_PENDING')) {
    throw "Unexpected PAPER order state '$($intent.status)'. Inspect key $idempotencyKey; do not resubmit."
}

if ($intent.status -ne 'CANCEL_PENDING') {
    $cancelUri = if ($Market -eq 'US') {
        "$SpringApiBaseUrl/api/v1/kis/orders/paper/$idempotencyKey/cancel`?exchange=$Exchange"
    } else { "$SpringApiBaseUrl/api/v1/kis/orders/paper/$idempotencyKey/cancel" }
    try {
        $intent = Invoke-RestMethod -Method Post -Uri $cancelUri -TimeoutSec 20
    } catch {
        # Cancellation may have reached KIS despite a lost response. Reconcile the same intent only.
        try { $intent = Invoke-RestMethod -Method Post -Uri "$SpringApiBaseUrl/api/v1/kis/orders/paper/$idempotencyKey/reconcile" -TimeoutSec 20 }
        catch { throw "Cancel outcome is unresolved. Do not resend; inspect PAPER order key $idempotencyKey." }
    }
}

for ($attempt = 0; $attempt -lt 15 -and $intent.status -notin @('CANCELLED', 'FILLED', 'REJECTED', 'UNKNOWN'); $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $intent = Invoke-RestMethod -Method Post -Uri "$SpringApiBaseUrl/api/v1/kis/orders/paper/$idempotencyKey/reconcile" -TimeoutSec 20
    } catch {
        $intent = Get-Intent $idempotencyKey
    }
}

Write-Output "PAPER lifecycle result: market=$Market symbol=$normalizedSymbol quantity=1 status=$($intent.status) key=$idempotencyKey"
if ($intent.status -ne 'CANCELLED') {
    if ($intent.status -eq 'FILLED') { Write-Output 'A simulated fill occurred; review and close the PAPER position if needed.' }
    else { Write-Output 'Broker state is not terminal. Do not resubmit; reconcile this key manually.' }
    exit 2
}
