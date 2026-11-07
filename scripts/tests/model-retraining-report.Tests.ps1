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

    It 'classifies known training failures without retaining traceback or arbitrary output' {
        $directory = Join-Path $TestDrive 'classified-training-failure'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $path = Join-Path $directory 'run.json'
        $rawOutput = @(
            'Traceback (most recent call last):',
            'ValueError: at least three distinct sessions are required for train/validation/test splits',
            'private-path-or-secret-marker'
        )
        $failureCode = Get-AiTraderModelTrainingFailureCode -Output $rawOutput

        Write-AiTraderModelRetrainingFailureReport -Path $path -RunId 'us-aapl-test' `
            -FailureStage 'candidate_training' -Market 'US' -Symbol 'AAPL' `
            -FromDate '2026-11-02' -ToDate '2026-11-06' -DatasetPath 'data/model-candidates/test.csv' `
            -ExitCode 2 -FailureCode $failureCode

        $json = Get-Content -LiteralPath $path -Raw
        $report = $json | ConvertFrom-Json
        $report.failure_code | Should Be 'insufficient_training_sessions'
        $json | Should Not Match 'Traceback|private-path-or-secret-marker|at least three distinct sessions'
    }

    It 'maps unknown training errors to a generic safe code' {
        Get-AiTraderModelTrainingFailureCode -Output @('secret-bearing database host detail') |
            Should Be 'unclassified_training_failure'
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

Describe 'Model retraining operations summary' {
    It 'reports no prior runs without treating it as a failed run' {
        $directory = Join-Path $TestDrive 'no-retraining-runs'

        $summary = Get-AiTraderModelRetrainingSummary -Directory $directory

        $summary.state | Should Be 'NO_RUNS'
        $summary.latestRun | Should Be $null
    }

    It 'summarizes an unapproved candidate without copying arbitrary report fields' {
        $directory = Join-Path $TestDrive 'candidate-summary'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $path = Join-Path $directory 'latest.json'
        $document = [ordered]@{
            run_id = 'us-aapl-20261107-aaaaaaaaaaaa'
            status = 'candidate_created_review_required'
            market = 'US'
            symbol = 'AAPL'
            from_date = '2026-11-02'
            to_date = '2026-11-06'
            dataset_path = 'secret-local-path'
            secret = 'secret-field-value'
            candidates = @(@{ model_id = 'lightgbm-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'; approved = $false })
        }
        Set-AiTraderAtomicJsonFile -Path $path -Value $document

        $summary = Get-AiTraderModelRetrainingSummary -Directory $directory
        $serialized = $summary | ConvertTo-Json -Depth 6 -Compress

        $summary.state | Should Be 'AVAILABLE'
        $summary.latestRun.status | Should Be 'candidate_created_review_required'
        $summary.latestRun.candidateModelCount | Should Be 1
        $summary.latestRun.allCandidatesUnapproved | Should Be $true
        $serialized | Should Not Match 'secret-local-path|secret-field-value'
    }

    It 'summarizes a failed run from sanitized fields only' {
        $directory = Join-Path $TestDrive 'failure-summary'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $path = Join-Path $directory 'latest.json'
        Write-AiTraderModelRetrainingFailureReport -Path $path -RunId 'kr-005930-20261106-cccccccccccc' `
            -FailureStage 'candidate_training' -Market 'KR' -Symbol '005930' `
            -FromDate '2026-11-02' -ToDate '2026-11-06' -DatasetPath 'data/model-candidates/test.csv' `
            -Exception ([System.InvalidOperationException]::new('secret-bearing database detail')) `
            -FailureCode 'insufficient_training_sessions'

        $summary = Get-AiTraderModelRetrainingSummary -Directory $directory
        $serialized = $summary | ConvertTo-Json -Depth 6 -Compress

        $summary.state | Should Be 'AVAILABLE'
        $summary.latestRun.status | Should Be 'training_failed'
        $summary.latestRun.failureStage | Should Be 'candidate_training'
        $summary.latestRun.failureCode | Should Be 'insufficient_training_sessions'
        $summary.latestRun.errorType | Should Be 'InvalidOperationException'
        $serialized | Should Not Match 'secret-bearing database detail'
    }

    It 'classifies a legacy traceback without returning its raw contents' {
        $directory = Join-Path $TestDrive 'legacy-failure-summary'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $path = Join-Path $directory 'legacy.json'
        $legacy = [ordered]@{
            run_id = 'us-aapl-20261108-aaaaaaaaaaaa'
            status = 'training_failed'
            market = 'US'
            symbol = 'AAPL'
            from_date = '2026-11-02'
            to_date = '2026-11-06'
            candidate_model_ids = @()
            orphan_candidate_artifacts = @()
            output = "Traceback (most recent call last):`nValueError: at least three distinct sessions are required`nsecret-marker"
        }
        Set-AiTraderAtomicJsonFile -Path $path -Value $legacy

        $summary = Get-AiTraderModelRetrainingSummary -Directory $directory
        $serialized = $summary | ConvertTo-Json -Depth 6 -Compress

        $summary.latestRun.failureCode | Should Be 'insufficient_training_sessions'
        $serialized | Should Not Match 'Traceback|secret-marker|at least three distinct sessions'
    }

    It 'does not hide a corrupt newest report behind an older valid one' {
        $directory = Join-Path $TestDrive 'corrupt-latest'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $validPath = Join-Path $directory 'older.json'
        $corruptPath = Join-Path $directory 'newest.json'
        Write-AiTraderModelRetrainingFailureReport -Path $validPath -RunId 'us-aapl-20261106-dddddddddddd' `
            -FailureStage 'data_export' -Market 'US' -Symbol 'AAPL' `
            -FromDate '2026-11-02' -ToDate '2026-11-05' -DatasetPath 'data/model-candidates/older.csv'
        Set-Content -LiteralPath $corruptPath -Value '{not-json' -Encoding utf8
        [IO.File]::SetLastWriteTimeUtc($corruptPath, [DateTime]::UtcNow.AddMinutes(1))

        $summary = Get-AiTraderModelRetrainingSummary -Directory $directory

        $summary.state | Should Be 'INVALID_REPORT'
        $summary.latestRun | Should Be $null
    }
}
