param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8080",
    [ValidateRange(0, 3600)] [int]$WaitTimeoutSeconds = 0,
    [ValidateRange(1, 300)] [int]$RetryIntervalSeconds = 10
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
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
$reportName = "operations-{0}.json" -f (Get-Date -Format "yyyy-MM-dd")
$reportPath = Join-Path $reportDirectory $reportName
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Output "Saved local operations report: $reportPath"
