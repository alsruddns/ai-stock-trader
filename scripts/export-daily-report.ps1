param(
    [string]$ApiBaseUrl = "http://127.0.0.1:8080",
    [string]$PythonApiBaseUrl = "http://127.0.0.1:8000",
    [ValidateRange(0, 3600)] [int]$WaitTimeoutSeconds = 0,
    [ValidateRange(1, 300)] [int]$RetryIntervalSeconds = 10,
    [string]$OutputPath
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

function Get-OptionalLocalJson([string]$Url) {
    try { return Invoke-RestMethod -Uri $Url -TimeoutSec 5 }
    catch { return $null }
}

$pythonHealth = Get-OptionalLocalJson "$PythonApiBaseUrl/api/v1/health"
$paperSession = Get-OptionalLocalJson "$PythonApiBaseUrl/api/v1/paper/session"
$aiModels = Get-OptionalLocalJson "$PythonApiBaseUrl/api/v1/ai/models"
$marketSessions = @{ KR = $null; US = $null }
$marketDataQuality = @{ KR = $null; US = $null }
foreach ($market in @('KR', 'US')) {
    $marketSessions[$market] = Get-OptionalLocalJson "$PythonApiBaseUrl/api/v1/market/sessions/$market"
    if (-not $paperSession -or -not $paperSession.decisions) { continue }
    $symbols = @($paperSession.decisions.PSObject.Properties |
        ForEach-Object { $_.Value } |
        Where-Object { $_.market -eq $market -and $_.symbol } |
        ForEach-Object { ([string]$_.symbol).Trim().ToUpperInvariant() } |
        Select-Object -Unique)
    if ($symbols.Count -lt 1 -or $symbols.Count -gt 50) { continue }
    $query = @('latest_closed=true') + @($symbols | ForEach-Object {
        'symbols=' + [Uri]::EscapeDataString($_)
    })
    $qualityUrl = "${PythonApiBaseUrl}/api/v1/market/session-quality/${market}?" + ($query -join '&')
    $marketDataQuality[$market] = Get-OptionalLocalJson $qualityUrl
}

$report | Add-Member -MemberType NoteProperty -Name reportSchemaVersion -Value 2 -Force
$report | Add-Member -MemberType NoteProperty -Name generatedAt -Value ([DateTimeOffset]::Now.ToString('o')) -Force
$report | Add-Member -MemberType NoteProperty -Name pythonConnected -Value ($null -ne $pythonHealth) -Force
$report | Add-Member -MemberType NoteProperty -Name pythonHealth -Value $pythonHealth -Force
$report | Add-Member -MemberType NoteProperty -Name paperSession -Value $paperSession -Force
$report | Add-Member -MemberType NoteProperty -Name aiModels -Value $aiModels -Force
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
$report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $reportPath -Encoding utf8
Write-Output "Saved local operations report: $reportPath"
