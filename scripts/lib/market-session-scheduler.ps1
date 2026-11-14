function Get-SessionMonitorDurationSeconds {
    return 25200
}

function Get-SessionMonitorMarkets {
    [CmdletBinding()]
    param([switch]$IncludeUS)

    if ($IncludeUS) { return @('KR', 'US') }
    return @('KR')
}

function Test-SessionMonitorTaskDefinition {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$Task,
        [Parameter(Mandatory)][string]$MonitorPath,
        [Parameter(Mandatory)][ValidateSet('paper', 'real')][string]$Feed,
        [Parameter(Mandatory)][DateTimeOffset]$ExpectedRunAt,
        [ValidateRange(1, 36000)][int]$ExpectedDurationSeconds = (Get-SessionMonitorDurationSeconds)
    )

    if (-not $Task -or $Task.State -eq 'Disabled' -or
        $Task.Principal.RunLevel -ne 'Limited' -or $Task.Principal.LogonType -ne 'Interactive' -or
        -not [bool]$Task.Settings.WakeToRun -or -not [bool]$Task.Settings.StartWhenAvailable -or
        $Task.Settings.MultipleInstances -ne 'IgnoreNew' -or
        [int]$Task.Settings.RestartCount -ne 4 -or
        [string]$Task.Settings.RestartInterval -ne 'PT1M' -or
        [string]$Task.Settings.ExecutionTimeLimit -ne 'PT8H' -or
        @($Task.Actions).Count -ne 1 -or @($Task.Triggers).Count -ne 1) { return $false }

    $action = $Task.Actions[0]
    $trigger = $Task.Triggers[0]
    if (-not [bool]$trigger.Enabled -or $trigger.CimClass.CimClassName -ne 'MSFT_TaskTimeTrigger' -or
        [string]$action.Arguments -notmatch [regex]::Escape($MonitorPath) -or
        [string]$action.Arguments -notmatch "-Feed\s+$Feed(?:\s|$)" -or
        [string]$action.Arguments -notmatch "-DurationSeconds\s+$ExpectedDurationSeconds(?:\s|$)" -or
        [string]$action.Arguments -notmatch "-ExpectedSessionDate\s+$([regex]::Escape($ExpectedRunAt.ToOffset([TimeSpan]::FromHours(9)).ToString('yyyy-MM-dd')))(?:\s|$)" -or
        [string]$action.Arguments -notmatch '-RequireSessionClose(?:\s|$)') { return $false }

    try {
        $scheduledAt = [DateTimeOffset]::Parse([string]$trigger.StartBoundary).ToUniversalTime()
        $expectedAt = $ExpectedRunAt.ToUniversalTime()
        return $scheduledAt -eq $expectedAt
    } catch {
        return $false
    }
}

function Test-SessionMonitorRefreshTaskDefinition {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$Task,
        [Parameter(Mandatory)][string]$RegistrarPath,
        [Parameter(Mandatory)][ValidateRange(1, 20)][int]$SessionCount,
        [switch]$IncludeUS
    )

    if (-not $Task -or $Task.State -eq 'Disabled' -or
        $Task.Principal.RunLevel -ne 'Limited' -or $Task.Principal.LogonType -ne 'Interactive' -or
        -not [bool]$Task.Settings.WakeToRun -or -not [bool]$Task.Settings.StartWhenAvailable -or
        $Task.Settings.MultipleInstances -ne 'IgnoreNew' -or
        @($Task.Actions).Count -ne 1 -or @($Task.Triggers).Count -ne 1) { return $false }

    $action = $Task.Actions[0]
    $trigger = $Task.Triggers[0]
    if (-not [bool]$trigger.Enabled -or $trigger.CimClass.CimClassName -ne 'MSFT_TaskDailyTrigger' -or
        [int]$trigger.DaysInterval -ne 1 -or
        [string]$action.Arguments -notmatch [regex]::Escape($RegistrarPath) -or
        [string]$action.Arguments -notmatch "-SessionCount\s+$SessionCount(?:\s|$)") { return $false }
    $taskIncludesUS = [string]$action.Arguments -match '(?:^|\s)-IncludeUS(?:\s|$)'
    if ($taskIncludesUS -ne [bool]$IncludeUS) { return $false }

    try {
        $scheduledAt = [DateTimeOffset]::Parse([string]$trigger.StartBoundary)
        return $scheduledAt.Hour -eq 0 -and $scheduledAt.Minute -eq 5
    } catch {
        return $false
    }
}

function Test-PostSessionRefreshTaskDefinition {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$Task,
        [Parameter(Mandatory)][string]$RestartPath,
        [Parameter(Mandatory)][string]$PowerShellPath,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][DateTimeOffset]$ExpectedRunAt
    )

    if (-not $Task -or $Task.State -ne 'Ready' -or
        $Task.Principal.RunLevel -ne 'Limited' -or $Task.Principal.LogonType -ne 'Interactive' -or
        -not [bool]$Task.Settings.WakeToRun -or [bool]$Task.Settings.StartWhenAvailable -or
        [bool]$Task.Settings.DisallowStartIfOnBatteries -or [bool]$Task.Settings.StopIfGoingOnBatteries -or
        $Task.Settings.MultipleInstances -ne 'IgnoreNew' -or
        [string]$Task.Settings.ExecutionTimeLimit -ne 'PT5M' -or
        @($Task.Actions).Count -ne 1 -or @($Task.Triggers).Count -ne 1) { return $false }

    $action = $Task.Actions[0]
    $trigger = $Task.Triggers[0]
    try {
        $actualPowerShellPath = [IO.Path]::GetFullPath([string]$action.Execute)
        $expectedPowerShellPath = [IO.Path]::GetFullPath($PowerShellPath)
        $actualWorkingDirectory = [IO.Path]::GetFullPath([string]$action.WorkingDirectory)
        $expectedWorkingDirectory = [IO.Path]::GetFullPath($WorkingDirectory)
        $expectedArguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$RestartPath`" -DomesticOnly"
    } catch {
        return $false
    }

    if (-not [bool]$trigger.Enabled -or $trigger.CimClass.CimClassName -ne 'MSFT_TaskTimeTrigger' -or
        -not [string]::Equals($actualPowerShellPath, $expectedPowerShellPath, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals($actualWorkingDirectory, $expectedWorkingDirectory, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([string]$action.Arguments, $expectedArguments, [StringComparison]::OrdinalIgnoreCase)) { return $false }

    try {
        $scheduledAt = [DateTimeOffset]::Parse([string]$trigger.StartBoundary).ToUniversalTime()
        return $scheduledAt -eq $ExpectedRunAt.ToUniversalTime()
    } catch {
        return $false
    }
}

function Get-PostSessionRefreshPowerShellPath {
    $powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $powerShellPath -PathType Leaf)) { $powerShellPath = Join-Path $PSHOME 'pwsh.exe' }
    if (-not (Test-Path -LiteralPath $powerShellPath -PathType Leaf)) { throw 'No PowerShell executable was found.' }
    return [IO.Path]::GetFullPath($powerShellPath)
}

function Get-KrxPostSessionRefreshTime([string]$SessionDate, [string]$RunTime = '15:45') {
    try {
        $runLocal = [DateTime]::ParseExact(
            "$SessionDate $RunTime", 'yyyy-MM-dd HH:mm', [Globalization.CultureInfo]::InvariantCulture
        )
        $koreaZone = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
        return [DateTimeOffset]::new($runLocal, $koreaZone.GetUtcOffset($runLocal))
    } catch {
        throw 'Korean post-session refresh date or time is invalid.'
    }
}
