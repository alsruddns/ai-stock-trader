[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)] [ValidateSet('KR', 'US')] [string]$Market,
    [Parameter(Mandatory)] [string]$Symbol,
    [Parameter(Mandatory)] [ValidatePattern('^\d{4}-\d{2}-\d{2}$')] [string]$FromDate,
    [Parameter(Mandatory)] [ValidatePattern('^\d{4}-\d{2}-\d{2}$')] [string]$ToDate,
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')] [string]$RunTime = '08:15'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$symbolValue = $Symbol.Trim().ToUpperInvariant()
if ($Market -eq 'KR' -and $symbolValue -notmatch '^\d{6}$') { throw 'KR symbol must be a six-digit instrument code.' }
if ($Market -eq 'US' -and $symbolValue -notmatch '^[A-Z][A-Z0-9.-]{0,15}$') { throw 'US symbol must contain 1 to 16 letters, digits, dots, or hyphens.' }
$from = [DateTime]::ParseExact($FromDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
$to = [DateTime]::ParseExact($ToDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
if ($from.Date -gt $to.Date) { throw 'FromDate must be on or before ToDate.' }

$envFile = Join-Path $repoRoot '.env_local'
if (-not (Test-Path -LiteralPath $envFile)) { throw 'The ignored .env_local file is required.' }
$localSettings = @{}
foreach ($line in Get-Content -LiteralPath $envFile) {
    if ($line -match '^\s*([^#=]+)=(.*)$') { $localSettings[$matches[1].Trim()] = $matches[2].Trim().Trim('"').Trim("'") }
}
foreach ($required in @('TRADING_MODE', 'LIVE_TRADING_ENABLED', 'KIS_PAPER_ORDERS_ENABLED', 'KIS_REAL_ORDERS_ENABLED', 'PAPER_AUTOTRADING_ENABLED')) {
    if (-not $localSettings.ContainsKey($required)) { throw "Required local safety setting '$required' is missing." }
}
if ($localSettings.TRADING_MODE -ne 'paper' -or $localSettings.LIVE_TRADING_ENABLED -ne 'false' -or
    $localSettings.KIS_PAPER_ORDERS_ENABLED -ne 'false' -or $localSettings.KIS_REAL_ORDERS_ENABLED -ne 'false' -or
    $localSettings.PAPER_AUTOTRADING_ENABLED -ne 'false') { throw 'Model holdout evaluation requires all order and automation switches disabled.' }

$pythonPath = Join-Path $repoRoot 'services\market-ai\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'The project Python environment with exchange calendar support is required.' }
$calendarCode = 'import sys; from datetime import date, datetime, timedelta; from zoneinfo import ZoneInfo; from app.market_calendar import exchange_session_window; market=sys.argv[1]; zone=ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York"); current=date.fromisoformat(sys.argv[2]); final=date.fromisoformat(sys.argv[3]); sessions=[]; 
while current <= final:
 at=datetime.combine(current, datetime.min.time(), tzinfo=zone)+timedelta(hours=12)
 if exchange_session_window(market, at): sessions.append(current.isoformat())
 current += timedelta(days=1)
print("|".join(sessions))'
Push-Location (Join-Path $repoRoot 'services\market-ai')
try {
    $sessionDates = & $pythonPath -c $calendarCode $Market $FromDate $ToDate
    if ($LASTEXITCODE -ne 0) { throw 'The official exchange calendar could not validate the holdout range.' }
} finally { Pop-Location }
$sessionList = @(([string]$sessionDates).Split('|', [StringSplitOptions]::RemoveEmptyEntries))
if ($sessionList.Count -lt 5) { throw 'At least five official exchange sessions are required for independent model evaluation.' }

$runDate = $to.Date.AddDays(1)
$runAt = $runDate.Add([TimeSpan]::ParseExact($RunTime, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
if ($runAt -le (Get-Date)) { throw 'Holdout evaluation must be scheduled after the data range and market close.' }
$dateKey = $runDate.ToString('yyyyMMdd', [Globalization.CultureInfo]::InvariantCulture)
$taskName = "AIStockTrader-P4-Model-Holdout-$Market-$symbolValue-$dateKey"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { throw "Task '$taskName' already exists; refusing to replace it." }
$scriptPath = Join-Path $PSScriptRoot 'run-model-holdout-evaluation.ps1'
$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powerShellPath)) { $powerShellPath = Join-Path $PSHOME 'pwsh.exe' }
if (-not (Test-Path -LiteralPath $powerShellPath)) { throw 'No PowerShell executable was found.' }
$arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`" -Market $Market -Symbol $symbolValue -FromDate $FromDate -ToDate $ToDate"
$action = New-ScheduledTaskAction -Execute $powerShellPath -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Once -At $runAt
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$description = "Read-only independent $Market holdout backtest for $symbolValue over $($sessionList.Count) sessions; candidate remains unapproved and no orders."
if ($PSCmdlet.ShouldProcess($taskName, "Register read-only candidate evaluation at $runAt")) {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $description | Out-Null
    Write-Output "Registered '$taskName' for $runAt local time. The current Windows user must be signed in and PostgreSQL available."
}
