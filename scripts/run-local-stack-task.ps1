$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envPath)) { $envPath = Join-Path $repoRoot '.env' }
$logDirectory = Join-Path $repoRoot '.local\logs'
$taskLog = Join-Path $logDirectory 'local-stack-task.log'
$runtimeState = Join-Path $logDirectory 'local-stack-pids.json'
$backendStdout = Join-Path $logDirectory 'spring-boot.stdout.log'
$backendStderr = Join-Path $logDirectory 'spring-boot.stderr.log'
$marketAiStdout = Join-Path $logDirectory 'market-ai.stdout.log'
$marketAiStderr = Join-Path $logDirectory 'market-ai.stderr.log'
$backendProcess = $null
$marketAiProcess = $null
$loadedEnvironmentNames = @()
$originalPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$originalPythonPathWasSet = $null -ne $originalPythonPath

function Restore-RunnerPythonPath {
    if ($script:originalPythonPathWasSet) {
        [Environment]::SetEnvironmentVariable('PYTHONPATH', $script:originalPythonPath, 'Process')
    } else {
        Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
}

function Write-TaskLog {
    param([string]$Message)
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
    Add-Content -LiteralPath $taskLog -Value "$timestamp $Message" -Encoding UTF8
}

function Write-ProcessState {
    $state = @{
        runnerProcessId = $PID
        backendProcessId = if ($null -ne $backendProcess -and -not $backendProcess.HasExited) { $backendProcess.Id } else { $null }
        marketAiProcessId = if ($null -ne $marketAiProcess -and -not $marketAiProcess.HasExited) { $marketAiProcess.Id } else { $null }
        updatedAt = (Get-Date).ToString('o')
    } | ConvertTo-Json -Compress
    $temporaryState = "$runtimeState.tmp"
    Set-Content -LiteralPath $temporaryState -Value $state -Encoding UTF8
    Move-Item -LiteralPath $temporaryState -Destination $runtimeState -Force
}

function Stop-ServiceProcess {
    param($Process)
    if ($null -ne $Process -and -not $Process.HasExited) {
        $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        if (Test-Path -LiteralPath $taskkill) {
            & $taskkill /PID $Process.Id /T /F 2>$null | Out-Null
        } else {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
        if (-not $Process.WaitForExit(10000)) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

function Import-LocalEnvironment {
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw 'Missing local .env_local or .env file.'
    }
    $environmentHashBefore = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    foreach ($name in $script:loadedEnvironmentNames) {
        [Environment]::SetEnvironmentVariable($name, $null, 'Process')
    }
    $script:loadedEnvironmentNames = @()
    foreach ($item in Get-ChildItem Env:KIS_* -ErrorAction SilentlyContinue) {
        [Environment]::SetEnvironmentVariable($item.Name, $null, 'Process')
    }
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
        $script:loadedEnvironmentNames += $name
    }
    if (-not $env:POSTGRES_PASSWORD -or $env:POSTGRES_PASSWORD -eq 'replace_with_your_local_postgres_password') {
        throw 'Local PostgreSQL password is not configured in .env_local or .env.'
    }
    $env:TRADING_MODE = 'paper'
    $env:LIVE_TRADING_ENABLED = 'false'
    $env:KIS_REAL_ORDERS_ENABLED = 'false'
    $environmentHashAfter = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
    if ($environmentHashAfter -ne $environmentHashBefore) {
        throw 'Local environment file changed while configuration was being loaded.'
    }
    return $environmentHashAfter
}

try {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Write-ProcessState

    while ($true) {
        try {
            $environmentHash = Import-LocalEnvironment
            $powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            if (-not (Test-Path -LiteralPath $powerShellPath)) { $powerShellPath = (Get-Process -Id $PID).Path }
            $backendRunner = Join-Path $PSScriptRoot 'start-local.ps1'
            $backendProcess = Start-Process -FilePath $powerShellPath `
                -ArgumentList @('-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $backendRunner, '-PaperOnly') `
                -WorkingDirectory $repoRoot -WindowStyle Hidden `
                -RedirectStandardOutput $backendStdout -RedirectStandardError $backendStderr -PassThru
            Write-TaskLog 'Spring Boot process started.'
            Write-ProcessState

            $backendReady = $false
            $backendDeadline = (Get-Date).AddMinutes(5)
            while ((Get-Date) -lt $backendDeadline) {
                if ($backendProcess.HasExited) { throw "Spring Boot exited during startup (code $($backendProcess.ExitCode))." }
                try {
                    Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/operations/summary' -TimeoutSec 2 | Out-Null
                    $backendReady = $true
                    break
                } catch {
                    Start-Sleep -Seconds 2
                }
            }
            if (-not $backendReady) { throw 'Spring Boot did not become ready within five minutes.' }

            $serviceDirectory = Join-Path $repoRoot 'services\market-ai'
            $pythonPath = Join-Path $serviceDirectory '.venv\Scripts\python.exe'
            if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Python virtual environment is missing; create it and install the market-ai dependencies.' }
            $existingPythonPath = $env:PYTHONPATH
            if ($existingPythonPath) {
                $env:PYTHONPATH = "$serviceDirectory$([IO.Path]::PathSeparator)$existingPythonPath"
            } else {
                $env:PYTHONPATH = $serviceDirectory
            }
            $marketAiProcess = Start-Process -FilePath $pythonPath `
                -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') `
                -WorkingDirectory $repoRoot -WindowStyle Hidden `
                -RedirectStandardOutput $marketAiStdout -RedirectStandardError $marketAiStderr -PassThru
            Write-TaskLog 'Python market-ai process started.'
            Write-ProcessState

            $marketAiReady = $false
            $marketAiDeadline = (Get-Date).AddMinutes(2)
            while ((Get-Date) -lt $marketAiDeadline) {
                if ($marketAiProcess.HasExited) { throw "Python market-ai exited during startup (code $($marketAiProcess.ExitCode))." }
                try {
                    Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 2 | Out-Null
                    $marketAiReady = $true
                    break
                } catch {
                    Start-Sleep -Seconds 2
                }
            }
            if (-not $marketAiReady) { throw 'Python market-ai did not become ready within two minutes.' }
            Write-TaskLog 'Spring Boot and Python market-ai are ready. Real KIS order switches are forced off.'

            while ($true) {
                Start-Sleep -Seconds 2
                $currentEnvironmentHash = (Get-FileHash -LiteralPath $envPath -Algorithm SHA256).Hash
                if ($currentEnvironmentHash -ne $environmentHash) {
                    Write-TaskLog 'Local environment file changed; restarting APIs to reload configuration.'
                    throw 'Local environment file changed.'
                }
                if ($backendProcess.HasExited) { throw "Spring Boot stopped unexpectedly (code $($backendProcess.ExitCode))." }
                if ($marketAiProcess.HasExited) { throw "Python market-ai stopped unexpectedly (code $($marketAiProcess.ExitCode))." }
            }
        } catch {
            Write-TaskLog ("FAILURE " + $_.Exception.Message)
        } finally {
            Stop-ServiceProcess -Process $marketAiProcess
            Stop-ServiceProcess -Process $backendProcess
            $marketAiProcess = $null
            $backendProcess = $null
            Restore-RunnerPythonPath
            Write-ProcessState
        }
        Write-TaskLog 'Retrying both APIs after 15 seconds.'
        Start-Sleep -Seconds 15
    }
} catch {
    Write-TaskLog ("FAILURE " + $_.Exception.Message)
    exit 1
} finally {
    Stop-ServiceProcess -Process $marketAiProcess
    Stop-ServiceProcess -Process $backendProcess
    Restore-RunnerPythonPath
    if (Test-Path -LiteralPath $runtimeState) {
        try {
            $state = Get-Content -LiteralPath $runtimeState -Raw | ConvertFrom-Json
            if ([int]$state.runnerProcessId -eq $PID) { Remove-Item -LiteralPath $runtimeState -Force }
        } catch { }
    }
}
