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
    [string]$PythonPath = (Join-Path (Split-Path -Parent $PSScriptRoot) 'services\market-ai\.venv\Scripts\python.exe')
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$symbolValue = $Symbol.Trim().ToUpperInvariant()
if ($Market -eq 'KR' -and $symbolValue -notmatch '^\d{6}$') { throw 'KR symbol must be a six-digit instrument code.' }
if ($Market -eq 'US' -and $symbolValue -notmatch '^[A-Z][A-Z0-9.-]{0,15}$') { throw 'US symbol must contain 1 to 16 letters, digits, dots, or hyphens.' }
if ($FromDate.Date -gt $ToDate.Date) { throw 'FromDate must be on or before ToDate.' }
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw "Python interpreter not found: $PythonPath" }

$envFile = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) { throw 'The ignored .env_local file is required.' }
$localSettings = @{}
foreach ($line in Get-Content -LiteralPath $envFile) {
    if ($line -match '^\s*([^#=]+)=(.*)$') { $localSettings[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'") }
}
foreach ($required in @('TRADING_MODE', 'LIVE_TRADING_ENABLED', 'KIS_PAPER_ORDERS_ENABLED', 'KIS_REAL_ORDERS_ENABLED', 'PAPER_AUTOTRADING_ENABLED')) {
    if (-not $localSettings.ContainsKey($required)) { throw "Required local safety setting '$required' is missing." }
}
if ($localSettings.TRADING_MODE -ne 'paper' -or $localSettings.LIVE_TRADING_ENABLED -ne 'false' -or
    $localSettings.KIS_PAPER_ORDERS_ENABLED -ne 'false' -or $localSettings.KIS_REAL_ORDERS_ENABLED -ne 'false' -or
    $localSettings.PAPER_AUTOTRADING_ENABLED -ne 'false') {
    throw 'Holdout evaluation requires PAPER mode with all order and automation switches disabled.'
}

$runId = '{0}-{1}-holdout-{2}-{3}' -f $Market.ToLowerInvariant(), $symbolValue.ToLowerInvariant(), $FromDate.ToString('yyyyMMdd'), [guid]::NewGuid().ToString('N').Substring(0, 12)
$csvPath = Join-Path $repoRoot "data\model-candidates\$runId.csv"
$reportDirectory = Join-Path $repoRoot '.local\validation\model-holdout'
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$reportPath = Join-Path $reportDirectory "holdout-$runId.json"
$exportRelativePath = "data/model-candidates/$runId.csv"
$exportArgs = @((Join-Path $repoRoot 'scripts\export-market-data.py'), '--market', $Market, '--symbol', $symbolValue,
    '--from-date', $FromDate.ToString('yyyy-MM-dd'), '--to-date', $ToDate.ToString('yyyy-MM-dd'), '--output', $exportRelativePath)
$evaluationArgs = @((Join-Path $repoRoot 'scripts\evaluate-ai-model-candidates.py'), $csvPath,
    '--market', $Market, '--symbol', $symbolValue, '--registry', (Join-Path $repoRoot '.local\models'))

Push-Location $repoRoot
try {
    $exportOutput = & $PythonPath @exportArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Holdout data export failed (exit $LASTEXITCODE): $($exportOutput -join [Environment]::NewLine)" }
    $export = ($exportOutput -join [Environment]::NewLine) | ConvertFrom-Json
    $sidecarPath = "$csvPath.provenance.json"
    if (-not (Test-Path -LiteralPath $csvPath -PathType Leaf) -or -not (Test-Path -LiteralPath $sidecarPath -PathType Leaf)) {
        throw 'Export did not produce the CSV and rights provenance sidecar.'
    }
    $sidecar = Get-Content -LiteralPath $sidecarPath -Raw | ConvertFrom-Json
    $csvSha256 = (Get-FileHash -LiteralPath $csvPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sidecar.rights_reviewed -ne $true -or $sidecar.dataset_sha256 -ne $csvSha256 -or
        $sidecar.permissions.local_storage -ne $true -or $sidecar.permissions.backtesting -ne $true) {
        throw 'Holdout provenance did not verify the CSV hash and local storage/backtesting permissions.'
    }
    $evaluationOutput = & $PythonPath @evaluationArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Candidate holdout evaluation failed (exit $LASTEXITCODE): $($evaluationOutput -join [Environment]::NewLine)" }
    $evaluation = ($evaluationOutput -join [Environment]::NewLine) | ConvertFrom-Json
    $evaluation | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $reportPath -Encoding utf8
    Write-Output "Holdout report: $reportPath"
    Get-Content -LiteralPath $reportPath -Raw
} finally {
    Pop-Location
}
