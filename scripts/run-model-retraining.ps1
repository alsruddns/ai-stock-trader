param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('KR', 'US')]
    [string]$Market,

    [Parameter(Mandatory = $true)]
    [string]$Symbol,

    [Parameter(Mandatory = $true)]
    [datetime]$FromDate,

    [Parameter(Mandatory = $true)]
    [datetime]$ToDate,

    [decimal]$BuyCommissionBps,

    [decimal]$SellCommissionBps,

    [decimal]$SellTaxBps,

    [decimal]$SlippageBps,

    [decimal]$RoundTripCostBps,

    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'lib\domestic-only-validation.ps1')
Assert-DomesticOnlyValidation -Market $Market -Operation 'model retraining and data validation'
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $repoRoot 'services\market-ai\.venv\Scripts\python.exe'
}
. (Join-Path $PSScriptRoot 'lib\paper-safety.ps1')
. (Join-Path $PSScriptRoot 'lib\model-retraining-report.ps1')
$localSettings = Read-AiTraderLocalSafetySettings -RepositoryRoot $repoRoot
Assert-AiTraderPaperSafetyLocks -Settings $localSettings

$symbolValue = $Symbol.Trim().ToUpperInvariant()
if ($Market -eq 'KR' -and $symbolValue -notmatch '^\d{6}$') {
    throw 'KR symbol must be a six-digit instrument code.'
}
if ($Market -eq 'US' -and $symbolValue -notmatch '^[A-Z][A-Z0-9.-]{0,15}$') {
    throw 'US symbol must contain 1 to 16 letters, digits, dots, or hyphens.'
}
if ($FromDate.Date -gt $ToDate.Date) {
    throw 'FromDate must be on or before ToDate.'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python interpreter not found: $PythonPath"
}

$runId = '{0}-{1}-{2}-{3}' -f $Market.ToLowerInvariant(), $symbolValue.ToLowerInvariant(), $FromDate.ToString('yyyyMMdd'), [guid]::NewGuid().ToString('N').Substring(0, 12)
$dataDirectory = Join-Path $repoRoot 'data\model-candidates'
$reportDirectory = Join-Path $repoRoot '.local\models\retraining'
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$csvPath = Join-Path $dataDirectory "$runId.csv"
$reportPath = Join-Path $reportDirectory "$runId.json"
$exportRelativePath = "data/model-candidates/$runId.csv"
$modelDirectory = Join-Path $repoRoot '.local\models'

$providedCostParameters = $PSBoundParameters
$envSettings = @{}
function Resolve-CostAssumption([string]$ParameterName, [string]$EnvironmentName, [decimal]$Value) {
    $maximum = if ($ParameterName -eq 'RoundTripCostBps') { 40000 } else { 10000 }
    if ($providedCostParameters.ContainsKey($ParameterName)) {
        if ($Value -lt 0 -or $Value -gt $maximum) { throw "Cost assumption '$ParameterName' is out of range." }
        return $Value
    }
    $raw = $envSettings[$EnvironmentName]
    $parsed = [decimal]0
    if ([string]::IsNullOrWhiteSpace($raw) -or
        -not [decimal]::TryParse($raw, [Globalization.NumberStyles]::Number,
            [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed) -or $parsed -lt 0 -or $parsed -gt $maximum) {
        throw "Required market backtest cost assumption '$EnvironmentName' is missing or invalid."
    }
    return $parsed
}
$stage = 'configuration'
$stageExitCode = $null
$datasetSha256 = $null
$candidateModelIds = @()
$orphanCandidateArtifacts = @()
try {
    $envFile = Join-Path $repoRoot '.env_local'
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        throw 'The ignored .env_local file is required.'
    }
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([^#=]+)=(.*)$') {
            $envSettings[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'")
        }
    }
    $marketCostPrefix = "BACKTEST_$Market"
    $BuyCommissionBps = Resolve-CostAssumption 'BuyCommissionBps' "${marketCostPrefix}_BUY_COMMISSION_BPS" $BuyCommissionBps
    $SellCommissionBps = Resolve-CostAssumption 'SellCommissionBps' "${marketCostPrefix}_SELL_COMMISSION_BPS" $SellCommissionBps
    $SellTaxBps = Resolve-CostAssumption 'SellTaxBps' "${marketCostPrefix}_SELL_TAX_BPS" $SellTaxBps
    $SlippageBps = Resolve-CostAssumption 'SlippageBps' "${marketCostPrefix}_SLIPPAGE_BPS" $SlippageBps
    $RoundTripCostBps = Resolve-CostAssumption 'RoundTripCostBps' "${marketCostPrefix}_ROUND_TRIP_COST_BPS" $RoundTripCostBps
    if ($BuyCommissionBps + $SellCommissionBps + $SellTaxBps + 2 * $SlippageBps -le 0 -or
        $BuyCommissionBps -gt 10000 -or $SellCommissionBps -gt 10000 -or $SellTaxBps -gt 10000 -or
        $SlippageBps -gt 10000 -or
        $RoundTripCostBps -lt ($BuyCommissionBps + $SellCommissionBps + $SellTaxBps + 2 * $SlippageBps)) {
        throw 'Market backtest cost assumptions are invalid or the label round-trip cost is too low.'
    }
} catch {
    $failureCode = if ($_.Exception.Message -match "\.env_local file is required") {
        'local_configuration_missing'
    } elseif ($_.Exception.Message -match 'missing or invalid') {
        'market_cost_assumptions_missing_or_invalid'
    } else {
        'market_cost_assumptions_inconsistent'
    }
    Write-AiTraderModelRetrainingFailureReport -Path $reportPath -RunId $runId `
        -FailureStage 'configuration' -Market $Market -Symbol $symbolValue `
        -FromDate $FromDate.ToString('yyyy-MM-dd') -ToDate $ToDate.ToString('yyyy-MM-dd') `
        -DatasetPath $exportRelativePath -FailureCode $failureCode
    throw "Model retraining stopped during configuration; a sanitized report is available at $reportPath."
}

$existingManifestNames = @()
$existingArtifactNames = @()
$stage = 'data_export'
$exportArgs = @(
    (Join-Path $repoRoot 'scripts\export-market-data.py'),
    '--market', $Market,
    '--symbol', $symbolValue,
    '--from-date', $FromDate.ToString('yyyy-MM-dd'),
    '--to-date', $ToDate.ToString('yyyy-MM-dd'),
    '--output', $exportRelativePath,
    '--require-approved-websocket-source'
)

Push-Location $repoRoot
try {
    $exportOutput = & $PythonPath @exportArgs 2>&1
    $stageExitCode = $LASTEXITCODE
    if ($stageExitCode -ne 0) {
        throw 'Market data export failed.'
    }
    $export = ($exportOutput -join [Environment]::NewLine) | ConvertFrom-Json
    $sidecarPath = "$csvPath.provenance.json"
    if ($export.rights_reviewed -ne $true -or $export.source_quality_verified -ne $true -or
        -not (Test-Path -LiteralPath $csvPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $sidecarPath -PathType Leaf)) {
        throw 'Export did not produce a rights-reviewed, source-quality-verified CSV and provenance sidecar.'
    }
    $sidecar = Get-Content -LiteralPath $sidecarPath -Raw | ConvertFrom-Json
    $datasetSha256 = (Get-FileHash -LiteralPath $csvPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sidecar.rights_reviewed -ne $true -or $sidecar.dataset_sha256 -ne $datasetSha256 -or
        $sidecar.permissions.local_storage -ne $true -or $sidecar.permissions.backtesting -ne $true -or
        $sidecar.permissions.model_training -ne $true) {
        throw 'Export provenance did not verify the CSV hash and required personal-use permissions.'
    }

    $trainingArgs = @(
        (Join-Path $repoRoot 'scripts\train-ai-models.py'),
        $csvPath,
        '--market', $Market,
        '--symbol', $symbolValue,
        '--buy-commission-bps', $BuyCommissionBps.ToString([Globalization.CultureInfo]::InvariantCulture),
        '--sell-commission-bps', $SellCommissionBps.ToString([Globalization.CultureInfo]::InvariantCulture),
        '--sell-tax-bps', $SellTaxBps.ToString([Globalization.CultureInfo]::InvariantCulture),
        '--slippage-bps', $SlippageBps.ToString([Globalization.CultureInfo]::InvariantCulture),
        '--round-trip-cost-bps', $RoundTripCostBps.ToString([Globalization.CultureInfo]::InvariantCulture)
    )
    $existingManifestNames = @(
        Get-ChildItem -LiteralPath $modelDirectory -Filter '*.json' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match '^(lightgbm|xgboost)-[a-f0-9]{32}$' } |
            ForEach-Object { $_.Name }
    )
    $existingArtifactNames = @(
        Get-ChildItem -LiteralPath $modelDirectory -Filter '*.joblib' -File -ErrorAction SilentlyContinue |
            Where-Object { $_.BaseName -match '^(lightgbm|xgboost)-[a-f0-9]{32}$' } |
            ForEach-Object { $_.Name }
    )
    $stage = 'candidate_training'
    $trainingOutput = & $PythonPath @trainingArgs 2>&1
    $stageExitCode = $LASTEXITCODE
    if ($stageExitCode -ne 0) {
        $candidateModelIds = @(
            Get-ChildItem -LiteralPath $modelDirectory -Filter '*.json' -File -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.BaseName -match '^(lightgbm|xgboost)-[a-f0-9]{32}$' -and
                    $existingManifestNames -notcontains $_.Name
                } |
                ForEach-Object { $_.BaseName }
        )
        $newCandidateArtifacts = @(
            Get-ChildItem -LiteralPath $modelDirectory -Filter '*.joblib' -File -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.BaseName -match '^(lightgbm|xgboost)-[a-f0-9]{32}$' -and
                    $existingArtifactNames -notcontains $_.Name
                } |
                ForEach-Object { $_.Name }
        )
        $orphanCandidateArtifacts = @($newCandidateArtifacts | Where-Object {
            $candidateModelIds -notcontains [IO.Path]::GetFileNameWithoutExtension($_)
        })
        Write-AiTraderModelRetrainingFailureReport -Path $reportPath -RunId $runId `
            -FailureStage 'candidate_training' -Market $Market -Symbol $symbolValue `
            -FromDate $FromDate.ToString('yyyy-MM-dd') -ToDate $ToDate.ToString('yyyy-MM-dd') `
            -DatasetPath $exportRelativePath -DatasetSha256 $datasetSha256 -ExitCode $stageExitCode `
            -FailureCode (Get-AiTraderModelTrainingFailureCode -Output $trainingOutput) `
            -CandidateModelIds $candidateModelIds -OrphanCandidateArtifacts $orphanCandidateArtifacts
        throw 'Candidate training failed; only sanitized failure metadata was recorded.'
    }

    $stage = 'candidate_verification'
    $training = ($trainingOutput -join [Environment]::NewLine) | ConvertFrom-Json
    if ($training.data_rights_verified -ne $true -or -not $training.models -or
        @($training.models | Where-Object { $_.model_id -notmatch '^(lightgbm|xgboost)-[a-f0-9]{32}$' }).Count -gt 0) {
        throw 'Trainer output did not satisfy the expected rights and candidate model contract.'
    }
    foreach ($candidate in $training.models) {
        $manifestPath = Join-Path $repoRoot ('.local\models\{0}.json' -f $candidate.model_id)
        if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
            throw "Candidate manifest is missing: $($candidate.model_id)"
        }
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        if ($manifest.approved -ne $false -or $manifest.artifact_sha256 -ne $candidate.artifact_sha256 -or
            $manifest.dataset_sha256 -ne $datasetSha256) {
            throw "Candidate manifest failed its approval or hash check: $($candidate.model_id)"
        }
    }
    $report = [ordered]@{
        run_id = $runId
        status = 'candidate_created_review_required'
        market = $Market
        symbol = $symbolValue
        from_date = $FromDate.ToString('yyyy-MM-dd')
        to_date = $ToDate.ToString('yyyy-MM-dd')
        dataset_path = $exportRelativePath
        dataset_sha256 = $training.dataset_sha256
        data_rights_verified = $true
        candidate_models_created = $true
        candidates = @($training.models | ForEach-Object {
            [ordered]@{
                model_id = $_.model_id
                model_type = $_.model_type
                artifact_sha256 = $_.artifact_sha256
                approved = $false
                test_sessions = $_.test_sessions
                ai_quant_backtest_test_metrics = $_.ai_quant_backtest_test_metrics
                quant_backtest_test_metrics = $_.quant_backtest_test_metrics
            }
        })
    }
    $stage = 'complete'
    Set-AiTraderAtomicJsonFile -Path $reportPath -Value $report -Depth 12
    Write-Output "Candidate report: $reportPath"
    Get-Content -LiteralPath $reportPath -Raw
} catch {
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        $safeFailureStage = if ($stage -eq 'complete') { 'candidate_verification' } else { $stage }
        if ($safeFailureStage -eq 'candidate_verification') {
            $candidateModelIds = @(
                Get-ChildItem -LiteralPath $modelDirectory -Filter '*.json' -File -ErrorAction SilentlyContinue |
                    Where-Object {
                        $_.BaseName -match '^(lightgbm|xgboost)-[a-f0-9]{32}$' -and
                        $existingManifestNames -notcontains $_.Name
                    } |
                    ForEach-Object { $_.BaseName }
            )
            $newCandidateArtifacts = @(
                Get-ChildItem -LiteralPath $modelDirectory -Filter '*.joblib' -File -ErrorAction SilentlyContinue |
                    Where-Object {
                        $_.BaseName -match '^(lightgbm|xgboost)-[a-f0-9]{32}$' -and
                        $existingArtifactNames -notcontains $_.Name
                    } |
                    ForEach-Object { $_.Name }
            )
            $orphanCandidateArtifacts = @($newCandidateArtifacts | Where-Object {
                $candidateModelIds -notcontains [IO.Path]::GetFileNameWithoutExtension($_)
            })
        }
        Write-AiTraderModelRetrainingFailureReport -Path $reportPath -RunId $runId `
            -FailureStage $safeFailureStage -Market $Market -Symbol $symbolValue `
            -FromDate $FromDate.ToString('yyyy-MM-dd') -ToDate $ToDate.ToString('yyyy-MM-dd') `
            -DatasetPath $exportRelativePath -DatasetSha256 $datasetSha256 -ExitCode $stageExitCode `
            -Exception $_.Exception -CandidateModelIds $candidateModelIds `
            -OrphanCandidateArtifacts $orphanCandidateArtifacts
    }
    throw "Model retraining stopped during '$stage'; a sanitized report is available at $reportPath."
}
finally {
    Pop-Location
}
