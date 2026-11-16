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
    if ($text -match 'approval-ready model training is not ready:') {
        return 'training_not_approval_ready'
    }
    if ($text -match 'at least three distinct sessions are required') {
        return 'insufficient_training_sessions'
    }
    if ($text -match 'insufficient samples per chronological partition') {
        return 'insufficient_partition_samples'
    }
    if ($text -match 'validation quant candidates have no positive-net AI threshold') {
        return 'no_profitable_validation_ai_threshold'
    }
    if ($text -match 'training partition must contain both outcome classes') {
        return 'single_class_training_partition'
    }
    if ($text -match 'Install the optional ai dependencies to train LightGBM and XGBoost models') {
        return 'training_dependencies_missing'
    }
    return 'unclassified_training_failure'
}

function Get-AiTraderModelTrainingDiagnostics {
    param([object[]]$Output)

    # Parse a single, fixed-schema marker emitted by train-ai-models.py. Ignore
    # all surrounding Python output and reconstruct only bounded numeric fields.
    $prefix = 'AI_TRADER_TRAINING_DIAGNOSTICS='
    $markers = @($Output | ForEach-Object { [string]$_ } |
        Where-Object { $_.StartsWith($prefix, [StringComparison]::Ordinal) })
    if ($markers.Count -ne 1) { return $null }
    try {
        $parsed = $markers[0].Substring($prefix.Length) | ConvertFrom-Json -ErrorAction Stop
    } catch { return $null }
    if ($parsed.schema_version -ne 1 -or $null -eq $parsed.threshold_selection_rejections) {
        return $null
    }
    $rejections = @($parsed.threshold_selection_rejections)
    if ($rejections.Count -lt 1 -or $rejections.Count -gt 2) { return $null }
    $result = @()
    foreach ($rejection in $rejections) {
        if ($rejection.model_type -notin @('lightgbm', 'xgboost') -or
            $rejection.failure_code -ne 'no_profitable_validation_ai_threshold' -or
            $null -eq $rejection.validation) { return $null }
        $source = $rejection.validation
        $integerNames = @(
            'validation_quant_candidate_count', 'validation_quant_candidate_session_count',
            'validation_thresholds_evaluated', 'validation_eligible_threshold_count',
            'validation_positive_net_threshold_count', 'validation_max_selected_predictions',
            'validation_max_selected_sessions', 'minimum_validation_trades', 'minimum_validation_sessions'
        )
        $values = @{}
        foreach ($name in $integerNames) {
            $property = $source.PSObject.Properties[$name]
            if ($null -eq $property -or $property.Value -is [bool] -or
                $property.Value -isnot [ValueType] -or [double]$property.Value -lt 0 -or
                [double]$property.Value -gt 1000000 -or [double]$property.Value -ne [math]::Truncate([double]$property.Value)) {
                return $null
            }
            $values[$name] = [int]$property.Value
        }
        $meanName = 'validation_best_eligible_mean_net_return_bps'
        $meanProperty = $source.PSObject.Properties[$meanName]
        if ($null -eq $meanProperty) { return $null }
        $mean = $meanProperty.Value
        if ($null -ne $mean -and ($mean -is [bool] -or $mean -isnot [ValueType] -or
            [double]::IsNaN([double]$mean) -or [double]::IsInfinity([double]$mean) -or
            [math]::Abs([double]$mean) -gt 1000000)) { return $null }
        if ($values.validation_eligible_threshold_count -gt $values.validation_thresholds_evaluated -or
            $values.validation_positive_net_threshold_count -gt $values.validation_eligible_threshold_count -or
            $values.validation_max_selected_predictions -gt $values.validation_quant_candidate_count -or
            $values.validation_max_selected_sessions -gt $values.validation_quant_candidate_session_count -or
            $values.minimum_validation_trades -ne 5 -or $values.minimum_validation_sessions -ne 3) {
            return $null
        }
        $result += [pscustomobject]@{
            model_type = [string]$rejection.model_type
            validation_quant_candidate_count = $values.validation_quant_candidate_count
            validation_quant_candidate_session_count = $values.validation_quant_candidate_session_count
            validation_thresholds_evaluated = $values.validation_thresholds_evaluated
            validation_eligible_threshold_count = $values.validation_eligible_threshold_count
            validation_positive_net_threshold_count = $values.validation_positive_net_threshold_count
            validation_max_selected_predictions = $values.validation_max_selected_predictions
            validation_max_selected_sessions = $values.validation_max_selected_sessions
            validation_best_eligible_mean_net_return_bps = $mean
            minimum_validation_trades = $values.minimum_validation_trades
            minimum_validation_sessions = $values.minimum_validation_sessions
        }
    }
    return ,$result
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
        [string[]]$OrphanCandidateArtifacts = @(),
        [object[]]$ThresholdSelectionDiagnostics = @()
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
        'training_not_approval_ready', 'insufficient_training_sessions', 'insufficient_partition_samples',
        'no_profitable_validation_ai_threshold', 'single_class_training_partition', 'training_dependencies_missing',
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
        threshold_selection_diagnostics = @($ThresholdSelectionDiagnostics)
        candidate_models_created = ($CandidateModelIds.Count -gt 0)
        candidate_model_ids = @($CandidateModelIds)
        orphan_candidate_artifacts = @($OrphanCandidateArtifacts)
        candidates_may_exist_after_partial_failure = ($OrphanCandidateArtifacts.Count -gt 0)
    }
    Set-AiTraderAtomicJsonFile -Path $Path -Value $report -Depth 8
}

function Get-AiTraderModelRetrainingSummary {
    param(
        [Parameter(Mandatory)][string]$Directory,
        [string[]]$OperationalMarkets = @('KR')
    )

    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        return [pscustomobject]@{ state = 'NO_RUNS'; latestRun = $null }
    }
    $markets = @($OperationalMarkets | ForEach-Object { ([string]$_).Trim().ToUpperInvariant() } | Select-Object -Unique)
    if ($markets.Count -eq 0 -or @($markets | Where-Object { $_ -notin @('KR', 'US') }).Count -gt 0) {
        $markets = @('KR')
    }
    $latestFiles = @(Get-ChildItem -LiteralPath $Directory -Filter '*.json' -File -ErrorAction SilentlyContinue |
        Sort-Object -Property LastWriteTimeUtc -Descending)
    if ($latestFiles.Count -eq 0) {
        return [pscustomobject]@{ state = 'NO_RUNS'; latestRun = $null }
    }

    foreach ($latestFile in $latestFiles) {
        try {
            $document = Get-Content -LiteralPath $latestFile.FullName -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        } catch {
            return [pscustomobject]@{ state = 'INVALID_REPORT'; latestRun = $null }
        }
        if ($null -eq $document -or $document.market -notin @('KR', 'US')) {
            return [pscustomobject]@{ state = 'INVALID_REPORT'; latestRun = $null }
        }
        if ($document.market -notin $markets) { continue }

        $validStatuses = @('candidate_created_review_required', 'configuration_failed', 'export_failed', 'training_failed', 'candidate_verification_failed')
        if ($document.status -notin $validStatuses -or
            $document.run_id -notmatch '^(kr|us)-[a-z0-9.-]+-\d{8}-[a-f0-9]{12}$' -or
            -not $document.run_id.StartsWith($document.market.ToLowerInvariant() + '-') -or
            [string]::IsNullOrWhiteSpace([string]$document.symbol)) {
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
            'training_not_approval_ready', 'insufficient_training_sessions', 'insufficient_partition_samples',
            'no_profitable_validation_ai_threshold', 'single_class_training_partition', 'training_dependencies_missing',
            'unclassified_training_failure'
        )
        $failureCode = if ($document.failure_code -in $knownFailureCodes) {
            [string]$document.failure_code
        } else { $null }
        if (($null -eq $failureCode) -and ($document.status -eq 'training_failed') -and ($document.output -is [string])) {
            $failureCode = Get-AiTraderModelTrainingFailureCode -Output @([string]$document.output)
        }
        $thresholdSelectionDiagnostics = @()
        if ($document.threshold_selection_diagnostics) {
            $diagnosticRejections = @($document.threshold_selection_diagnostics | ForEach-Object {
                $diagnostic = $_
                [ordered]@{
                    model_type = $diagnostic.model_type
                    failure_code = 'no_profitable_validation_ai_threshold'
                    validation = [ordered]@{
                        validation_quant_candidate_count = $diagnostic.validation_quant_candidate_count
                        validation_quant_candidate_session_count = $diagnostic.validation_quant_candidate_session_count
                        validation_thresholds_evaluated = $diagnostic.validation_thresholds_evaluated
                        validation_eligible_threshold_count = $diagnostic.validation_eligible_threshold_count
                        validation_positive_net_threshold_count = $diagnostic.validation_positive_net_threshold_count
                        validation_max_selected_predictions = $diagnostic.validation_max_selected_predictions
                        validation_max_selected_sessions = $diagnostic.validation_max_selected_sessions
                        validation_best_eligible_mean_net_return_bps = $diagnostic.validation_best_eligible_mean_net_return_bps
                        minimum_validation_trades = $diagnostic.minimum_validation_trades
                        minimum_validation_sessions = $diagnostic.minimum_validation_sessions
                    }
                }
            })
            $markerPayload = [ordered]@{
                schema_version = 1
                threshold_selection_rejections = $diagnosticRejections
            } | ConvertTo-Json -Depth 8 -Compress
            $thresholdSelectionDiagnostics = @(Get-AiTraderModelTrainingDiagnostics -Output @(
                ('AI_TRADER_TRAINING_DIAGNOSTICS=' + $markerPayload)
            ))
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
                thresholdSelectionDiagnostics = $thresholdSelectionDiagnostics
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
    return [pscustomobject]@{ state = 'NO_RUNS'; latestRun = $null }
}
