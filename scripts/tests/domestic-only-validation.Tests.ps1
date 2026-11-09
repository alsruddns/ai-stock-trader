. (Join-Path $PSScriptRoot '../lib/domestic-only-validation.ps1')

function Test-ValidationBlocked {
    param([Parameter(Mandatory)] [ScriptBlock]$Action)
    try { & $Action; return $false } catch { return $true }
}

Describe 'Domestic-only operational validation scope' {
    It 'allows domestic KRX validation' {
        { Assert-DomesticOnlyValidation -Market KR -Operation 'quote validation' } | Should Not Throw
    }

    It 'blocks overseas operational validation' {
        (Test-ValidationBlocked { Assert-DomesticOnlyValidation -Market US -Operation 'quote validation' }) | Should Be $true
    }

    It 'blocks a US KIS PAPER lifecycle before reading local credentials or calling APIs' {
        $scriptPath = Join-Path $PSScriptRoot '../run-us-paper-lifecycle-smoke.ps1'
        (Test-ValidationBlocked { & $scriptPath -ConfirmPaperLifecycleSmoke }) | Should Be $true
    }

    It 'blocks US model training before reading local credentials or exporting data' {
        $scriptPath = Join-Path $PSScriptRoot '../run-model-retraining.ps1'
        (Test-ValidationBlocked { & $scriptPath -Market US -Symbol AAPL -FromDate '2026-11-02' -ToDate '2026-11-06' }) | Should Be $true
    }

    It 'blocks the US monitoring horizon before registering scheduled tasks' {
        $scriptPath = Join-Path $PSScriptRoot '../register-market-data-session-horizon.ps1'
        (Test-ValidationBlocked { & $scriptPath -IncludeUS }) | Should Be $true
    }

    It 'blocks REAL US quote checks before reading local credentials' {
        $scriptPath = Join-Path $PSScriptRoot '../test-real-kis-us-market-data.ps1'
        (Test-ValidationBlocked { & $scriptPath -Market US }) | Should Be $true
    }

    It 'blocks US order lifecycle entry before reading local credentials' {
        $scriptPath = Join-Path $PSScriptRoot '../test-kis-paper-order-lifecycle-isolated.ps1'
        (Test-ValidationBlocked { & $scriptPath -Market US -Symbol AAPL -LimitPrice 10 -PreflightOnly }) | Should Be $true
    }

    It 'blocks US holdout evaluation before exporting database data' {
        $scriptPath = Join-Path $PSScriptRoot '../run-model-holdout-evaluation.ps1'
        (Test-ValidationBlocked { & $scriptPath -Market US -Symbol AAPL -FromDate '2026-11-02' -ToDate '2026-11-06' }) | Should Be $true
    }
}
