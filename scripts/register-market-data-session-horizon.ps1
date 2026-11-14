[CmdletBinding(SupportsShouldProcess)]
param(
    [ValidateRange(1, 20)]
    [int]$SessionCount = 10,
    [switch]$IncludeUS,
    [switch]$InstallDailyRefresh
)

$ErrorActionPreference = 'Stop'
if ($IncludeUS) {
    throw 'Current market-session validation scope is domestic KR only. No US monitoring tasks were registered.'
}
$schedulerLibrary = Join-Path $PSScriptRoot 'lib\market-session-scheduler.ps1'
. $schedulerLibrary
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
            print(day.isoformat() + "|" + run_at.isoformat())
            found += 1
    day += timedelta(days=1)
if found < count:
    raise SystemExit("calendar horizon did not contain enough future sessions")
'@
$calendarEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($calendarCode))
$calendarBootstrap = "import base64;exec(base64.b64decode('$calendarEncoded'))"
$markets = @(Get-SessionMonitorMarkets -IncludeUS:$IncludeUS)

$sessionsByMarket = @{}
Push-Location (Join-Path $repoRoot 'services\market-ai')
try {
    foreach ($market in $markets) {
        $rows = @(& $pythonPath -c $calendarBootstrap $market $SessionCount)
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
$postSessionRegistrar = Join-Path $PSScriptRoot 'register-post-session-refresh-task.ps1'
$postSessionPowerShellPath = Get-PostSessionRefreshPowerShellPath
$postSessionWorkingDirectory = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$monitorPath = Join-Path $PSScriptRoot 'test-paper-websocket-monitor.ps1'
$taskRows = @()
foreach ($market in @('KR', 'US')) {
    $feed = if ($market -eq 'KR') { 'paper' } else { 'real' }
    $taskMarket = if ($market -eq 'KR') { 'KRX' } else { 'US' }
    foreach ($session in $sessionsByMarket[$market]) {
        $dateKey = ([DateTime]::ParseExact($session.Date, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)).ToString('yyyyMMdd')
        $taskName = "AIStockTrader-$($taskMarket)-Session-Monitor-$dateKey"
        $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($existingTask -and -not (Test-SessionMonitorTaskDefinition -Task $existingTask `
                -MonitorPath $monitorPath -Feed $feed -ExpectedRunAt $session.RunAt)) {
            throw "Existing task '$taskName' does not match the required enabled, read-only KST monitor definition; refusing to replace or count it."
        }
        $taskRows += [pscustomobject]@{ Market = $market; Feed = $feed; Date = $session.Date; RunAt = $session.RunAt; TaskName = $taskName; ExistingTask = $existingTask }
    }
}

$toRegister = @($taskRows | Where-Object { $null -eq $_.ExistingTask })
Write-Output "Future sessions covered: $(@($taskRows | Where-Object {$_.Market -eq 'KR'}).Count) KR and $(@($taskRows | Where-Object {$_.Market -eq 'US'}).Count) US; adding $($toRegister.Count) missing tasks."
foreach ($row in $toRegister) {
    if ($PSCmdlet.ShouldProcess($row.TaskName, "Register read-only $($row.Market) session monitor for $($row.Date) at $($row.RunAt.ToString('HH:mm')) KST")) {
        & $registrar -Feed $row.Feed -SessionDate $row.Date
    }
}

$postSessionRows = @($taskRows | Where-Object { $_.Market -eq 'KR' } | ForEach-Object {
    $dateKey = ([DateTime]::ParseExact($_.Date, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)).ToString('yyyyMMdd')
    $taskName = "AIStockTrader-PostSessionRefresh-$dateKey"
    $expectedRunAt = Get-KrxPostSessionRefreshTime $_.Date
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask -and -not (Test-PostSessionRefreshTaskDefinition `
            -Task $existingTask -RestartPath (Join-Path $PSScriptRoot 'restart-local-stack-task.ps1') `
            -PowerShellPath $postSessionPowerShellPath -WorkingDirectory $postSessionWorkingDirectory `
            -ExpectedRunAt $expectedRunAt)) {
        throw "Existing task '$taskName' does not match the safe domestic post-session refresh definition; refusing to replace it."
    }
    [pscustomobject]@{
        Date = $_.Date; RunAt = $expectedRunAt; TaskName = $taskName; ExistingTask = $existingTask
    }
})
$postSessionToRegister = @($postSessionRows | Where-Object { $null -eq $_.ExistingTask })
Write-Output "Domestic post-session refreshes covered: $($postSessionRows.Count); adding $($postSessionToRegister.Count) missing tasks."
foreach ($row in $postSessionToRegister) {
    if ($PSCmdlet.ShouldProcess($row.TaskName, "Register domestic closed-session API refresh for $($row.Date) at $($row.RunAt.ToString('HH:mm')) KST")) {
        & $postSessionRegistrar -SessionDate $row.Date
    }
}

if ($InstallDailyRefresh) {
    $refreshTaskName = 'AIStockTrader-Session-Monitor-Rolling-Registration'
    $existingRefreshTask = Get-ScheduledTask -TaskName $refreshTaskName -ErrorAction SilentlyContinue
    if ($existingRefreshTask) {
        if (-not (Test-SessionMonitorRefreshTaskDefinition -Task $existingRefreshTask `
                -RegistrarPath $PSCommandPath -SessionCount $SessionCount -IncludeUS:$IncludeUS)) {
            throw "Existing task '$refreshTaskName' does not match the required daily 00:05 KST registration definition; refusing to replace it."
        }
        Write-Output "Verified existing daily refresh task '$refreshTaskName'; it was left unchanged."
    } else {
        $includeUSArgument = if ($IncludeUS) { ' -IncludeUS' } else { '' }
        $scriptArguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$PSCommandPath`" -SessionCount $SessionCount$includeUSArgument"
        $action = New-ScheduledTaskAction -Execute $powerShellPath -Argument $scriptArguments -WorkingDirectory $repoRoot
        $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddMinutes(5))
        $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
        $description = if ($IncludeUS) {
            'Maintains a rolling horizon of read-only KIS KR PAPER and US REAL market-session monitors; no orders.'
        } else {
            'Maintains a rolling horizon of read-only KIS KR PAPER market-session monitors; US is excluded; no orders.'
        }
        if ($PSCmdlet.ShouldProcess($refreshTaskName, 'Register daily 00:05 KST rolling session monitor registration')) {
            Register-ScheduledTask -TaskName $refreshTaskName -Action $action -Trigger $trigger `
                -Principal $principal -Settings $settings -Description $description | Out-Null
            Write-Output "Registered daily refresh '$refreshTaskName' at 00:05 KST."
        }
    }
}
