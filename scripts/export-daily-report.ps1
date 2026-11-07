param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8080",
    [string]$PythonApiBaseUrl = "http://127.0.0.1:8000",
    [ValidateRange(0, 3600)] [int]$WaitTimeoutSeconds = 0,
    [ValidateRange(1, 300)] [int]$RetryIntervalSeconds = 10,
    [string]$OutputPath,
    [string]$ModelRetrainingReportDirectory
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'lib\operations-report.ps1')
. (Join-Path $PSScriptRoot 'lib\model-retraining-report.ps1')
$reportDirectory = Join-Path $repoRoot ".local\reports"
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$deadline = [DateTimeOffset]::UtcNow.AddSeconds($WaitTimeoutSeconds)
$report = $null
do {
    try {
        $report = Invoke-RestMethod -Uri "$ApiBaseUrl/api/v1/operations/summary" -TimeoutSec 5
        break
    }
    catch {
        if ([DateTimeOffset]::UtcNow -ge $deadline) {
            throw 'The local Spring operations API did not become available before the report timeout.'
        }
        Start-Sleep -Seconds ([Math]::Min($RetryIntervalSeconds,
            [Math]::Max(1, [int][Math]::Ceiling(($deadline - [DateTimeOffset]::UtcNow).TotalSeconds))))
    }
} while ([DateTimeOffset]::UtcNow -lt $deadline)

if ($null -eq $report) {
    throw 'The local Spring operations API returned no report.'
}

$endpointStatus = [ordered]@{}
$healthResult = Invoke-AiTraderOptionalJsonRequest "$PythonApiBaseUrl/api/v1/health"
$endpointStatus['python.health'] = [ordered]@{ state = $healthResult.state; httpStatus = $healthResult.http_status; errorType = $healthResult.error_type }
$pythonHealth = $healthResult.data
$paperSessionResult = Invoke-AiTraderOptionalJsonRequest "$PythonApiBaseUrl/api/v1/paper/session"
$endpointStatus['python.paper_session'] = [ordered]@{ state = $paperSessionResult.state; httpStatus = $paperSessionResult.http_status; errorType = $paperSessionResult.error_type }
$paperSession = $paperSessionResult.data
$modelsResult = Invoke-AiTraderOptionalJsonRequest "$PythonApiBaseUrl/api/v1/ai/models"
$endpointStatus['python.ai_models'] = [ordered]@{ state = $modelsResult.state; httpStatus = $modelsResult.http_status; errorType = $modelsResult.error_type }
$aiModels = $modelsResult.data
if ([string]::IsNullOrWhiteSpace($ModelRetrainingReportDirectory)) {
    $retrainingDirectory = Join-Path $repoRoot '.local\models\retraining'
} elseif ([IO.Path]::IsPathRooted($ModelRetrainingReportDirectory)) {
    $retrainingDirectory = $ModelRetrainingReportDirectory
} else {
    $retrainingDirectory = Join-Path $repoRoot $ModelRetrainingReportDirectory
}
$modelRetraining = Get-AiTraderModelRetrainingSummary -Directory $retrainingDirectory
$endpointStatus['local.model_retraining'] = [ordered]@{
    state = $modelRetraining.state
    latestStatus = $modelRetraining.latestRun.status
    failureStage = $modelRetraining.latestRun.failureStage
    errorType = $modelRetraining.latestRun.errorType
}
$marketSessions = @{ KR = $null; US = $null }
$marketDataQuality = @{ KR = $null; US = $null }
foreach ($market in @('KR', 'US')) {
    $sessionResult = Invoke-AiTraderOptionalJsonRequest "$PythonApiBaseUrl/api/v1/market/sessions/$market"
    $endpointStatus["python.market_session_$market"] = [ordered]@{ state = $sessionResult.state; httpStatus = $sessionResult.http_status; errorType = $sessionResult.error_type }
    $marketSessions[$market] = $sessionResult.data
    if (-not $paperSession -or -not $paperSession.decisions) {
        $endpointStatus["python.market_data_quality_$market"] = [ordered]@{ state = 'SKIPPED_NO_DECISIONS'; httpStatus = $null; errorType = $null }
        continue
    }
    $symbols = @($paperSession.decisions.PSObject.Properties |
        ForEach-Object { $_.Value } |
        Where-Object { $_.market -eq $market -and $_.symbol } |
        ForEach-Object { ([string]$_.symbol).Trim().ToUpperInvariant() } |
        Select-Object -Unique)
    if ($symbols.Count -lt 1) {
        $endpointStatus["python.market_data_quality_$market"] = [ordered]@{ state = 'SKIPPED_NO_SYMBOLS'; httpStatus = $null; errorType = $null }
        continue
    }
    if ($symbols.Count -gt 50) {
        $endpointStatus["python.market_data_quality_$market"] = [ordered]@{ state = 'SKIPPED_SYMBOL_LIMIT'; httpStatus = $null; errorType = $null }
        continue
    }
    $query = @('latest_closed=true') + @($symbols | ForEach-Object {
        'symbols=' + [Uri]::EscapeDataString($_)
    })
    $qualityUrl = "${PythonApiBaseUrl}/api/v1/market/session-quality/${market}?" + ($query -join '&')
    $qualityResult = Invoke-AiTraderOptionalJsonRequest $qualityUrl
    $endpointStatus["python.market_data_quality_$market"] = [ordered]@{ state = $qualityResult.state; httpStatus = $qualityResult.http_status; errorType = $qualityResult.error_type }
    $marketDataQuality[$market] = $qualityResult.data
}

$report | Add-Member -MemberType NoteProperty -Name reportSchemaVersion -Value 4 -Force
$report | Add-Member -MemberType NoteProperty -Name generatedAt -Value ([DateTimeOffset]::Now.ToString('o')) -Force
$report | Add-Member -MemberType NoteProperty -Name pythonConnected -Value ($healthResult.state -eq 'AVAILABLE' -and $null -ne $pythonHealth) -Force
$endpointStates = @($endpointStatus.Values | ForEach-Object { $_.state })
$optionalDataStatus = if (@($endpointStates | Where-Object { $_ -in @('HTTP_ERROR', 'UNREACHABLE') }).Count -gt 0) {
    'PARTIAL'
} elseif (@($endpointStates | Where-Object { $_ -ne 'AVAILABLE' }).Count -gt 0) {
    'INCOMPLETE'
} else {
    'COMPLETE'
}
$report | Add-Member -MemberType NoteProperty -Name optionalDataStatus -Value $optionalDataStatus -Force
$report | Add-Member -MemberType NoteProperty -Name endpointStatus -Value $endpointStatus -Force
$report | Add-Member -MemberType NoteProperty -Name pythonHealth -Value $pythonHealth -Force
$report | Add-Member -MemberType NoteProperty -Name paperSession -Value $paperSession -Force
$report | Add-Member -MemberType NoteProperty -Name aiModels -Value $aiModels -Force
$report | Add-Member -MemberType NoteProperty -Name modelRetraining -Value $modelRetraining -Force
$report | Add-Member -MemberType NoteProperty -Name marketSessions -Value $marketSessions -Force
$report | Add-Member -MemberType NoteProperty -Name marketDataQuality -Value $marketDataQuality -Force

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $reportName = "operations-{0}.json" -f (Get-Date -Format "yyyy-MM-dd")
    $reportPath = Join-Path $reportDirectory $reportName
} elseif ([IO.Path]::IsPathRooted($OutputPath)) {
    $reportPath = $OutputPath
} else {
    $reportPath = Join-Path $repoRoot $OutputPath
}
$outputDirectory = Split-Path -Parent $reportPath
if ($outputDirectory) { New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null }
Set-AiTraderAtomicJsonFile -Path $reportPath -Value $report -Depth 12
Write-Output "Saved local operations report: $reportPath"
