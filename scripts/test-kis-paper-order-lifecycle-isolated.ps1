[CmdletBinding()]
param(
    [Parameter(Mandatory)] [ValidateSet('KR', 'US')] [string]$Market,
    [Parameter(Mandatory)] [string]$Symbol,
    [Parameter(Mandatory)] [decimal]$LimitPrice,
    [string]$Exchange = 'NASD',
    [string]$MarketAiApiBaseUrl = 'http://127.0.0.1:8000',
    [ValidateRange(1024, 65535)] [int]$TemporaryPort = 8081,
    [switch]$PreflightOnly,
    [switch]$ConfirmPaperOrderAndCancel
)

$ErrorActionPreference = 'Stop'
if ($PreflightOnly -eq $ConfirmPaperOrderAndCancel) {
    throw 'Choose exactly one mode: -PreflightOnly or -ConfirmPaperOrderAndCancel.'
}
if ($LimitPrice -le 0) { throw 'LimitPrice must be positive.' }
if ($Market -eq 'KR' -and $ConfirmPaperOrderAndCancel) {
    throw 'Domestic PAPER cancel preflight is unsupported; no order was submitted. Use -PreflightOnly or wait until KIS documents a PAPER cancel-quantity query.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'The isolated PAPER test requires the ignored .env_local file.'
}

$originalEnvironment = @{}
foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $originalEnvironment[[string]$entry.Key] = [string]$entry.Value
}
$temporaryApi = $null

try {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $separator = $trimmed.IndexOf('=')
        if ($separator -lt 1) { continue }
        $name = $trimmed.Substring(0, $separator).Trim()
        $value = $trimmed.Substring($separator + 1).Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }

    if ($env:KIS_ENVIRONMENT -ne 'paper' -or $env:TRADING_MODE -ne 'paper' -or
        $env:LIVE_TRADING_ENABLED -ne 'false' -or $env:KIS_PAPER_ORDERS_ENABLED -ne 'false' -or
        $env:KIS_REAL_ORDERS_ENABLED -ne 'false' -or $env:KIS_REAL_RECONCILIATION_ENABLED -ne 'false' -or
        $env:PAPER_AUTOTRADING_ENABLED -ne 'false') {
        throw 'Isolated test requires PAPER mode and all persistent order/automation switches disabled in .env_local.'
    }
    if ($env:KIS_ACCOUNT_READ_ENABLED -ne 'true' -or $env:KIS_PAPER_ACCOUNT_READ_ENABLED -ne 'true' -or
        $env:KIS_MARKET_DATA_ENABLED -ne 'true' -or -not $env:POSTGRES_PASSWORD -or
        -not $env:KIS_PAPER_APP_KEY -or -not $env:KIS_PAPER_APP_SECRET -or -not $env:KIS_PAPER_ACCOUNT_NUMBER) {
        throw 'Isolated test requires PAPER read-only credentials and PostgreSQL configuration in .env_local.'
    }
    if (($env:KIS_PAPER_ACCOUNT_NUMBER -replace '-', '') -notmatch '^\d{10}$') {
        throw 'KIS_PAPER_ACCOUNT_NUMBER must contain eight account digits and a two-digit product code.'
    }
    if (Get-NetTCPConnection -State Listen -LocalPort $TemporaryPort -ErrorAction SilentlyContinue) {
        throw "Temporary port $TemporaryPort is already in use. No service was started."
    }

    $env:SERVER_ADDRESS = '127.0.0.1'
    $env:SERVER_PORT = [string]$TemporaryPort
    $env:KIS_PAPER_ORDERS_ENABLED = 'true'
    $env:KIS_REAL_APP_KEY = ''
    $env:KIS_REAL_APP_SECRET = ''
    $env:KIS_REAL_ACCOUNT_NUMBER = ''
    $env:KIS_REAL_ACCOUNT_READ_ENABLED = 'false'
    $env:KIS_REAL_ORDERS_ENABLED = 'false'
    $env:KIS_REAL_RECONCILIATION_ENABLED = 'false'
    $env:LIVE_TRADING_ENABLED = 'false'
    $env:PAPER_AUTOTRADING_ENABLED = 'false'
    $env:PAPER_ORDER_TIMEOUT_SECONDS = '0'
    $env:KIS_SNAPSHOT_PERSISTENCE_ENABLED = 'false'

    $apiDirectory = Join-Path $repoRoot 'backend/trading-api'
    Push-Location $apiDirectory
    try {
        & .\mvnw.cmd '-DskipTests' package
        if ($LASTEXITCODE -ne 0) { throw "Spring Boot package build failed with code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }

    $jarPath = Join-Path $apiDirectory 'target/trading-api-0.1.0-SNAPSHOT.jar'
    if (-not (Test-Path -LiteralPath $jarPath)) { throw 'Built Spring Boot jar was not found.' }
    $logDirectory = Join-Path $repoRoot '.local/logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $stdoutPath = Join-Path $logDirectory "paper-order-isolated-$stamp.out.log"
    $stderrPath = Join-Path $logDirectory "paper-order-isolated-$stamp.err.log"
    $javaPath = (Get-Command java -ErrorAction Stop).Source
    $temporaryApi = Start-Process -FilePath $javaPath -ArgumentList ('-jar "{0}"' -f $jarPath) `
        -WorkingDirectory $apiDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
    $operationsUrl = "http://127.0.0.1:$TemporaryPort/api/v1/operations/summary"
    $ready = $false
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if ($temporaryApi.HasExited) { throw "Isolated Spring Boot exited before becoming ready. Logs: $stderrPath" }
        try {
            $operations = Invoke-RestMethod -Uri $operationsUrl -TimeoutSec 3
            if ($operations.tradingMode -eq 'paper' -and $operations.liveTrading -eq 'disabled' -and
                $operations.kisPaperOrdersEnabled -and -not $operations.kisRealOrdersEnabled -and
                -not $operations.kisRealReconciliationEnabled -and -not $operations.paperAutomationEnabled) {
                $ready = $true
                break
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw "Isolated Spring Boot did not report the required PAPER-only configuration. Logs: $stderrPath" }

    $testScript = Join-Path $PSScriptRoot 'test-kis-paper-order-lifecycle.ps1'
    $powershellPath = (Get-Process -Id $PID).Path
    $limitText = $LimitPrice.ToString([Globalization.CultureInfo]::InvariantCulture)
    $testArguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $testScript,
        '-Market', $Market, '-Symbol', $Symbol, '-LimitPrice', $limitText, '-Exchange', $Exchange,
        '-SpringApiBaseUrl', "http://127.0.0.1:$TemporaryPort", '-MarketAiApiBaseUrl', $MarketAiApiBaseUrl,
        '-EnablePaperOrdersForThisRun')
    if ($PreflightOnly) { $testArguments += '-PreflightOnly' } else { $testArguments += '-ConfirmPaperOrderAndCancel' }
    & $powershellPath @testArguments
    if ($LASTEXITCODE -ne 0) { throw "PAPER lifecycle test exited with code $LASTEXITCODE." }
} finally {
    if ($null -ne $temporaryApi -and -not $temporaryApi.HasExited) {
        Stop-Process -Id $temporaryApi.Id -Force -ErrorAction SilentlyContinue
        $temporaryApi.WaitForExit(10000) | Out-Null
    }

    $currentEnvironment = [Environment]::GetEnvironmentVariables('Process')
    foreach ($name in $currentEnvironment.Keys) {
        if (-not $originalEnvironment.ContainsKey([string]$name)) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
    }
    foreach ($name in $originalEnvironment.Keys) {
        Set-Item -LiteralPath "Env:$name" -Value $originalEnvironment[$name]
    }
}
