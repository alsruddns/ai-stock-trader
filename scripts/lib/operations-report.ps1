function Invoke-AiTraderOptionalJsonRequest {
    param([Parameter(Mandatory)][string]$Url)

    try {
        $data = Invoke-RestMethod -Uri $Url -TimeoutSec 5
        return [pscustomobject]@{
            data = $data
            state = 'AVAILABLE'
            http_status = $null
            error_type = $null
        }
    }
    catch {
        $httpStatus = $null
        try {
            if ($null -ne $_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
                $parsedStatus = [int]$_.Exception.Response.StatusCode
                if ($parsedStatus -gt 0) { $httpStatus = $parsedStatus }
            }
        } catch { }
        $state = if ($null -ne $httpStatus) { 'HTTP_ERROR' } else { 'UNREACHABLE' }
        return [pscustomobject]@{
            data = $null
            state = $state
            http_status = $httpStatus
            error_type = $_.Exception.GetType().Name
        }
    }
}

function Set-AiTraderAtomicJsonFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Value,
        [ValidateRange(1, 100)][int]$Depth = 12
    )

    $directory = Split-Path -Parent $Path
    if ([string]::IsNullOrWhiteSpace($directory) -or -not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw 'The operations report output directory must already exist.'
    }
    $temporaryPath = Join-Path $directory ('.{0}.{1}.tmp' -f [IO.Path]::GetFileName($Path), [guid]::NewGuid().ToString('N'))
    $backupPath = "$temporaryPath.bak"
    try {
        $json = $Value | ConvertTo-Json -Depth $Depth
        [IO.File]::WriteAllText($temporaryPath, $json, [Text.Encoding]::UTF8)
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [IO.File]::Replace($temporaryPath, $Path, $backupPath)
        } else {
            [IO.File]::Move($temporaryPath, $Path)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
        if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
            Remove-Item -LiteralPath $backupPath -Force
        }
    }
}

function New-AiTraderOperationsReportRunPath {
    param(
        [Parameter(Mandatory)][string]$Directory,
        [DateTimeOffset]$Timestamp = [DateTimeOffset]::UtcNow
    )

    $stamp = $Timestamp.ToUniversalTime().ToString('yyyy-MM-ddTHHmmssfffZ', [Globalization.CultureInfo]::InvariantCulture)
    $runId = [guid]::NewGuid().ToString('N').Substring(0, 8)
    return Join-Path $Directory ("operations-run-{0}-{1}.json" -f $stamp, $runId)
}

function Get-AiTraderMarketDataQualitySummary {
    param(
        [Parameter(Mandatory)]$EndpointStatus,
        [Parameter(Mandatory)]$MarketDataQuality
    )

    $qualityEndpointStates = @('KR', 'US') | ForEach-Object {
        $EndpointStatus["python.market_data_quality_$_"].state
    }
    if (@($qualityEndpointStates | Where-Object { $_ -in @('HTTP_ERROR', 'UNREACHABLE') }).Count -gt 0) {
        return [ordered]@{
            sessionCompleteness = 'UNAVAILABLE'
            spreadCoverage = 'UNAVAILABLE'
            sessionsReported = 0
            incompleteSessions = 0
            sessionsMissingSpreads = 0
        }
    }
    if (@($qualityEndpointStates | Where-Object { $_ -ne 'AVAILABLE' }).Count -gt 0) {
        return [ordered]@{
            sessionCompleteness = 'UNVERIFIED'
            spreadCoverage = 'UNVERIFIED'
            sessionsReported = 0
            incompleteSessions = 0
            sessionsMissingSpreads = 0
        }
    }

    $sessions = @('KR', 'US') | ForEach-Object {
        $marketData = $MarketDataQuality[$_]
        if ($marketData -and $marketData.sessions) { @($marketData.sessions) }
    }
    if ($sessions.Count -eq 0) {
        return [ordered]@{
            sessionCompleteness = 'UNVERIFIED'
            spreadCoverage = 'UNVERIFIED'
            sessionsReported = 0
            incompleteSessions = 0
            sessionsMissingSpreads = 0
        }
    }

    $incompleteSessions = 0
    $sessionsMissingSpreads = 0
    foreach ($session in $sessions) {
        if ($session.full_session_complete -isnot [bool] -or -not $session.full_session_complete) {
            $incompleteSessions++
        }
        $observedBars = 0
        $spreadBars = -1
        if (-not [int]::TryParse([string]$session.observed_completed_bars, [ref]$observedBars) -or
                -not [int]::TryParse([string]$session.spread_bars, [ref]$spreadBars) -or
                $observedBars -lt 1 -or $spreadBars -ne $observedBars) {
            $sessionsMissingSpreads++
        }
    }
    return [ordered]@{
        sessionCompleteness = if ($incompleteSessions -eq 0) { 'COMPLETE' } else { 'INCOMPLETE' }
        spreadCoverage = if ($sessionsMissingSpreads -eq 0) { 'COMPLETE' } else { 'INCOMPLETE' }
        sessionsReported = $sessions.Count
        incompleteSessions = $incompleteSessions
        sessionsMissingSpreads = $sessionsMissingSpreads
    }
}

function Get-AiTraderSessionMonitorScheduleSummary {
    try {
        $tasks = @(Get-ScheduledTask -ErrorAction Stop | Where-Object {
            $_.TaskName -match '^AIStockTrader-(KRX|US)-Session-Monitor-\d{8}$'
        })
    }
    catch {
        return [ordered]@{
            state = 'UNAVAILABLE'
            errorType = $_.Exception.GetType().Name
            registeredCount = 0
            krRegisteredCount = 0
            usRegisteredCount = 0
            tasks = @()
        }
    }

    $rows = @()
    foreach ($task in $tasks) {
        $market = if ($task.TaskName -like 'AIStockTrader-KRX-*') { 'KR' } else { 'US' }
        try {
            $info = Get-ScheduledTaskInfo -TaskName $task.TaskName -ErrorAction Stop
            $rows += [ordered]@{
                market = $market
                taskName = $task.TaskName
                state = [string]$task.State
                enabled = [bool]$task.Settings.Enabled
                nextRunTime = if ($info.NextRunTime -and $info.NextRunTime.Year -gt 2000) { $info.NextRunTime.ToString('o') } else { $null }
                lastRunTime = if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) { $info.LastRunTime.ToString('o') } else { $null }
                lastTaskResult = [int]$info.LastTaskResult
            }
        }
        catch {
            $rows += [ordered]@{
                market = $market
                taskName = $task.TaskName
                state = 'UNKNOWN'
                enabled = [bool]$task.Settings.Enabled
                nextRunTime = $null
                lastRunTime = $null
                lastTaskResult = $null
            }
        }
    }

    return [ordered]@{
        state = if ($rows.Count -eq 0) { 'UNVERIFIED' } elseif (@($rows | Where-Object { $_.state -eq 'UNKNOWN' }).Count -gt 0) { 'PARTIAL' } else { 'AVAILABLE' }
        errorType = $null
        registeredCount = $rows.Count
        krRegisteredCount = @($rows | Where-Object { $_.market -eq 'KR' }).Count
        usRegisteredCount = @($rows | Where-Object { $_.market -eq 'US' }).Count
        tasks = @($rows | Sort-Object market, taskName)
    }
}
