param(
    [Parameter(Mandatory)] [ValidateSet('KR', 'US')] [string]$Market,
    [Parameter(Mandatory)] [string]$Symbol,
    [Parameter(Mandatory)] [decimal]$LimitPrice,
    [string]$Exchange = 'NASD',
    [string]$SpringApiBaseUrl = 'http://127.0.0.1:18080',
    [string]$MarketAiApiBaseUrl = 'http://127.0.0.1:18000',
    [switch]$PreflightOnly,
    [switch]$EnablePaperOrdersForThisRun,
    [switch]$ConfirmPaperOrderAndCancel,
    [switch]$ConfirmPaperOrderSubmitAndObserve
)

$originalEnvironment = @{}
$preflightEvidencePath = $null
$preflightEvidenceStage = 'Configuration'
$quoteFreshnessCheckPassed = $null
$orderableFundsCheckPassed = $null
foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $originalEnvironment[[string]$entry.Key] = [string]$entry.Value
}

try {
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\domestic-only-validation.ps1')
Assert-DomesticOnlyValidation -Market $Market -Operation 'KIS PAPER order lifecycle validation'
if (@([bool]$PreflightOnly, [bool]$ConfirmPaperOrderAndCancel, [bool]$ConfirmPaperOrderSubmitAndObserve | Where-Object { $_ }).Count -ne 1) {
    throw 'Choose exactly one mode: -PreflightOnly, -ConfirmPaperOrderAndCancel, or -ConfirmPaperOrderSubmitAndObserve.'
}
if (-not $PreflightOnly -and -not $ConfirmPaperOrderAndCancel -and -not $ConfirmPaperOrderSubmitAndObserve) {
    throw 'No request was sent. Choose an explicit PAPER validation mode.'
}
if ($LimitPrice -le 0) { throw 'LimitPrice must be positive.' }
if ($Market -eq 'KR' -and $ConfirmPaperOrderAndCancel) {
    throw 'Domestic PAPER cancel preflight is unsupported; no order was submitted. Use -PreflightOnly or wait until KIS documents a PAPER cancel-quantity query.'
}
if ($ConfirmPaperOrderSubmitAndObserve -and -not $EnablePaperOrdersForThisRun -and $env:KIS_PAPER_ORDERS_ENABLED -ne 'true') {
    throw 'PAPER order submission requires an explicit one-run enable switch or a PAPER-orders-enabled isolated service.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'lib/kis-paper-order-preflight.ps1')
. (Join-Path $PSScriptRoot 'lib/kis-paper-order-lifecycle.ps1')
$preflightEvidencePath = Join-Path $repoRoot '.local/validation/paper-preflight-latest.json'
$preflightEvidenceStage = 'Configuration'
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

if ($EnablePaperOrdersForThisRun) {
    $env:KIS_PAPER_ORDERS_ENABLED = 'true'
    $env:KIS_REAL_APP_KEY = ''
    $env:KIS_REAL_APP_SECRET = ''
    $env:KIS_REAL_ACCOUNT_NUMBER = ''
    $env:KIS_REAL_ACCOUNT_READ_ENABLED = 'false'
}

if ($env:KIS_ENVIRONMENT -ne 'paper' -or $env:TRADING_MODE -ne 'paper' -or
    $env:LIVE_TRADING_ENABLED -ne 'false' -or
    ($PreflightOnly -and -not $EnablePaperOrdersForThisRun -and $env:KIS_PAPER_ORDERS_ENABLED -ne 'false') -or
    (($ConfirmPaperOrderAndCancel -or $ConfirmPaperOrderSubmitAndObserve -or $EnablePaperOrdersForThisRun) -and $env:KIS_PAPER_ORDERS_ENABLED -ne 'true') -or
    $env:KIS_REAL_ORDERS_ENABLED -ne 'false' -or $env:KIS_REAL_RECONCILIATION_ENABLED -ne 'false' -or
    $env:PAPER_AUTOTRADING_ENABLED -ne 'false') {
    throw 'Requires PAPER mode, the matching PAPER order switch, and live orders, REAL reconciliation, and paper automation disabled.'
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

function Get-LocalApi([string]$Url, [int]$TimeoutSec = 15) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec }
    catch {
        $status = 'unavailable'
        if ($null -ne $_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
            $status = [string][int]$_.Exception.Response.StatusCode
        }
        $safePath = ([Uri]$Url).AbsolutePath
        throw (Format-SafeLocalApiFailure -Status $status -Path $safePath -ErrorDetail ([string]$_.ErrorDetails.Message))
    }
}

function Get-Intent([string]$Id) {
    return Get-LocalApi "$SpringApiBaseUrl/api/v1/kis/orders/paper/$Id"
}

$preflightEvidenceStage = 'LocalServiceState'
$operations = Get-LocalApi "$SpringApiBaseUrl/api/v1/operations/summary"
$expectedPaperOrdersEnabled = $env:KIS_PAPER_ORDERS_ENABLED -eq 'true'
if ($operations.tradingMode -ne 'paper' -or $operations.liveTrading -ne 'disabled' -or
    ([bool]$operations.kisPaperOrdersEnabled -ne $expectedPaperOrdersEnabled) -or $operations.kisRealOrdersEnabled -or
    $operations.kisRealReconciliationEnabled -or $operations.paperAutomationEnabled) {
    throw 'Running Spring service does not report the required PAPER-only settings for this mode.'
}

$normalizedSymbol = $Symbol.ToUpperInvariant()
$preflightEvidenceStage = 'Quote'
$quotePath = if ($Market -eq 'KR') { "/api/v1/market/quotes/KR/$normalizedSymbol" }
    else { "/api/v1/market/quotes/US/$normalizedSymbol`?exchange=$(ConvertTo-KisQuoteExchange -Exchange $Exchange)" }
$quote = Get-LocalApi "$MarketAiApiBaseUrl$quotePath"
if ([decimal]$quote.price -le 0 -or $quote.market -ne $Market -or $quote.symbol -ne $normalizedSymbol) {
    throw 'Fresh market quote did not match the requested instrument.'
}
$preflightEvidenceStage = 'QuoteValidation'
Assert-KisPaperQuoteFresh -Quote $quote
$quoteFreshnessCheckPassed = $true
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

$escapedSymbol = [Uri]::EscapeDataString($normalizedSymbol)
$escapedPrice = [Uri]::EscapeDataString($LimitPrice.ToString([Globalization.CultureInfo]::InvariantCulture))
$escapedExchange = [Uri]::EscapeDataString($Exchange)
$accountPath = "/api/v1/kis/accounts/paper/$Market/balance`?symbol=$escapedSymbol`&unitPrice=$escapedPrice`&exchange=$escapedExchange"
# KIS token issuance is paced across Spring and Python by a shared one-per-minute slot.
# This read-only call can wait for that slot before the broker request starts.
$preflightEvidenceStage = 'Account'
$account = Get-LocalApi "$SpringApiBaseUrl$accountPath" -TimeoutSec 180
$preflightEvidenceStage = 'OrderableFunds'
Assert-KisPaperOrderableFunds -Account $account -Market $Market -Currency $currency -MinimumNotional $notional
$orderableFundsCheckPassed = $true

if ($PreflightOnly) {
    Write-KisPaperPreflightEvidence -Path $preflightEvidencePath -Market $Market -Symbol $normalizedSymbol `
        -Currency $currency -Result PASSED -Stage Complete `
        -QuoteFreshnessCheckPassed $quoteFreshnessCheckPassed -OrderableFundsCheckPassed $orderableFundsCheckPassed
    Write-Output "Read-only PAPER preflight passed for market=$Market symbol=$normalizedSymbol; quote and orderable funds are fresh and sufficient. No order was sent. Evidence: .local/validation/paper-preflight-latest.json"
    exit 0
}

$marketSession = Get-LocalApi "$MarketAiApiBaseUrl/api/v1/market/sessions/$Market"
Assert-KisPaperSessionOpen -Session $marketSession -Market $Market

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

if ($ConfirmPaperOrderSubmitAndObserve) {
    for ($attempt = 0; $attempt -lt 5 -and $intent.status -in @('ACCEPTED', 'PARTIALLY_FILLED'); $attempt++) {
        Start-Sleep -Seconds 1
        try {
            $intent = Invoke-RestMethod -Method Post -Uri "$SpringApiBaseUrl/api/v1/kis/orders/paper/$idempotencyKey/reconcile" -TimeoutSec 20
        } catch {
            # Reconcile the existing idempotency key only; never submit a replacement.
            $intent = Get-Intent $idempotencyKey
        }
    }
    if ($intent.environment -ne 'paper' -or $intent.idempotencyKey -ne $idempotencyKey) {
        throw "Reconciliation response did not match this PAPER request. Inspect order key $idempotencyKey; do not resubmit."
    }
    $observation = Get-KisPaperOrderSubmitObservationResult -Intent $intent
    Write-Output "PAPER submit observation: market=$Market symbol=$normalizedSymbol status=$($intent.status) filled=$($observation.filledQuantity) order_remains_open=$($observation.orderRemainsOpen) key=$idempotencyKey"
    Write-Output $observation.message
    if ($observation.exitCode -ne 0) { exit $observation.exitCode }
    exit 0
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

$lifecycleResult = Get-KisPaperOrderLifecycleResult -Intent $intent
Write-Output "PAPER lifecycle result: market=$Market symbol=$normalizedSymbol quantity=1 status=$($intent.status) filled=$($lifecycleResult.filledQuantity) key=$idempotencyKey"
Write-Output $lifecycleResult.message
if ($lifecycleResult.exitCode -ne 0) { exit $lifecycleResult.exitCode }
} catch {
    if ($PreflightOnly -and $preflightEvidencePath -and
        (Get-Command -Name Write-KisPaperPreflightEvidence -ErrorAction SilentlyContinue)) {
        try {
            Write-KisPaperPreflightEvidence -Path $preflightEvidencePath -Market $Market -Symbol $Symbol `
                -Currency $(if ($Market -eq 'KR') { 'KRW' } else { 'USD' }) -Result FAILED `
                -Stage $preflightEvidenceStage -QuoteFreshnessCheckPassed $quoteFreshnessCheckPassed `
                -OrderableFundsCheckPassed $orderableFundsCheckPassed
        } catch {
            # Evidence failure must not replace the sanitized preflight failure.
        }
    }
    throw
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
