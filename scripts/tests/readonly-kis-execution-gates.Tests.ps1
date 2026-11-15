. (Join-Path $PSScriptRoot '../lib/readonly-kis-execution-gates.ps1')

Describe 'Read-only KIS execution gate validation' {
    It 'allows missing execution gates because the application defaults them off' {
        Assert-KisReadOnlyExecutionGatesDisabled -Values @{}
    }

    It 'allows explicit false values for every order and automation gate' {
        $values = @{}
        foreach ($name in $script:ReadOnlyKisExecutionGateNames) {
            $values[$name] = 'false'
        }
        Assert-KisReadOnlyExecutionGatesDisabled -Values $values
    }

    It 'rejects every enabled order or automation gate' {
        foreach ($name in $script:ReadOnlyKisExecutionGateNames) {
            $values = @{}
            $values[$name] = 'true'
            { Assert-KisReadOnlyExecutionGatesDisabled -Values $values } | Should Throw
        }
    }

    It 'covers order and automation switches declared by the environment template' {
        $repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
        $templateVariables = @(Get-Content -LiteralPath (Join-Path $repositoryRoot '.env.example') | ForEach-Object {
            if ($_ -match '^\s*([A-Z][A-Z0-9_]+)=') { $Matches[1] }
        })
        $expected = @($templateVariables | Where-Object {
            $_ -match '^(LIVE_TRADING_ENABLED|KIS_PAPER_ORDERS_ENABLED|KIS_REAL_ORDERS_ENABLED|REAL_TRADING_APPROVAL_CONFIRMED|PAPER_AUTOTRADING_ENABLED|REAL_SIGNAL_SUBMISSION_ENABLED|REAL_PROTECTIVE_EXIT_MANAGER_ENABLED|KIS_REAL_PROTECTIVE_EXIT_AUTOMATION_ENABLED|KIS_REAL_RECONCILIATION_ENABLED|KIS_REAL_EXECUTION_NOTICES_ENABLED|KIS_PAPER_RECONCILIATION_ENABLED)$'
        })
        $expected += 'REAL_SIGNAL_MONITOR_ENABLED'
        $missing = @($expected | Where-Object { $script:ReadOnlyKisExecutionGateNames -notcontains $_ })

        $missing.Count | Should Be 0
    }
}
