[CmdletBinding()]
param(
    [string]$SpringApiBaseUrl = 'http://127.0.0.1:18080',
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')][string]$SessionDate = '2026-11-16',
    [ValidatePattern('^\d{2}:\d{2}$')][string]$OrderMinute = '10:14',
    [ValidatePattern('^\d{6}$')][string]$Symbol = '035720'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'lib\paper-no-order-confirmation.ps1')
$evidencePath = Join-Path $repoRoot ".local\validation\paper-no-order-confirmation-$($SessionDate.Replace('-', '')).jsonl"
$result = [ordered]@{
    checkedAt = [DateTimeOffset]::UtcNow.ToString('o')
    market = 'KR'
    symbol = $Symbol
    sessionDate = $SessionDate
    orderMinute = $OrderMinute
    status = 'blocked'
    brokerOrderOrCancelSent = $false
}

try {
    $summary = Invoke-RestMethod -Uri "$SpringApiBaseUrl/api/v1/operations/summary" -TimeoutSec 15
    Assert-PaperNoOrderConfirmationLocks -OperationsSummary $summary
    $candidate = Get-PaperNoOrderConfirmationCandidate -OperationsSummary $summary `
        -SessionDate $SessionDate -Symbol $Symbol -OrderMinute $OrderMinute
    if ($candidate.alreadyConfirmed) {
        $result.status = 'already_confirmed'
    } else {
        $uri = "$SpringApiBaseUrl/api/v1/kis/orders/paper/$($candidate.idempotencyKey)/confirm-no-order"
        $response = Invoke-RestMethod -Method Post -Uri $uri -ContentType 'application/json' `
            -Body '{"confirmedNoOrder":true}' -TimeoutSec 180
        if ($response.status -ne 'NO_ORDER_CONFIRMED') {
            throw 'Spring did not confirm the absent PAPER order.'
        }
        $result.status = 'NO_ORDER_CONFIRMED'
    }
} catch {
    $result.failureType = $_.Exception.GetType().Name
    $result.status = 'blocked'
    $safeFailure = [System.IO.Path]::GetFileName($evidencePath)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $evidencePath) | Out-Null
    [System.IO.File]::AppendAllText($evidencePath, (($result | ConvertTo-Json -Compress) + "`n"), [Text.UTF8Encoding]::new($false))
    throw "No-order confirmation did not complete; UNKNOWN remains protected. Evidence: .local/validation/$safeFailure"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $evidencePath) | Out-Null
[System.IO.File]::AppendAllText($evidencePath, (($result | ConvertTo-Json -Compress) + "`n"), [Text.UTF8Encoding]::new($false))
Write-Output "PAPER no-order confirmation: $($result.status). No broker order or cancel was sent."
