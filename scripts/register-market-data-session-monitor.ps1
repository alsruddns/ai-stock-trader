[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidateSet('paper', 'real')]
    [string]$Feed,
    [Parameter(Mandatory)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$SessionDate
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$market = if ($Feed -eq 'paper') { 'KR' } else { 'US' }
$taskMarket = if ($Feed -eq 'paper') { 'KRX' } else { 'US' }
$expectedBars = if ($Feed -eq 'paper') { 380 } else { 390 }
$runTime = if ($Feed -eq 'paper') { '08:45' } else { '23:25' }
$parsedDate = [DateTime]::ParseExact($SessionDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$runAt = $parsedDate.Add([TimeSpan]::ParseExact($runTime, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
if ($runAt -le (Get-Date)) { throw 'The session monitor start time must still be in the future.' }

$pythonPath = Join-Path $repoRoot 'services\market-ai\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'The project Python environment with exchange calendar support is required.' }
$calendarCode = 'import sys; from datetime import datetime, timedelta; from zoneinfo import ZoneInfo; from app.market_calendar import exchange_session_window; market=sys.argv[1]; zone=ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York"); at=datetime.fromisoformat(sys.argv[2]).replace(tzinfo=zone)+timedelta(hours=12); print("SESSION" if exchange_session_window(market, at) else "CLOSED")'
Push-Location (Join-Path $repoRoot 'services\market-ai')
try {
    $calendarResult = & $pythonPath -c $calendarCode $market $SessionDate
    if ($LASTEXITCODE -ne 0 -or $calendarResult -ne 'SESSION') {
        throw "The official exchange calendar did not confirm a $market session on $SessionDate."
    }
} finally {
    Pop-Location
}

$dateKey = $parsedDate.ToString('yyyyMMdd', [Globalization.CultureInfo]::InvariantCulture)
$taskName = "AIStockTrader-$($taskMarket)-Session-Monitor-$dateKey"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "Task '$taskName' already exists; refusing to replace it."
}
$evidenceName = if ($Feed -eq 'paper') { 'paper-websocket-samples.jsonl' } else { 'us-websocket-samples.jsonl' }
$evidencePath = Join-Path $repoRoot ".local\validation\$evidenceName"
$monitorPath = Join-Path $PSScriptRoot 'test-paper-websocket-monitor.ps1'
$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powerShellPath)) { $powerShellPath = Join-Path $PSHOME 'pwsh.exe' }
if (-not (Test-Path -LiteralPath $powerShellPath)) { throw 'No PowerShell executable was found.' }
$arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$monitorPath`" -Feed $Feed -DurationSeconds 24300 -PollIntervalSeconds 15 -MaxNoTickSeconds 300 -MinimumBarsDelta $expectedBars -EvidencePath `"$evidencePath`" -RequireSessionClose"
$action = New-ScheduledTaskAction -Execute $powerShellPath -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Once -At $runAt
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 8) -MultipleInstances IgnoreNew
$description = "Read-only KIS $market session data monitor; verifies $expectedBars closed bars and market close; no orders."

if ($PSCmdlet.ShouldProcess($taskName, "Register read-only session monitor for $SessionDate at $runTime local time")) {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description $description | Out-Null
    Write-Output "Registered '$taskName' for $runAt local time. The current Windows user must be signed in and the local APIs must be running."
}
