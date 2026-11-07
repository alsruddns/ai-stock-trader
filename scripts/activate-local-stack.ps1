[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$TaskName = 'AIStockTrader-LocalStack',
    [ValidateRange(10, 300)][int]$StartupTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envPath)) { $envPath = Join-Path $repoRoot '.env' }
. (Join-Path $PSScriptRoot 'lib\local-postgres-binding.ps1')
. (Join-Path $PSScriptRoot 'lib\local-stack-activation.ps1')

$environment = Get-LocalStackEnvironment -Path $envPath
Assert-LocalStackPaperLocks -Environment $environment
$port = 5432
if (-not [string]::IsNullOrWhiteSpace([string]$environment.POSTGRES_PORT)) {
    $parsedPort = 0
    if (-not [int]::TryParse([string]$environment.POSTGRES_PORT, [ref]$parsedPort) -or
        $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        throw 'POSTGRES_PORT must be an integer between 1 and 65535.'
    }
    $port = $parsedPort
}
Assert-LocalPostgresBinding -Port $port

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) { throw "Scheduled task '$TaskName' is not registered. Run scripts/install-local-stack-task.ps1 first." }
$runnerPath = Join-Path $PSScriptRoot 'run-local-stack-task.ps1'
Assert-LocalStackTaskAction -Task $task -ExpectedRunnerPath $runnerPath
if ($task.State -ne 'Running') {
    $occupiedPorts = @(Get-NetTCPConnection -State Listen -LocalPort @(8000, 8080) -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty LocalPort -Unique)
    Assert-LocalStackPortsFree -OccupiedPorts $occupiedPorts
}

if ($PSCmdlet.ShouldProcess("PAPER-locked local APIs in task '$TaskName'", 'Enable and start local Spring Boot and FastAPI services')) {
    if ($task.State -eq 'Disabled') { Enable-ScheduledTask -TaskName $TaskName | Out-Null }
    $task = Get-ScheduledTask -TaskName $TaskName
    if ($task.State -ne 'Running') { Start-ScheduledTask -TaskName $TaskName }
} else {
    return
}

$deadline = [DateTimeOffset]::UtcNow.AddSeconds($StartupTimeoutSeconds)
while ([DateTimeOffset]::UtcNow -lt $deadline) {
    $pythonHealth = $null
    $springSummary = $null
    try { $pythonHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/health' -TimeoutSec 3 } catch { }
    try { $springSummary = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/operations/summary' -TimeoutSec 3 } catch { }
    if ($pythonHealth -and $springSummary) {
        Assert-LocalStackReadyResponses -PythonHealth $pythonHealth -SpringSummary $springSummary
        Write-Output 'Local Spring Boot and FastAPI are healthy on loopback; PAPER mode is active and order/automation locks are confirmed.'
        return
    }
    Start-Sleep -Seconds 2
}

$lastResult = (Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue).LastTaskResult
throw "Local stack did not become healthy within $StartupTimeoutSeconds seconds (task result $lastResult). Inspect sanitized .local\logs without sharing credentials."
