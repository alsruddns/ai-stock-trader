param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8080"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$reportDirectory = Join-Path $repoRoot ".local\reports"
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$report = Invoke-RestMethod -Uri "$ApiBaseUrl/api/v1/operations/summary" -TimeoutSec 5
$reportName = "operations-{0}.json" -f (Get-Date -Format "yyyy-MM-dd")
$reportPath = Join-Path $reportDirectory $reportName
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Output "Saved local operations report: $reportPath"
