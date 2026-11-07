$ErrorActionPreference = 'Stop'

$scriptPath = Join-Path $PSScriptRoot 'activate-local-stack.ps1'
$helperPath = Join-Path $PSScriptRoot 'lib\local-stack-activation.ps1'
foreach ($path in @($scriptPath, $helperPath)) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count -gt 0) { throw "PowerShell syntax errors found in $(Split-Path $path -Leaf)." }
}
. $helperPath

$safeEnvironment = @{
    TRADING_MODE = 'paper'
    LIVE_TRADING_ENABLED = 'false'
    KIS_PAPER_ORDERS_ENABLED = 'false'
    KIS_REAL_ORDERS_ENABLED = 'false'
    PAPER_AUTOTRADING_ENABLED = 'false'
}
Assert-LocalStackPaperLocks -Environment $safeEnvironment

foreach ($switchName in @('LIVE_TRADING_ENABLED', 'KIS_PAPER_ORDERS_ENABLED',
        'KIS_REAL_ORDERS_ENABLED', 'PAPER_AUTOTRADING_ENABLED')) {
    $unsafeEnvironment = $safeEnvironment.Clone()
    $unsafeEnvironment[$switchName] = 'true'
    $rejected = $false
    try { Assert-LocalStackPaperLocks -Environment $unsafeEnvironment }
    catch { $rejected = $_.Exception.Message -match [regex]::Escape($switchName) }
    if (-not $rejected) { throw "Activation must reject $switchName=true." }
}

$expectedRunner = Join-Path $PSScriptRoot 'run-local-stack-task.ps1'
$validTask = [pscustomobject]@{ Actions = @([pscustomobject]@{ Arguments = "-File `"$expectedRunner`"" }) }
$invalidTask = [pscustomobject]@{ Actions = @([pscustomobject]@{ Arguments = '-File C:\other\run-local-stack-task.ps1' }) }
Assert-LocalStackTaskAction -Task $validTask -ExpectedRunnerPath $expectedRunner
$wrongTaskRejected = $false
try { Assert-LocalStackTaskAction -Task $invalidTask -ExpectedRunnerPath $expectedRunner }
catch { $wrongTaskRejected = $_.Exception.Message -match 'does not launch this repository' }
if (-not $wrongTaskRejected) { throw 'Activation must reject an unrelated scheduled task action.' }
Assert-LocalStackPortsFree -OccupiedPorts @()
$occupiedPortRejected = $false
try { Assert-LocalStackPortsFree -OccupiedPorts @(18080) }
catch { $occupiedPortRejected = $_.Exception.Message -match 'already occupied' }
if (-not $occupiedPortRejected) { throw 'Activation must reject an already occupied local API port.' }

$pythonHealth = [pscustomobject]@{
    status = 'ok'; live_trading_enabled = $false; paper_automation_enabled = $false
}
$springSummary = [pscustomobject]@{
    tradingMode = 'paper'; liveTrading = 'disabled'
    paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
}
Assert-LocalStackReadyResponses -PythonHealth $pythonHealth -SpringSummary $springSummary

$unsafeSummary = $springSummary.PSObject.Copy()
$unsafeSummary.kisRealOrdersEnabled = $true
$unsafeResponseRejected = $false
try { Assert-LocalStackReadyResponses -PythonHealth $pythonHealth -SpringSummary $unsafeSummary }
catch { $unsafeResponseRejected = $_.Exception.Message -match 'healthy PAPER mode' }
if (-not $unsafeResponseRejected) { throw 'Activation must reject a readiness response with REAL orders enabled.' }

$incompleteHealth = [pscustomobject]@{ status = 'ok'; live_trading_enabled = $false }
$incompleteResponseRejected = $false
try { Assert-LocalStackReadyResponses -PythonHealth $incompleteHealth -SpringSummary $springSummary }
catch { $incompleteResponseRejected = $_.Exception.Message -match 'missing a required safety status' }
if (-not $incompleteResponseRejected) { throw 'Activation must reject an incomplete health response.' }

Write-Output 'Local stack activation safety checks passed.'
