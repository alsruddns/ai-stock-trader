$script:ReadOnlyKisExecutionGateNames = @(
    'LIVE_TRADING_ENABLED',
    'KIS_PAPER_ORDERS_ENABLED',
    'KIS_REAL_ORDERS_ENABLED',
    'REAL_TRADING_APPROVAL_CONFIRMED',
    'PAPER_AUTOTRADING_ENABLED',
    'REAL_SIGNAL_MONITOR_ENABLED',
    'REAL_SIGNAL_SUBMISSION_ENABLED',
    'REAL_PROTECTIVE_EXIT_MANAGER_ENABLED',
    'KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED',
    'KIS_REAL_RECONCILIATION_ENABLED',
    'KIS_REAL_EXECUTION_NOTICES_ENABLED',
    'KIS_PAPER_RECONCILIATION_ENABLED'
)

function Assert-KisReadOnlyExecutionGatesDisabled {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [System.Collections.IDictionary]$Values
    )

    foreach ($name in $script:ReadOnlyKisExecutionGateNames) {
        $value = [string]$Values[$name]
        if (-not [string]::IsNullOrWhiteSpace($value) -and
            -not $value.Trim().Equals('false', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Read-only KIS account test requires all order and automation gates to be disabled.'
        }
    }
}

function Get-KisReadOnlyAccountTestOverrides {
    [CmdletBinding()]
    param(
        [string]$TestMethod,
        [string]$ConfiguredOrderableUnitPrice
    )

    $overrides = @{ KIS_TEST_REAL_ORDERABLE_READS = 'false' }
    if ($TestMethod -ne 'readsDomesticRealOrderableCashForSymbolWithoutPlacingOrders') {
        return $overrides
    }

    $unitPrice = $ConfiguredOrderableUnitPrice
    if ([string]::IsNullOrWhiteSpace($unitPrice)) {
        $unitPrice = '70000'
    }
    if ($unitPrice -notmatch '^[1-9][0-9]{0,8}$') {
        throw 'The read-only domestic orderable-cash test requires a positive integer query price.'
    }

    $overrides.KIS_TEST_REAL_ORDERABLE_READS = 'true'
    $overrides.KIS_REAL_ACCOUNT_ORDERABLE_TEST_SYMBOL = '005930'
    $overrides.KIS_REAL_ACCOUNT_ORDERABLE_TEST_UNIT_PRICE = $unitPrice
    return $overrides
}
