[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$SessionDate,
    [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
    [string]$RunTime = '15:45'
)

$ErrorActionPreference = 'Stop'
$koreaZone = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
if ([TimeZoneInfo]::Local.Id -ne $koreaZone.Id) {
    throw 'Windows time zone must be Korea Standard Time (KST) for post-session refresh scheduling.'
}
. (Join-Path $PSScriptRoot 'lib\market-session-scheduler.ps1')
. (Join-Path $PSScriptRoot 'lib\exchange-session-monitor.ps1')
$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot 'services\market-ai\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'The project Python environment with exchange calendar support is required.'
}

$calendarCode = 'import sys; from datetime import date, datetime, time; from zoneinfo import ZoneInfo; from app.market_calendar import exchange_session_window; d=date.fromisoformat(sys.argv[1]); z=ZoneInfo("Asia/Seoul"); w=exchange_session_window("KR", datetime.combine(d, time(12), tzinfo=z)); print("CLOSED" if w is None else w[0].isoformat()+"|"+w[1].isoformat())'
$calendarEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($calendarCode))
$calendarBootstrap = "import base64;exec(base64.b64decode('$calendarEncoded'))"
Push-Location (Join-Path $repoRoot 'services\market-ai')
try {
    $calendarResult = & $pythonPath -c $calendarBootstrap $SessionDate
    if ($LASTEXITCODE -ne 0 -or $calendarResult -eq 'CLOSED' -or
        [string]::IsNullOrWhiteSpace([string]$calendarResult)) {
        throw "The official KRX calendar did not confirm a session on $SessionDate."
    }
} finally {
    Pop-Location
}
$calendarParts = ([string]$calendarResult).Split('|', 2)
if ($calendarParts.Count -ne 2) { throw 'The official KRX calendar returned an invalid session window.' }
$openUtc = [DateTimeOffset]::Parse($calendarParts[0], [Globalization.CultureInfo]::InvariantCulture)
$closeUtc = [DateTimeOffset]::Parse($calendarParts[1], [Globalization.CultureInfo]::InvariantCulture)
$closeKst = [TimeZoneInfo]::ConvertTime($closeUtc, $koreaZone)
$sessionLocalDate = [DateTime]::ParseExact(
    $SessionDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture
)
$runAt = Get-KrxPostSessionRefreshTime $SessionDate $RunTime
$runLocal = $runAt.DateTime
if ($runAt -lt $closeKst.AddMinutes(15)) {
    throw 'Post-session refresh must start at least 15 minutes after the verified KRX close.'
}
if ($runAt -le [DateTimeOffset]::Now) { throw 'The post-session refresh time must still be in the future.' }

$dateKey = $sessionLocalDate.ToString('yyyyMMdd', [Globalization.CultureInfo]::InvariantCulture)
$monitorTaskName = "AIStockTrader-KRX-Session-Monitor-$dateKey"
$monitorTask = Get-ScheduledTask -TaskName $monitorTaskName -ErrorAction SilentlyContinue
$monitorPath = Join-Path $PSScriptRoot 'test-paper-websocket-monitor.ps1'
$expectedMonitorAt = Get-ExchangeSessionMonitorStart 'KR' $openUtc
if (-not (Test-SessionMonitorTaskDefinition -Task $monitorTask -MonitorPath $monitorPath `
        -Feed 'paper' -ExpectedRunAt $expectedMonitorAt) -or
        [string]$monitorTask.Actions[0].Arguments -notmatch '-MinimumBarsDelta\s+380(?:\s|$)') {
    throw "The exact full-session domestic PAPER monitor '$monitorTaskName' must be registered before its refresh task."
}

$taskName = "AIStockTrader-PostSessionRefresh-$dateKey"
$restartPath = Join-Path $PSScriptRoot 'restart-local-stack-task.ps1'
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    if (-not (Test-PostSessionRefreshTaskDefinition -Task $existingTask `
            -RestartPath $restartPath -ExpectedRunAt $runAt)) {
        throw "Existing task '$taskName' does not match the safe domestic post-session refresh definition; refusing to replace it."
    }
    Write-Output "Verified existing '$taskName' at $runAt; no task changes were made."
    return
}

$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powerShellPath -PathType Leaf)) { $powerShellPath = Join-Path $PSHOME 'pwsh.exe' }
if (-not (Test-Path -LiteralPath $powerShellPath -PathType Leaf)) { throw 'No PowerShell executable was found.' }
$arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$restartPath`" -DomesticOnly"
$action = New-ScheduledTaskAction -Execute $powerShellPath -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Once -At $runLocal
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew
$description = "Restarts the local domestic-only Spring and Python APIs after the verified KRX session; no trading requests."
if ($PSCmdlet.ShouldProcess($taskName, "Register domestic post-session API refresh at $runAt")) {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description $description | Out-Null
    Write-Output "Registered '$taskName' for $runAt. The current Windows user must be signed in and the local APIs must be running."
}
