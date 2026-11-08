[CmdletBinding()]
param(
    [switch]$ConfirmPaperLifecycleSmoke,
    [string]$SpringApiBaseUrl = 'http://127.0.0.1:18080',
    [string]$MarketAiApiBaseUrl = 'http://127.0.0.1:18000'
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmPaperLifecycleSmoke) {
    throw 'No request was sent. The one-share PAPER lifecycle smoke requires -ConfirmPaperLifecycleSmoke.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
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

    $quoteUri = "$MarketAiApiBaseUrl/api/v1/market/quotes/US/F?exchange=NASD"
    $quote = Invoke-RestMethod -Uri $quoteUri -TimeoutSec 10
    . (Join-Path $PSScriptRoot 'lib/kis-paper-order-preflight.ps1')
    Assert-KisPaperQuoteFresh -Quote $quote
    if ($quote.market -ne 'US' -or $quote.symbol -ne 'F') {
        throw 'Fresh quote did not match the fixed US PAPER smoke instrument; no order was submitted.'
    }

    $price = [decimal]$quote.price
    . (Join-Path $PSScriptRoot 'lib/us-paper-lifecycle-smoke.ps1')
    $limitPrice = Get-KisUsPaperSmokeLimitPrice -Price $price

    Write-Output 'Starting guarded one-share US KIS PAPER order/cancel lifecycle. Persistent order and automation switches remain disabled.'
    $powershellPath = (Get-Process -Id $PID).Path
    $testScript = Join-Path $PSScriptRoot 'test-kis-paper-order-lifecycle-isolated.ps1'
    $limitText = $limitPrice.ToString([Globalization.CultureInfo]::InvariantCulture)
    & $powershellPath -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $testScript `
        -Market US -Symbol F -LimitPrice $limitText -Exchange NASD `
        -SpringApiBaseUrl $SpringApiBaseUrl -MarketAiApiBaseUrl $MarketAiApiBaseUrl `
        -ConfirmPaperOrderAndCancel
    if ($LASTEXITCODE -ne 0) {
        throw "Guarded PAPER lifecycle ended with exit code $LASTEXITCODE. Review the sanitized local log; do not resubmit automatically."
    }
    Write-Output 'Guarded one-share US PAPER lifecycle completed.'
} finally {
    Stop-Transcript | Out-Null
}
