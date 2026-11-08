param(
    [string]$ApiBaseUrl = "http://127.0.0.1:18080",
    [ValidateRange(1, 3600)] [int]$WaitTimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$reportDirectory = Join-Path $repoRoot ".local\reports"
$logPath = Join-Path $reportDirectory "daily-report-task.log"
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$operationsReportLibrary = Join-Path $PSScriptRoot 'lib\operations-report.ps1'
. $operationsReportLibrary
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss zzz"

try {
    $archivePath = New-AiTraderOperationsReportRunPath -Directory $reportDirectory
    & (Join-Path $PSScriptRoot "export-daily-report.ps1") `
        -ApiBaseUrl $ApiBaseUrl -WaitTimeoutSeconds $WaitTimeoutSeconds -ArchiveOutputPath $archivePath | Out-Null
    Add-Content -LiteralPath $logPath -Value "$timestamp SUCCESS report exported archive=$([IO.Path]::GetFileName($archivePath))" -Encoding utf8
}
catch {
    $errorType = $_.Exception.GetType().Name
    Add-Content -LiteralPath $logPath -Value "$timestamp FAILED $errorType" -Encoding utf8
    exit 1
}
