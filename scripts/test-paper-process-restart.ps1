$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envFile)) { $envFile = Join-Path $repoRoot '.env' }
if (-not (Test-Path -LiteralPath $envFile)) {
    throw 'Repository .env_local or .env file is missing. Copy .env.example and configure local PostgreSQL first.'
}

$originalEnvironment = @{}
foreach ($line in Get-Content -LiteralPath $envFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
    $parts = $trimmed.Split('=', 2)
    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if (-not $originalEnvironment.ContainsKey($name)) {
        $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}

if (-not $env:POSTGRES_PASSWORD -or $env:POSTGRES_PASSWORD -eq 'replace_with_your_local_postgres_password') {
    throw 'Set POSTGRES_PASSWORD in .env to the password for your local PostgreSQL user.'
}

foreach ($name in @('TRADING_MODE', 'LIVE_TRADING_ENABLED', 'KIS_REAL_ORDERS_ENABLED',
        'KIS_PAPER_ORDERS_ENABLED', 'SERVER_ADDRESS')) {
    if (-not $originalEnvironment.ContainsKey($name)) {
        $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
}
$env:TRADING_MODE = 'paper'
$env:LIVE_TRADING_ENABLED = 'false'
$env:KIS_REAL_ORDERS_ENABLED = 'false'
$env:KIS_PAPER_ORDERS_ENABLED = 'false'
$env:SERVER_ADDRESS = '127.0.0.1'
$port = Get-Random -Minimum 20000 -Maximum 50000
$baseUrl = "http://127.0.0.1:$port"
$idempotencyKey = [guid]::NewGuid().ToString()
$body = @{
    market = 'KR'
    symbol = '005930'
    side = 'BUY'
    quantity = 1
    limitPrice = 1
    currency = 'KRW'
} | ConvertTo-Json -Compress
$apiDirectory = Join-Path $repoRoot 'backend\trading-api'
$jarPath = Join-Path $apiDirectory 'target\trading-api-0.1.0-SNAPSHOT.jar'
$process = $null
$orderId = $null

function Start-PaperApiProcess {
    param([string]$Jar, [int]$Port, [string]$BaseUrl)

    $started = Start-Process -FilePath 'java' -ArgumentList @(
        '-jar', "`"$Jar`"", "--server.port=$Port", '--server.address=127.0.0.1'
    ) -WorkingDirectory (Split-Path -Parent $Jar) -WindowStyle Hidden -PassThru
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($started.HasExited) { throw "Spring API process exited during startup (code $($started.ExitCode))." }
        try {
            Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/v1/operations/summary" -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $ready) { throw 'Spring API did not become ready within 30 seconds.' }
    return $started
}

function Stop-PaperApiProcess {
    param($RunningProcess)
    if ($null -ne $RunningProcess -and -not $RunningProcess.HasExited) {
        Stop-Process -Id $RunningProcess.Id -Force
        $RunningProcess.WaitForExit()
    }
}

try {
    Push-Location $apiDirectory
    try {
        & .\mvnw.cmd -DskipTests package
        if ($LASTEXITCODE -ne 0) { throw "Spring API package failed with exit code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $jarPath)) { throw 'Packaged Spring API jar was not found.' }

    $process = Start-PaperApiProcess -Jar $jarPath -Port $port -BaseUrl $baseUrl
    $created = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper" `
        -Headers @{ 'Idempotency-Key' = $idempotencyKey } -ContentType 'application/json' -Body $body -TimeoutSec 10
    $orderId = [long]$created.id
    if ($created.status -ne 'PENDING') { throw "Expected a pending paper order, received '$($created.status)'." }
    Stop-PaperApiProcess -RunningProcess $process
    $process = $null

    $process = Start-PaperApiProcess -Jar $jarPath -Port $port -BaseUrl $baseUrl
    $recovered = Invoke-RestMethod -Method Get `
        -Uri "$baseUrl/api/v1/orders/paper/by-idempotency/$idempotencyKey" -TimeoutSec 10
    $replayed = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper" `
        -Headers @{ 'Idempotency-Key' = $idempotencyKey } -ContentType 'application/json' -Body $body -TimeoutSec 10
    if ([long]$recovered.id -ne $orderId -or [long]$replayed.id -ne $orderId -or $recovered.status -ne 'PENDING') {
        throw 'Recovered order did not preserve its ID, state, and idempotent replay after process restart.'
    }

    Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper/$orderId/cancel" -TimeoutSec 10 | Out-Null
    $orderId = $null
    Write-Output "PASS: PAPER order $($recovered.id) survived a separate JVM process restart and idempotent replay. No KIS request was made."
} finally {
    if ($orderId) {
        try { Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper/$orderId/cancel" -TimeoutSec 3 | Out-Null } catch { }
    }
    Stop-PaperApiProcess -RunningProcess $process
    foreach ($name in $originalEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
    }
}
