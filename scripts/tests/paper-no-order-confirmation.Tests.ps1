. (Join-Path $PSScriptRoot '..\lib\paper-no-order-confirmation.ps1')

function New-PaperNoOrderSummary {
    param(
        [string]$Status = 'UNKNOWN',
        [string]$IdempotencyKey = 'e81d7466-d98a-4b5c-9c8e-217d9156176c',
        [string]$CreatedAt = '2026-11-16T01:14:17.891928Z',
        [bool]$BrokerOrderReferencePresent = $false,
        [int]$FilledQuantity = 0
    )
    [pscustomobject]@{
        tradingMode = 'paper'
        liveTrading = 'disabled'
        kisPaperOrdersEnabled = $false
        paperAutomationEnabled = $false
        kisRealOrdersEnabled = $false
        kisRealAccountReadsEnabled = $false
        kisRealReconciliationEnabled = $false
        recentPaperKisOrders = @([pscustomobject]@{
            idempotencyKey = $IdempotencyKey
            market = 'KR'
            symbol = '035720'
            side = 'BUY'
            quantity = 1
            filledQuantity = $FilledQuantity
            status = $Status
            brokerOrderReferencePresent = $BrokerOrderReferencePresent
            createdAt = $CreatedAt
        })
    }
}

function Test-ActionThrows {
    param([Parameter(Mandatory)][scriptblock]$Action)
    try {
        & $Action | Out-Null
        return $false
    } catch {
        return $true
    }
}

Describe 'PAPER no-order confirmation guard' {
    It 'accepts only the exact unlinked and unfilled incident intent with all trading locks closed' {
        $summary = New-PaperNoOrderSummary
        (Test-ActionThrows { Assert-PaperNoOrderConfirmationLocks -OperationsSummary $summary }) | Should Be $false
        $candidate = Get-PaperNoOrderConfirmationCandidate -OperationsSummary $summary `
            -SessionDate '2026-11-16' -Symbol '035720' -OrderMinute '10:14'
        $candidate.alreadyConfirmed | Should Be $false
        $candidate.idempotencyKey | Should Be 'e81d7466-d98a-4b5c-9c8e-217d9156176c'
    }

    It 'blocks when PAPER submission or any REAL trading gate is enabled' {
        $summary = New-PaperNoOrderSummary
        $summary.kisPaperOrdersEnabled = $true
        (Test-ActionThrows { Assert-PaperNoOrderConfirmationLocks -OperationsSummary $summary }) | Should Be $true
        $summary.kisPaperOrdersEnabled = $false
        $summary.kisRealOrdersEnabled = $true
        (Test-ActionThrows { Assert-PaperNoOrderConfirmationLocks -OperationsSummary $summary }) | Should Be $true
    }

    It 'blocks a filled, broker-linked, wrong-time, or malformed-key intent' {
        $linked = New-PaperNoOrderSummary -BrokerOrderReferencePresent $true
        (Test-ActionThrows { Get-PaperNoOrderConfirmationCandidate -OperationsSummary $linked -SessionDate '2026-11-16' -Symbol '035720' -OrderMinute '10:14' }) | Should Be $true
        $filled = New-PaperNoOrderSummary -FilledQuantity 1
        (Test-ActionThrows { Get-PaperNoOrderConfirmationCandidate -OperationsSummary $filled -SessionDate '2026-11-16' -Symbol '035720' -OrderMinute '10:14' }) | Should Be $true
        $wrongTime = New-PaperNoOrderSummary -CreatedAt '2026-11-16T01:15:17Z'
        (Test-ActionThrows { Get-PaperNoOrderConfirmationCandidate -OperationsSummary $wrongTime -SessionDate '2026-11-16' -Symbol '035720' -OrderMinute '10:14' }) | Should Be $true
        $badKey = New-PaperNoOrderSummary -IdempotencyKey 'invalid'
        (Test-ActionThrows { Get-PaperNoOrderConfirmationCandidate -OperationsSummary $badKey -SessionDate '2026-11-16' -Symbol '035720' -OrderMinute '10:14' }) | Should Be $true
    }

    It 'treats an already confirmed incident as idempotent' {
        $summary = New-PaperNoOrderSummary -Status 'NO_ORDER_CONFIRMED'
        $candidate = Get-PaperNoOrderConfirmationCandidate -OperationsSummary $summary `
            -SessionDate '2026-11-16' -Symbol '035720' -OrderMinute '10:14'
        $candidate.alreadyConfirmed | Should Be $true
        $candidate.idempotencyKey | Should Be $null
    }

    It 'registers the after-refresh script with a hidden PowerShell window' {
        $repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
        $source = Get-Content -LiteralPath (Join-Path $repositoryRoot 'scripts/register-paper-no-order-confirmation-task.ps1') -Raw
        $source | Should Match '(?s)-WindowStyle Hidden.*-ExecutionPolicy Bypass'
        $source | Should Match 'AIStockTrader-PostSessionRefresh-20261116'
        $source | Should Match 'AIStockTrader-Dashboard-PostSession-20261116'
    }
}
