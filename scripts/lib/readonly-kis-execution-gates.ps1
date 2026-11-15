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
