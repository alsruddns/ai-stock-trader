. (Join-Path $PSScriptRoot 'operations-report.ps1')

function ConvertTo-AiTraderModelMetricsReport {
    param([Parameter(Mandatory)][object]$Metrics)

    $names = @(
        'accuracy', 'brier', 'roc_auc', 'positive_label_rate', 'predicted_positive_rate',
        'positive_precision', 'positive_recall', 'specificity', 'balanced_accuracy',
        'candidate_count', 'candidate_coverage', 'candidate_positive_rate',
        'candidate_mean_forward_net_bps'
    )
    $result = [ordered]@{}
    foreach ($name in $names) {
        $property = $Metrics.PSObject.Properties[$name]
        if ($null -eq $property) {
            throw "Candidate metrics are missing the expected '$name' value."
        }
        $value = $property.Value
        if ($null -ne $value -and ($value -is [bool] -or $value -isnot [ValueType] -or
            $value -is [datetime] -or $value -is [guid])) {
            throw "Candidate metric '$name' must be a finite number or null."
        }
        if ($value -is [double] -and ([double]::IsNaN($value) -or [double]::IsInfinity($value))) {
            throw "Candidate metric '$name' must be a finite number or null."
        }
        if ($value -is [single] -and ([single]::IsNaN($value) -or [single]::IsInfinity($value))) {
            throw "Candidate metric '$name' must be a finite number or null."
        }
        $result[$name] = $value
    }
    return $result
}

function New-AiTraderModelCandidateReport {
    param([Parameter(Mandatory)][object]$Candidate)

    return [ordered]@{
        model_id = [string]$Candidate.model_id
        model_type = [string]$Candidate.model_type
        artifact_sha256 = [string]$Candidate.artifact_sha256
        approved = $false
        test_sessions = @($Candidate.test_sessions)
        validation_metrics = ConvertTo-AiTraderModelMetricsReport -Metrics $Candidate.validation_metrics
        test_metrics = ConvertTo-AiTraderModelMetricsReport -Metrics $Candidate.test_metrics
        quant_baseline_test_metrics = $Candidate.quant_baseline_test_metrics
        ai_quant_backtest_test_metrics = $Candidate.ai_quant_backtest_test_metrics
        quant_backtest_test_metrics = $Candidate.quant_backtest_test_metrics
    }
}

function Get-AiTraderModelTrainingFailureCode {
    param([string[]]$Output)

    # Map only known, non-sensitive contract failures. Never persist arbitrary stderr/tracebacks.
    $text = ($Output -join "`n")
    if ($text -match 'at least three distinct sessions are required') {
        return 'insufficient_training_sessions'
    }
    if ($text -match 'insufficient samples per chronological partition') {
        return 'insufficient_partition_samples'
    }
    if ($text -match 'training partition must contain both outcome classes') {
        return 'single_class_training_partition'
    }
    if ($text -match 'Install the optional ai dependencies to train LightGBM and XGBoost models') {
        return 'training_dependencies_missing'
    }
    return 'unclassified_training_failure'
}

function Write-AiTraderModelRetrainingFailureReport {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$RunId,
        [Parameter(Mandatory)][ValidateSet('configuration', 'data_export', 'candidate_training', 'candidate_verification')][string]$FailureStage,
        [Parameter(Mandatory)][string]$Market,
        [Parameter(Mandatory)][string]$Symbol,
        [Parameter(Mandatory)][string]$FromDate,
        [Parameter(Mandatory)][string]$ToDate,
        [Parameter(Mandatory)][string]$DatasetPath,
        [string]$DatasetSha256,
        [Nullable[int]]$ExitCode,
        [System.Exception]$Exception,
        [string]$FailureCode,
        [string[]]$CandidateModelIds = @(),
        [string[]]$OrphanCandidateArtifacts = @()
    )

    $status = switch ($FailureStage) {
        'configuration' { 'configuration_failed' }
        'data_export' { 'export_failed' }
        'candidate_training' { 'training_failed' }
        'candidate_verification' { 'candidate_verification_failed' }
    }
    $errorType = if ($null -ne $Exception) { $Exception.GetType().Name } else { $null }
    $safeFailureCodes = @(
        'local_configuration_missing', 'market_cost_assumptions_missing_or_invalid',
        'market_cost_assumptions_inconsistent',
        'insufficient_training_sessions', 'insufficient_partition_samples',
        'single_class_training_partition', 'training_dependencies_missing',
        'unclassified_training_failure'
    )
    $safeFailureCode = if ($FailureCode -in $safeFailureCodes) { $FailureCode } else { $null }
    $report = [ordered]@{
        run_id = $RunId
        status = $status
        failure_stage = $FailureStage
        failed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
        market = $Market
        symbol = $Symbol
        from_date = $FromDate
        to_date = $ToDate
        dataset_path = $DatasetPath
        dataset_sha256 = $DatasetSha256
        exit_code = $ExitCode
        error_type = $errorType
        failure_code = $safeFailureCode
        candidate_models_created = ($CandidateModelIds.Count -gt 0)
        candidate_model_ids = @($CandidateModelIds)
        orphan_candidate_artifacts = @($OrphanCandidateArtifacts)
        candidates_may_exist_after_partial_failure = ($OrphanCandidateArtifacts.Count -gt 0)
    }
    Set-AiTraderAtomicJsonFile -Path $Path -Value $report -Depth 8
}

function Get-AiTraderModelRetrainingSummary {
    param([Parameter(Mandatory)][string]$Directory)

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return [pscustomobject]@{ state = 'NO_RUNS'; latestRun = $null }
    }
    $latestFile = Get-ChildItem -LiteralPath $Directory -Filter '*.json' -File -ErrorAction SilentlyContinue |
        Sort-Object -Property LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($null -eq $latestFile) {
        return [pscustomobject]@{ state = 'NO_RUNS'; latestRun = $null }
    }
    try {
        $document = Get-Content -LiteralPath $latestFile.FullName -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return [pscustomobject]@{ state = 'INVALID_REPORT'; latestRun = $null }
    }
    $validStatuses = @('candidate_created_review_required', 'configuration_failed', 'export_failed', 'training_failed', 'candidate_verification_failed')
    if ($null -eq $document -or $document.status -notin $validStatuses -or
        $document.run_id -notmatch '^(kr|us)-[a-z0-9.-]+-\d{8}-[a-f0-9]{12}$' -or
        $document.market -notin @('KR', 'US') -or [string]::IsNullOrWhiteSpace([string]$document.symbol)) {
        return [pscustomobject]@{ state = 'INVALID_REPORT'; latestRun = $null }
    }

    $candidateCount = 0
    $allCandidatesUnapproved = $null
    $orphanArtifactCount = 0
    if ($document.status -eq 'candidate_created_review_required') {
        $candidates = @($document.candidates)
        if ($candidates.Count -eq 0 -or @($candidates | Where-Object {
            $_.model_id -notmatch '^(lightgbm|xgboost)-[a-f0-9]{32}$' -or $_.approved -ne $false
        }).Count -gt 0) {
            return [pscustomobject]@{ state = 'INVALID_REPORT'; latestRun = $null }
        }
        $candidateCount = $candidates.Count
        $allCandidatesUnapproved = $true
    } else {
        $candidateIds = @($document.candidate_model_ids)
        if (@($candidateIds | Where-Object { $_ -notmatch '^(lightgbm|xgboost)-[a-f0-9]{32}$' }).Count -gt 0) {
            return [pscustomobject]@{ state = 'INVALID_REPORT'; latestRun = $null }
        }
        $candidateCount = $candidateIds.Count
        $orphanArtifacts = @($document.orphan_candidate_artifacts)
        if (@($orphanArtifacts | Where-Object { $_ -notmatch '^(lightgbm|xgboost)-[a-f0-9]{32}\.joblib$' }).Count -gt 0) {
            return [pscustomobject]@{ state = 'INVALID_REPORT'; latestRun = $null }
        }
        $orphanArtifactCount = $orphanArtifacts.Count
    }
    $knownFailureCodes = @(
        'local_configuration_missing', 'market_cost_assumptions_missing_or_invalid',
        'market_cost_assumptions_inconsistent',
        'insufficient_training_sessions', 'insufficient_partition_samples',
        'single_class_training_partition', 'training_dependencies_missing',
        'unclassified_training_failure'
    )
    $failureCode = if ($document.failure_code -in $knownFailureCodes) {
        [string]$document.failure_code
    } else { $null }
    if (
        ($null -eq $failureCode) -and
        ($document.status -eq 'training_failed') -and
        ($document.output -is [string])
    ) {
        $failureCode = Get-AiTraderModelTrainingFailureCode -Output @([string]$document.output)
    }

    return [pscustomobject]@{
        state = 'AVAILABLE'
        latestRun = [pscustomobject]@{
            runId = [string]$document.run_id
            status = [string]$document.status
            failureStage = if ($document.failure_stage -in @('configuration', 'data_export', 'candidate_training', 'candidate_verification')) {
                [string]$document.failure_stage
            } else { $null }
            failureCode = $failureCode
            market = [string]$document.market
            symbol = [string]$document.symbol
            fromDate = [string]$document.from_date
            toDate = [string]$document.to_date
            recordedAtUtc = $latestFile.LastWriteTimeUtc.ToString('o')
            candidateModelCount = $candidateCount
            allCandidatesUnapproved = $allCandidatesUnapproved
            orphanArtifactCount = $orphanArtifactCount
            errorType = if ($document.error_type -match '^[A-Za-z][A-Za-z0-9.]{0,79}$') {
                [string]$document.error_type
            } else { $null }
        }
    }
}
