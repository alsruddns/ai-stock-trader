[CmdletBinding()]
param(
    [switch]$ConfirmPaperLifecycleSmoke,
    [string]$SpringApiBaseUrl = 'http://127.0.0.1:18080',
    [string]$MarketAiApiBaseUrl = 'http://127.0.0.1:18000'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'lib\domestic-only-validation.ps1')
try {
    Assert-DomesticOnlyValidation -Market 'US' -Operation 'KIS PAPER order lifecycle validation'
} catch {
    throw "$_ No KIS request was sent."
}
if (-not $ConfirmPaperLifecycleSmoke) {
    throw 'No request was sent. The one-share PAPER lifecycle smoke requires -ConfirmPaperLifecycleSmoke.'
}

. (Join-Path $PSScriptRoot 'lib/kis-paper-order-preflight.ps1')

function Get-SafeHttpStatus($ErrorRecord) {
    if ($null -ne $ErrorRecord.Exception.Response -and $null -ne $ErrorRecord.Exception.Response.StatusCode) {
        $candidate = [string][int]$ErrorRecord.Exception.Response.StatusCode
        if ($candidate -match '^\d{3}$') { return $candidate }
    }
    return 'unavailable'
}

$envPath = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'The PAPER lifecycle smoke requires the ignored local .env_local file.'
}

$logDirectory = Join-Path $repoRoot '.local/logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logPath = Join-Path $logDirectory "us-paper-lifecycle-smoke-$stamp.log"
Start-Transcript -LiteralPath $logPath -Force | Out-Null

try {
    $operations = Invoke-RestMethod -Uri "$SpringApiBaseUrl/api/v1/operations/summary" -TimeoutSec 10
    if ($operations.tradingMode -ne 'paper' -or $operations.liveTrading -ne 'disabled' -or
        $operations.kisPaperOrdersEnabled -or $operations.paperAutomationEnabled -or
        $operations.kisRealOrdersEnabled -or $operations.kisRealAccountReadsEnabled -or
        $operations.kisRealReconciliationEnabled) {
        throw 'Persistent service is not in the required locked PAPER state; no quote or account request was sent.'
    }

    $smokeSymbol = 'F'
    $smokeOrderExchange = 'NYSE'
    $smokeQuoteExchange = ConvertTo-KisQuoteExchange -Exchange $smokeOrderExchange
    $sessionUri = "$MarketAiApiBaseUrl/api/v1/market/sessions/US"
    try {
        $session = Invoke-RestMethod -Uri $sessionUri -TimeoutSec 10
    } catch {
        $safeFailure = Format-SafeLocalApiFailure -Status (Get-SafeHttpStatus $_) `
            -Path ([Uri]$sessionUri).AbsolutePath -ErrorDetail ([string]$_.ErrorDetails.Message)
        throw "$safeFailure No KIS quote, account, or order request was sent."
    }
    try {
        Assert-KisPaperSessionOpen -Session $session -Market 'US'
    } catch {
        throw "$($_.Exception.Message) No KIS quote, account, or order request was sent."
    }

    $quoteUri = "$MarketAiApiBaseUrl/api/v1/market/quotes/US/$smokeSymbol`?exchange=$smokeQuoteExchange"
    try {
        $quote = Invoke-RestMethod -Uri $quoteUri -TimeoutSec 10
    } catch {
        $safeFailure = Format-SafeLocalApiFailure -Status (Get-SafeHttpStatus $_) `
            -Path ([Uri]$quoteUri).AbsolutePath -ErrorDetail ([string]$_.ErrorDetails.Message)
        throw "$safeFailure No PAPER order was submitted."
    }
    . (Join-Path $PSScriptRoot 'lib/us-paper-lifecycle-smoke.ps1')
    $price = Get-KisUsPaperSmokeQuotePrice -Quote $quote -Symbol $smokeSymbol -Exchange $smokeQuoteExchange
    Assert-KisPaperQuoteFresh -Quote $quote
    $limitPrice = Get-KisUsPaperSmokeLimitPrice -Price $price

    Write-Output 'Starting guarded one-share US KIS PAPER order/cancel lifecycle. Persistent order and automation switches remain disabled.'
    $powershellPath = (Get-Process -Id $PID).Path
    $testScript = Join-Path $PSScriptRoot 'test-kis-paper-order-lifecycle-isolated.ps1'
    $limitText = $limitPrice.ToString([Globalization.CultureInfo]::InvariantCulture)
    & $powershellPath -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $testScript `
        -Market US -Symbol $smokeSymbol -LimitPrice $limitText -Exchange $smokeOrderExchange `
        -SpringApiBaseUrl $SpringApiBaseUrl -MarketAiApiBaseUrl $MarketAiApiBaseUrl `
        -ConfirmPaperOrderAndCancel
    if ($LASTEXITCODE -ne 0) {
        throw "Guarded PAPER lifecycle ended with exit code $LASTEXITCODE. Review the sanitized local log; do not resubmit automatically."
    }
    Write-Output 'Guarded one-share US PAPER lifecycle completed.'
} finally {
    Stop-Transcript | Out-Null
}
