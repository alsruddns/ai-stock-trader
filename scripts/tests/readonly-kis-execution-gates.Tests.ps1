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
}
