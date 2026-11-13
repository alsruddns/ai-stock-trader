. (Join-Path $PSScriptRoot '../lib/model-holdout-date-gate.ps1')

function Test-HoldoutDateGateFailure {
    param([Parameter(Mandatory)] [scriptblock]$Action, [Parameter(Mandatory)] [string]$ExpectedMessage)
    try {
        & $Action
        return $false
    } catch {
        return $_.Exception.Message -like $ExpectedMessage
    }
}

Describe 'Independent model holdout date gate' {
    It 'allows holdout sessions strictly after the training cutoff' {
        { Assert-AiTraderHoldoutRangeAfterTraining `
            -TrainingToDate ([datetime]'2026-12-01') `
            -FromDate ([datetime]'2026-12-02') `
            -ToDate ([datetime]'2026-12-08') } | Should Not Throw
    }

    It 'rejects a holdout beginning on the training cutoff date' {
        (Test-HoldoutDateGateFailure -ExpectedMessage '*later than the training cutoff date*' -Action { Assert-AiTraderHoldoutRangeAfterTraining `
            -TrainingToDate ([datetime]'2026-12-01') `
            -FromDate ([datetime]'2026-12-01') `
            -ToDate ([datetime]'2026-12-08') }) | Should Be $true
    }

    It 'rejects a holdout beginning before the training cutoff date' {
        (Test-HoldoutDateGateFailure -ExpectedMessage '*later than the training cutoff date*' -Action { Assert-AiTraderHoldoutRangeAfterTraining `
            -TrainingToDate ([datetime]'2026-12-01') `
            -FromDate ([datetime]'2026-11-30') `
            -ToDate ([datetime]'2026-12-08') }) | Should Be $true
    }

    It 'rejects a reversed holdout interval' {
        (Test-HoldoutDateGateFailure -ExpectedMessage '*FromDate must be on or before ToDate*' -Action { Assert-AiTraderHoldoutRangeAfterTraining `
            -TrainingToDate ([datetime]'2026-11-27') `
            -FromDate ([datetime]'2026-12-08') `
            -ToDate ([datetime]'2026-12-02') }) | Should Be $true
    }

    It 'blocks overlapping task registration before reading local settings' {
        $scriptPath = Join-Path $PSScriptRoot '../register-model-holdout-evaluation-task.ps1'
        (Test-HoldoutDateGateFailure -ExpectedMessage '*later than the training cutoff date*' -Action {
            & $scriptPath -Market KR -Symbol 005930 -TrainingToDate '2026-12-01' `
                -FromDate '2026-12-01' -ToDate '2026-12-08'
        }) | Should Be $true
    }
}
