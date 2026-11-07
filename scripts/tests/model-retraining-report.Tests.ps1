. (Join-Path $PSScriptRoot '..\lib\model-retraining-report.ps1')

Describe 'Model retraining failure reports' {
    It 'writes export failures without storing exception text' {
        $directory = Join-Path $TestDrive 'export-failure'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $path = Join-Path $directory 'run.json'
        $exception = [System.InvalidOperationException]::new('secret-bearing connection detail')

        Write-AiTraderModelRetrainingFailureReport -Path $path -RunId 'us-aapl-test' `
            -FailureStage 'data_export' -Market 'US' -Symbol 'AAPL' `
            -FromDate '2026-11-02' -ToDate '2026-11-06' -DatasetPath 'data/model-candidates/test.csv' `
            -ExitCode 2 -Exception $exception

        $json = Get-Content -LiteralPath $path -Raw
        $report = $json | ConvertFrom-Json
        $report.status | Should Be 'export_failed'
        $report.failure_stage | Should Be 'data_export'
        $report.error_type | Should Be 'InvalidOperationException'
        $json | Should Not Match 'secret-bearing connection detail'
    }

    It 'preserves partial candidate identifiers and orphan state without raw output' {
        $directory = Join-Path $TestDrive 'partial-training'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $path = Join-Path $directory 'run.json'

        Write-AiTraderModelRetrainingFailureReport -Path $path -RunId 'kr-005930-test' `
            -FailureStage 'candidate_training' -Market 'KR' -Symbol '005930' `
            -FromDate '2026-11-02' -ToDate '2026-11-06' -DatasetPath 'data/model-candidates/test.csv' `
            -DatasetSha256 ('a' * 64) -ExitCode 1 `
            -CandidateModelIds @('lightgbm-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa') `
            -OrphanCandidateArtifacts @('xgboost-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.joblib')

        $report = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $report.status | Should Be 'training_failed'
        $report.candidate_models_created | Should Be $true
        $report.candidate_model_ids.Count | Should Be 1
        $report.candidates_may_exist_after_partial_failure | Should Be $true
        $report.orphan_candidate_artifacts.Count | Should Be 1
        ($report.PSObject.Properties.Name -join ',') | Should Not Match '(^|,)output(,|$)'
    }

    It 'atomically replaces an existing report and removes temporary files' {
        $directory = Join-Path $TestDrive 'atomic-failure'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $path = Join-Path $directory 'run.json'
        Set-Content -LiteralPath $path -Value '{"version":1}' -Encoding utf8

        Write-AiTraderModelRetrainingFailureReport -Path $path -RunId 'us-aapl-test' `
            -FailureStage 'candidate_verification' -Market 'US' -Symbol 'AAPL' `
            -FromDate '2026-11-02' -ToDate '2026-11-06' -DatasetPath 'data/model-candidates/test.csv'

        $report = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $report.status | Should Be 'candidate_verification_failed'
        @(Get-ChildItem -LiteralPath $directory -File | Where-Object { $_.Name -match '\.tmp(?:\.bak)?$' }).Count | Should Be 0
    }
}
