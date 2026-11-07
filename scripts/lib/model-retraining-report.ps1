. (Join-Path $PSScriptRoot 'operations-report.ps1')

function Write-AiTraderModelRetrainingFailureReport {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$RunId,
        [Parameter(Mandatory)][ValidateSet('data_export', 'candidate_training', 'candidate_verification')][string]$FailureStage,
        [Parameter(Mandatory)][string]$Market,
        [Parameter(Mandatory)][string]$Symbol,
        [Parameter(Mandatory)][string]$FromDate,
        [Parameter(Mandatory)][string]$ToDate,
        [Parameter(Mandatory)][string]$DatasetPath,
        [string]$DatasetSha256,
        [Nullable[int]]$ExitCode,
        [System.Exception]$Exception,
        [string[]]$CandidateModelIds = @(),
        [string[]]$OrphanCandidateArtifacts = @()
    )

    $status = switch ($FailureStage) {
        'data_export' { 'export_failed' }
        'candidate_training' { 'training_failed' }
        'candidate_verification' { 'candidate_verification_failed' }
    }
    $errorType = if ($null -ne $Exception) { $Exception.GetType().Name } else { $null }
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
        candidate_models_created = ($CandidateModelIds.Count -gt 0)
        candidate_model_ids = @($CandidateModelIds)
        orphan_candidate_artifacts = @($OrphanCandidateArtifacts)
        candidates_may_exist_after_partial_failure = ($OrphanCandidateArtifacts.Count -gt 0)
    }
    Set-AiTraderAtomicJsonFile -Path $Path -Value $report -Depth 8
}
