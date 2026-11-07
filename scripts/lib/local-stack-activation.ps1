function Get-LocalStackEnvironment {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'Missing local .env_local or .env file.'
    }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
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
        $values[$name] = $value
    }
    return $values
}

function Assert-LocalStackPaperLocks {
    [CmdletBinding()]
    param([Parameter(Mandatory)][hashtable]$Environment)

    foreach ($name in @('LIVE_TRADING_ENABLED', 'KIS_PAPER_ORDERS_ENABLED',
            'KIS_REAL_ORDERS_ENABLED', 'PAPER_AUTOTRADING_ENABLED')) {
        if ([string]$Environment[$name] -eq 'true') {
            throw "Local stack activation refused because $name is enabled."
        }
    }
}

function Assert-LocalStackTaskAction {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$Task,
        [Parameter(Mandatory)][string]$ExpectedRunnerPath
    )

    $matches = @($Task.Actions | Where-Object {
        [string]$_.Arguments -match [regex]::Escape($ExpectedRunnerPath)
    }).Count -gt 0
    if (-not $matches) {
        throw 'The scheduled task does not launch this repository local stack runner.'
    }
}

function Assert-LocalStackPortsFree {
    [CmdletBinding()]
    param([Parameter(Mandatory)][AllowEmptyCollection()][int[]]$OccupiedPorts)

    $occupiedPorts = @($OccupiedPorts | Where-Object { $_ -in @(18000, 18080) } | Sort-Object -Unique)
    if ($occupiedPorts.Count -gt 0) {
        throw "Local API port(s) $($occupiedPorts -join ', ') are already occupied; activation was stopped."
    }
}

function Assert-LocalStackReadyResponses {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]$PythonHealth,
        [Parameter(Mandatory)]$SpringSummary
    )

    foreach ($name in @('status', 'live_trading_enabled', 'paper_automation_enabled')) {
        if ($null -eq $PythonHealth.PSObject.Properties[$name]) {
            throw 'Python health response is missing a required safety status.'
        }
    }
    foreach ($name in @('tradingMode', 'liveTrading', 'paperAutomationEnabled', 'kisRealOrdersEnabled')) {
        if ($null -eq $SpringSummary.PSObject.Properties[$name]) {
            throw 'Spring operations response is missing a required safety status.'
        }
    }
    if ([string]$PythonHealth.status -ne 'ok' -or
        [bool]$PythonHealth.live_trading_enabled -or
        [bool]$PythonHealth.paper_automation_enabled -or
        [string]$SpringSummary.tradingMode -ne 'paper' -or
        [string]$SpringSummary.liveTrading -ne 'disabled' -or
        [bool]$SpringSummary.paperAutomationEnabled -or
        [bool]$SpringSummary.kisRealOrdersEnabled) {
        throw 'Local APIs did not confirm healthy PAPER mode with trading locks enabled.'
    }
}
