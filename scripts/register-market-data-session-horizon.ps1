[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateRange(1, 20)]
    [int]$SessionCount = 10,
    [switch]$InstallDailyRefresh
)

$ErrorActionPreference = 'Stop'
$koreaZone = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
if ([TimeZoneInfo]::Local.Id -ne $koreaZone.Id) { throw 'Windows time zone must be Korea Standard Time (KST) for session task scheduling.' }
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot 'services\market-ai\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'The project Python environment with exchange calendar support is required.'
}
$registerScript = Join-Path $PSScriptRoot 'register-market-data-session-monitor.ps1'
$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powerShellPath -PathType Leaf)) { $powerShellPath = Join-Path $PSHOME 'pwsh.exe' }
if (-not (Test-Path -LiteralPath $powerShellPath -PathType Leaf)) { throw 'No PowerShell executable was found.' }

$calendarCode = @'
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from app.market_calendar import exchange_session_window
market, count = sys.argv[1], int(sys.argv[2])
korea = ZoneInfo("Asia/Seoul")
market_zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
now = datetime.now(korea)
day = now.date()
found = 0
while found < count and day <= now.date() + timedelta(days=60):
    window = exchange_session_window(market, datetime.combine(day, time(12), tzinfo=market_zone))
    if window is not None:
        lead = timedelta(minutes=15 if market == "KR" else 5)
        run_at = (window[0] - lead).astimezone(korea)
        if run_at > now:
            print(f"{day.isoformat()}|{run_at.isoformat()}")
            found += 1
    day += timedelta(days=1)
if found < count:
    raise SystemExit("calendar horizon did not contain enough future sessions")
'@

$sessionsByMarket = @{}
Push-Location (Join-Path $repoRoot 'services\market-ai')
try {
    foreach ($market in @('KR', 'US')) {
        $rows = @(& $pythonPath -c $calendarCode $market $SessionCount)
        if ($LASTEXITCODE -ne 0 -or $rows.Count -ne $SessionCount) {
            throw "The exchange calendar could not produce $SessionCount future $market sessions."
        }
        $sessionsByMarket[$market] = @($rows | ForEach-Object {
            $parts = ([string]$_).Split('|', 2)
            if ($parts.Count -ne 2) { throw "Invalid calendar result for $market." }
            [pscustomobject]@{ Date = $parts[0]; RunAt = [DateTimeOffset]::Parse($parts[1]) }
        })
    }
} finally {
    Pop-Location
}

$registrar = Join-Path $PSScriptRoot 'register-market-data-session-monitor.ps1'
$taskRows = @()
foreach ($market in @('KR', 'US')) {
    $feed = if ($market -eq 'KR') { 'paper' } else { 'real' }
    $taskMarket = if ($market -eq 'KR') { 'KRX' } else { 'US' }
    foreach ($session in $sessionsByMarket[$market]) {
        $dateKey = ([DateTime]::ParseExact($session.Date, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)).ToString('yyyyMMdd')
        $taskName = "AIStockTrader-$($taskMarket)-Session-Monitor-$dateKey"
        $exists = [bool](Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)
        $taskRows += [pscustomobject]@{ Market = $market; Feed = $feed; Date = $session.Date; RunAt = $session.RunAt; TaskName = $taskName; Exists = $exists }
    }
}

$toRegister = @($taskRows | Where-Object { -not $_.Exists })
Write-Output "Future sessions covered: $(@($taskRows | Where-Object {$_.Market -eq 'KR'}).Count) KR and $(@($taskRows | Where-Object {$_.Market -eq 'US'}).Count) US; adding $($toRegister.Count) missing tasks."
foreach ($row in $toRegister) {
    if ($PSCmdlet.ShouldProcess($row.TaskName, "Register read-only $($row.Market) session monitor for $($row.Date) at $($row.RunAt.ToString('HH:mm')) KST")) {
        & $registrar -Feed $row.Feed -SessionDate $row.Date -Confirm:$false
    }
}

if ($InstallDailyRefresh) {
    $refreshTaskName = 'AIStockTrader-Session-Monitor-Rolling-Registration'
    if (Get-ScheduledTask -TaskName $refreshTaskName -ErrorAction SilentlyContinue) {
        Write-Output "Daily refresh task '$refreshTaskName' already exists; it was left unchanged."
    } else {
        $scriptArguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`" -SessionCount $SessionCount"
        $action = New-ScheduledTaskAction -Execute $powerShellPath -Argument $scriptArguments -WorkingDirectory $repoRoot
        $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddMinutes(5))
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
        $description = 'Maintains a rolling horizon of read-only KIS KR PAPER and US REAL market-session monitors; no orders.'
        if ($PSCmdlet.ShouldProcess($refreshTaskName, 'Register daily 00:05 KST rolling session monitor registration')) {
            Register-ScheduledTask -TaskName $refreshTaskName -Action $action -Trigger $trigger `
                -Principal $principal -Settings $settings -Description $description | Out-Null
            Write-Output "Registered daily refresh '$refreshTaskName' at 00:05 KST."
        }
    }
}
