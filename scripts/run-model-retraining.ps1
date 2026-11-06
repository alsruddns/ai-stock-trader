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

    [string]$PythonPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'services\market-ai\.venv\Scripts\python.exe'),
    [decimal]$CommissionBps = 5,
    [decimal]$SlippageBps = 5
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
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
    if ($LASTEXITCODE -ne 0) {
        throw "Market data export failed (exit $LASTEXITCODE): $($exportOutput -join [Environment]::NewLine)"
    }
    $export = ($exportOutput -join [Environment]::NewLine) | ConvertFrom-Json
    $sidecarPath = "$csvPath.provenance.json"
    if ($export.rights_reviewed -ne $true -or $export.source_quality_verified -ne $true -or
        -not (Test-Path -LiteralPath $csvPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $sidecarPath -PathType Leaf)) {
        throw 'Export did not produce a rights-reviewed, source-quality-verified CSV and provenance sidecar.'
    }
    $sidecar = Get-Content -LiteralPath $sidecarPath -Raw | ConvertFrom-Json
    $csvSha256 = (Get-FileHash -LiteralPath $csvPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sidecar.rights_reviewed -ne $true -or $sidecar.dataset_sha256 -ne $csvSha256 -or
        $sidecar.permissions.local_storage -ne $true -or $sidecar.permissions.backtesting -ne $true -or
        $sidecar.permissions.model_training -ne $true) {
        throw 'Export provenance did not verify the CSV hash and required personal-use permissions.'
    }

    $trainingArgs = @(
        (Join-Path $repoRoot 'scripts\train-ai-models.py'),
        $csvPath,
        '--market', $Market,
        '--symbol', $symbolValue,
        '--commission-bps', $CommissionBps.ToString([Globalization.CultureInfo]::InvariantCulture),
        '--slippage-bps', $SlippageBps.ToString([Globalization.CultureInfo]::InvariantCulture)
    )
    $modelDirectory = Join-Path $repoRoot '.local\models'
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
    $trainingOutput = & $PythonPath @trainingArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        $addedManifests = @(
            Get-ChildItem -LiteralPath $modelDirectory -Filter '*.json' -File -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.BaseName -match '^(lightgbm|xgboost)-[a-f0-9]{32}$' -and
                    $existingManifestNames -notcontains $_.Name
                } |
                ForEach-Object { $_.BaseName }
        )
        $addedArtifacts = @(
            Get-ChildItem -LiteralPath $modelDirectory -Filter '*.joblib' -File -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.BaseName -match '^(lightgbm|xgboost)-[a-f0-9]{32}$' -and
                    $existingArtifactNames -notcontains $_.Name
                } |
                ForEach-Object { $_.Name }
        )
        $failure = [ordered]@{
            run_id = $runId
            status = 'training_failed'
            market = $Market
            symbol = $symbolValue
            from_date = $FromDate.ToString('yyyy-MM-dd')
            to_date = $ToDate.ToString('yyyy-MM-dd')
            dataset_path = $exportRelativePath
            dataset_sha256 = $csvSha256
            candidate_models_created = ($addedManifests.Count -gt 0)
            candidate_model_ids = $addedManifests
            orphan_candidate_artifacts = $addedArtifacts
            candidates_may_exist_after_partial_failure = ($addedArtifacts.Count -gt 0)
            output = ($trainingOutput -join [Environment]::NewLine)
        }
        $failure | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $reportPath -Encoding utf8
        throw "Candidate training failed (exit $LASTEXITCODE). Evidence saved to $reportPath."
    }

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
            $manifest.dataset_sha256 -ne $csvSha256) {
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
    $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding utf8
    Write-Output "Candidate report: $reportPath"
    Get-Content -LiteralPath $reportPath -Raw
}
finally {
    Pop-Location
}
