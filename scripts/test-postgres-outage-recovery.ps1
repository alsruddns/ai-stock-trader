$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envFile)) { $envFile = Join-Path $repoRoot '.env' }
if (-not (Test-Path -LiteralPath $envFile)) {
    throw 'Repository .env_local or .env file is missing.'
}

$pgBin = 'C:\Program Files\PostgreSQL\16\bin'
foreach ($binary in @('initdb.exe', 'pg_ctl.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $pgBin $binary))) {
        throw "PostgreSQL test binary is missing: $binary"
    }
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

$repoSafeTempRoot = [IO.Path]::GetFullPath($env:TEMP)
$clusterId = [guid]::NewGuid().ToString('N')
$clusterPath = [IO.Path]::GetFullPath((Join-Path $repoSafeTempRoot "ai-stock-trader-pg-outage-$clusterId"))
if (-not $clusterPath.StartsWith($repoSafeTempRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to create a PostgreSQL test cluster outside the temporary directory.'
}
$clusterLog = Join-Path $clusterPath 'postgres.log'
$springPort = Get-Random -Minimum 20000 -Maximum 50000
$pgListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$pgListener.Start()
$pgPort = $pgListener.LocalEndpoint.Port
$pgListener.Stop()
$baseUrl = "http://127.0.0.1:$springPort"
$databaseUrl = "jdbc:postgresql://127.0.0.1:$pgPort/ai_stock_trader_outage"
$paperOrdersLocked = $env:KIS_PAPER_ORDERS_ENABLED -ne 'true'
$realOrdersLocked = $env:KIS_REAL_ORDERS_ENABLED -ne 'true'
$liveLocked = $env:LIVE_TRADING_ENABLED -ne 'true'
$realReconciliationLocked = $env:KIS_REAL_RECONCILIATION_ENABLED -ne 'true'
if (-not ($paperOrdersLocked -and $realOrdersLocked -and $liveLocked -and $realReconciliationLocked)) {
    throw 'Database outage test requires all broker orders, live trading, and REAL reconciliation to remain disabled.'
}

$env:TRADING_MODE = 'paper'
$env:LIVE_TRADING_ENABLED = 'false'
$env:KIS_PAPER_ORDERS_ENABLED = 'false'
$env:KIS_REAL_ORDERS_ENABLED = 'false'
$env:KIS_REAL_RECONCILIATION_ENABLED = 'false'
$env:KIS_ACCOUNT_READ_ENABLED = 'false'
$env:KIS_PAPER_ACCOUNT_READ_ENABLED = 'false'
$env:KIS_REAL_ACCOUNT_READ_ENABLED = 'false'
$env:RUN_LOCAL_POSTGRES_INTEGRATION_TESTS = 'true'
$env:POSTGRES_USER = 'postgres'
$env:POSTGRES_PASSWORD = 'not-used-local-trust-auth'
$env:DATABASE_URL = $databaseUrl
$env:SPRING_DATASOURCE_HIKARI_CONNECTION_TIMEOUT = '2000'
$apiDirectory = Join-Path $repoRoot 'backend\trading-api'
$jarPath = Join-Path $apiDirectory 'target\trading-api-0.1.0-SNAPSHOT.jar'
$pgStarted = $false
$apiProcess = $null
$orderId = $null

function Start-TestDatabase {
    $arguments = @('-D', $clusterPath, '-o',
        "-h 127.0.0.1 -p $pgPort -c listen_addresses=127.0.0.1", '-l',
        $clusterLog, '-w', '-t', '30', 'start')
    & (Join-Path $pgBin 'pg_ctl.exe') @arguments
    $pgCtlExitCode = $LASTEXITCODE
    if ($pgCtlExitCode -ne 0) {
        throw 'Isolated PostgreSQL test database failed to start.'
    }
    $script:pgStarted = $true
}

function Stop-TestDatabase {
    if ($script:pgStarted) {
        $arguments = @('-D', $clusterPath, '-m', 'fast', '-w', '-t', '30', 'stop')
        & (Join-Path $pgBin 'pg_ctl.exe') @arguments
        $pgCtlExitCode = $LASTEXITCODE
        if ($pgCtlExitCode -ne 0) {
            throw 'Isolated PostgreSQL test database failed to stop cleanly.'
        }
        $script:pgStarted = $false
    }
}

function Start-PaperApi {
    $started = Start-Process -FilePath 'java' -ArgumentList @(
        '-jar', "`"$jarPath`"", "--server.port=$springPort", '--server.address=127.0.0.1'
    ) -WorkingDirectory $apiDirectory -WindowStyle Hidden -PassThru
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($started.HasExited) { throw "Spring API exited during startup (code $($started.ExitCode))." }
        try {
            Invoke-RestMethod -Method Get -Uri "$baseUrl/api/v1/operations/summary" -TimeoutSec 2 | Out-Null
            return $started
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw 'Spring API did not become ready within 30 seconds.'
}

try {
    Write-Output 'Preparing isolated temporary PostgreSQL cluster.'
    New-Item -ItemType Directory -Path $clusterPath -Force | Out-Null
    & (Join-Path $pgBin 'initdb.exe') -D $clusterPath -U postgres -A trust -E UTF8 --no-locale | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the isolated PostgreSQL test cluster.' }
    Start-TestDatabase
    Write-Output 'Creating isolated test database.'
    & (Join-Path $pgBin 'createdb.exe') -h 127.0.0.1 -p $pgPort -U postgres ai_stock_trader_outage
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the isolated test database.' }

    Push-Location $apiDirectory
    try {
        Write-Output 'Packaging Spring API.'
        & .\mvnw.cmd -DskipTests package
        if ($LASTEXITCODE -ne 0) { throw "Spring API package failed with exit code $LASTEXITCODE." }
    Write-Output 'Checking REAL risk baseline and fill journal migrations in the isolated database.'
    & .\mvnw.cmd '-Dtest=KisRealDailyRiskBaselinePostgresIntegrationTest,KisRealFillSnapshotPostgresIntegrationTest' test
    if ($LASTEXITCODE -ne 0) { throw "REAL risk baseline or fill journal PostgreSQL integration failed with exit code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $jarPath)) { throw 'Packaged Spring API jar was not found.' }

    Write-Output 'Starting Spring API against the isolated database.'
    $apiProcess = Start-PaperApi
    $key = [guid]::NewGuid().ToString()
    $body = @{
        market = 'KR'
        symbol = '005930'
        side = 'BUY'
        quantity = 1
        limitPrice = 1
        currency = 'KRW'
    } | ConvertTo-Json -Compress
    $created = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper" `
        -Headers @{ 'Idempotency-Key' = $key } -ContentType 'application/json' -Body $body -TimeoutSec 10
    $orderId = [long]$created.id
    if ($created.status -ne 'PENDING') { throw 'The test PAPER order was not persisted as pending.' }

    $amendmentKey = [guid]::NewGuid().ToString()
    $amendmentBody = @{ quantity = 2; limitPrice = 2 } | ConvertTo-Json -Compress
    $amended = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper/$orderId/amend" `
        -Headers @{ 'Idempotency-Key' = $amendmentKey } -ContentType 'application/json' `
        -Body $amendmentBody -TimeoutSec 10
    if ([long]$amended.id -ne $orderId -or [int]$amended.quantity -ne 2 -or
        [decimal]$amended.limitPrice -ne 2 -or $amended.status -ne 'PENDING') {
        throw 'The PAPER order amendment did not persist the new quantity and limit price.'
    }
    $amendedRetry = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper/$orderId/amend" `
        -Headers @{ 'Idempotency-Key' = $amendmentKey } -ContentType 'application/json' `
        -Body $amendmentBody -TimeoutSec 10
    if ([int]$amendedRetry.quantity -ne 2 -or [decimal]$amendedRetry.limitPrice -ne 2) {
        throw 'An identical PAPER amendment retry did not return the amended order.'
    }
    try {
        Invoke-WebRequest -Method Post -Uri "$baseUrl/api/v1/orders/paper/$orderId/amend" `
            -Headers @{ 'Idempotency-Key' = $amendmentKey } -ContentType 'application/json' `
            -Body (@{ quantity = 3; limitPrice = 3 } | ConvertTo-Json -Compress) -TimeoutSec 10 | Out-Null
        throw 'An amendment key unexpectedly accepted different values.'
    } catch {
        $statusCode = 0
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        if ($statusCode -ne 409) { throw "Conflicting amendment did not return HTTP 409 (observed status $statusCode)." }
    }

    Write-Output 'Stopping isolated PostgreSQL to simulate an outage.'
    Stop-TestDatabase
    $outageResponse = $null
    try {
        $outageResponse = Invoke-WebRequest -Method Get `
            -Uri "$baseUrl/api/v1/orders/paper/by-idempotency/$key" -TimeoutSec 8
        throw 'An order read unexpectedly succeeded while the isolated database was stopped.'
    } catch {
        $statusCode = 0
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        if ($statusCode -eq 503) {
            $safeBody = $_.ErrorDetails.Message
            if ($safeBody -match '(?i)password|jdbc:|postgres|driver|stack trace') {
                throw 'Database outage response leaked infrastructure details.'
            }
        } else {
            throw "Database outage did not return HTTP 503 (observed status $statusCode)."
        }
    }

    Write-Output 'Restarting isolated PostgreSQL and checking order recovery.'
    Start-TestDatabase
    $recovered = Invoke-RestMethod -Method Get `
        -Uri "$baseUrl/api/v1/orders/paper/by-idempotency/$key" -TimeoutSec 10
    if ([long]$recovered.id -ne $orderId -or $recovered.status -ne 'PENDING' -or
        [int]$recovered.quantity -ne 2 -or [decimal]$recovered.limitPrice -ne 2) {
        throw 'Persisted PAPER order and its amendment did not recover after PostgreSQL restarted.'
    }
    Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper/$orderId/cancel" -TimeoutSec 10 | Out-Null
    $orderId = $null
    Write-Output 'PASS: Isolated PostgreSQL applied migrations, persisted an idempotent PAPER amendment, rejected conflicting reuse, returned safe HTTP 503 during outage, and recovered the amended order after restart. No KIS request was made.'
} finally {
    if ($orderId -and $apiProcess -and -not $apiProcess.HasExited) {
        try { Invoke-RestMethod -Method Post -Uri "$baseUrl/api/v1/orders/paper/$orderId/cancel" -TimeoutSec 3 | Out-Null } catch { }
    }
    if ($apiProcess -and -not $apiProcess.HasExited) {
        Stop-Process -Id $apiProcess.Id -Force
        $apiProcess.WaitForExit()
    }
    Stop-TestDatabase
    if (Test-Path -LiteralPath $clusterPath) {
        $resolvedTemp = [IO.Path]::GetFullPath($repoSafeTempRoot)
        $resolvedCluster = [IO.Path]::GetFullPath($clusterPath)
        if ($resolvedCluster.StartsWith($resolvedTemp + [IO.Path]::DirectorySeparatorChar,
                [StringComparison]::OrdinalIgnoreCase) -and
            (Split-Path -Leaf $resolvedCluster) -eq "ai-stock-trader-pg-outage-$clusterId") {
            Remove-Item -LiteralPath $resolvedCluster -Recurse -Force
        }
    }
    foreach ($name in $originalEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
    }
}
