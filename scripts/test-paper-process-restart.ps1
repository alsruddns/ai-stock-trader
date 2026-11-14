$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$originalEnvironment = @{}
foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $originalEnvironment[[string]$entry.Key] = [string]$entry.Value
}
$pgCandidates = @()
if ($env:PGBIN) { $pgCandidates += $env:PGBIN }
$pgCandidates += @('C:\Program Files\PostgreSQL\16\bin', 'C:\Program Files\PostgreSQL\17\bin')
$pgBin = $pgCandidates | Select-Object -Unique | Where-Object {
    (Test-Path -LiteralPath (Join-Path $_ 'initdb.exe')) -and
    (Test-Path -LiteralPath (Join-Path $_ 'pg_ctl.exe')) -and
    (Test-Path -LiteralPath (Join-Path $_ 'createdb.exe'))
} | Select-Object -First 1
if (-not $pgBin) { throw 'PostgreSQL 16/17 binaries for the isolated restart test were not found.' }
foreach ($binary in @('initdb.exe', 'pg_ctl.exe', 'createdb.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $pgBin $binary))) {
        throw "Isolated PostgreSQL test binary is missing: $binary"
    }
}
$repoSafeTempRoot = [IO.Path]::GetFullPath($env:TEMP)
$clusterId = [guid]::NewGuid().ToString('N')
$clusterPath = [IO.Path]::GetFullPath((Join-Path $repoSafeTempRoot "ai-stock-trader-paper-restart-$clusterId"))
if (-not $clusterPath.StartsWith($repoSafeTempRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to create an isolated PostgreSQL test cluster outside the temporary directory.'
}
$clusterLog = Join-Path $clusterPath 'postgres.log'
$pgListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$pgListener.Start()
$pgPort = $pgListener.LocalEndpoint.Port
$pgListener.Stop()
$databaseUrl = "jdbc:postgresql://127.0.0.1:$pgPort/ai_stock_trader_paper_restart"
$pgStarted = $false
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

function Start-TestDatabase {
    $arguments = @('-D', $clusterPath, '-o',
        "-h 127.0.0.1 -p $pgPort -c listen_addresses=127.0.0.1", '-l',
        $clusterLog, '-w', '-t', '30', 'start')
    & (Join-Path $pgBin 'pg_ctl.exe') @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Isolated PostgreSQL test database failed to start.' }
    $script:pgStarted = $true
}

function Stop-TestDatabase {
    if ($script:pgStarted) {
        & (Join-Path $pgBin 'pg_ctl.exe') -D $clusterPath -m fast -w -t 30 stop
        if ($LASTEXITCODE -ne 0) { throw 'Isolated PostgreSQL test database failed to stop cleanly.' }
        $script:pgStarted = $false
    }
}

try {
    foreach ($item in @(Get-ChildItem Env:KIS_* -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath "Env:$($item.Name)" -ErrorAction SilentlyContinue
    }
    $env:TRADING_MODE = 'paper'
    $env:LIVE_TRADING_ENABLED = 'false'
    $env:KIS_REAL_ORDERS_ENABLED = 'false'
    $env:KIS_PAPER_ORDERS_ENABLED = 'false'
    $env:KIS_ACCOUNT_READ_ENABLED = 'false'
    $env:KIS_PAPER_ACCOUNT_READ_ENABLED = 'false'
    $env:KIS_REAL_ACCOUNT_READ_ENABLED = 'false'
    $env:KIS_REAL_RECONCILIATION_ENABLED = 'false'
    $env:KIS_MARKET_DATA_ENABLED = 'false'
    $env:PAPER_AUTOMATION_ENABLED = 'false'
    $env:POSTGRES_USER = 'postgres'
    $env:POSTGRES_PASSWORD = 'isolated-test-not-used-with-trust-auth'
    $env:DATABASE_URL = $databaseUrl
    $env:SERVER_ADDRESS = '127.0.0.1'
    New-Item -ItemType Directory -Path $clusterPath -Force | Out-Null
    & (Join-Path $pgBin 'initdb.exe') -D $clusterPath -U postgres -A trust -E UTF8 --no-locale | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the isolated PostgreSQL test cluster.' }
    Start-TestDatabase
    & (Join-Path $pgBin 'createdb.exe') -h 127.0.0.1 -p $pgPort -U postgres ai_stock_trader_paper_restart
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated PAPER restart database.' }
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
    try {
        if ($orderId) {
            try { Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper/$orderId/cancel" -TimeoutSec 3 | Out-Null } catch { }
        }
        Stop-PaperApiProcess -RunningProcess $process
        Stop-TestDatabase
        if ($clusterPath -and (Test-Path -LiteralPath $clusterPath)) {
            $resolvedTemp = [IO.Path]::GetFullPath($repoSafeTempRoot)
            $resolvedCluster = [IO.Path]::GetFullPath($clusterPath)
            if ($resolvedCluster.StartsWith($resolvedTemp + [IO.Path]::DirectorySeparatorChar,
                    [StringComparison]::OrdinalIgnoreCase) -and
                (Split-Path -Leaf $resolvedCluster) -eq "ai-stock-trader-paper-restart-$clusterId") {
                Remove-Item -LiteralPath $resolvedCluster -Recurse -Force
            }
        }
    } finally {
        $currentEnvironment = [Environment]::GetEnvironmentVariables('Process')
        foreach ($name in $currentEnvironment.Keys) {
            if (-not $originalEnvironment.ContainsKey([string]$name)) {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
            }
        }
        foreach ($name in $originalEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
        }
    }
}
