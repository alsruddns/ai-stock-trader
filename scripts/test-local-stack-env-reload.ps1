$ErrorActionPreference = 'Stop'

$runnerPath = Join-Path $PSScriptRoot 'run-local-stack-task.ps1'
$tokens = $null
$parseErrors = $null
$runnerAst = [System.Management.Automation.Language.Parser]::ParseFile($runnerPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) {
    throw 'The local stack runner has PowerShell syntax errors.'
}
$importFunction = $runnerAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Import-LocalEnvironment'
}, $true)
if ($null -eq $importFunction) {
    throw 'The local environment import function was not found.'
}
$restorePythonPathFunction = $runnerAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Restore-RunnerPythonPath'
}, $true)
if ($null -eq $restorePythonPathFunction) {
    throw 'The Python path restoration function was not found.'
}
$sessionGateFunction = $runnerAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-LocalSessionsFreshAndClosed'
}, $true)
$reloadDecisionFunction = $runnerAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-EnvironmentReloadDecision'
}, $true)
if ($null -eq $sessionGateFunction -or $null -eq $reloadDecisionFunction) {
    throw 'The environment reload session gate was not found.'
}
$operationalMarketsFunction = $runnerAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Get-OperationalMarketsFromEnvironment'
}, $true)
if ($null -eq $operationalMarketsFunction) {
    throw 'The operational market selection function was not found.'
}

$fixturePath = Join-Path ([IO.Path]::GetTempPath()) ("ai-stock-trader-env-reload-{0}.env" -f [Guid]::NewGuid())
$utf8 = [Text.UTF8Encoding]::new($false)
$originalPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$originalPythonPathWasSet = $null -ne $originalPythonPath
try {
    $envPath = $fixturePath
    $script:loadedEnvironmentNames = @()
    . ([ScriptBlock]::Create($importFunction.Extent.Text))
    . ([ScriptBlock]::Create($restorePythonPathFunction.Extent.Text))
    . ([ScriptBlock]::Create($sessionGateFunction.Extent.Text))
    . ([ScriptBlock]::Create($reloadDecisionFunction.Extent.Text))
    . ([ScriptBlock]::Create($operationalMarketsFunction.Extent.Text))

    $nowUtc = [DateTimeOffset]::UtcNow
    $closedSessions = @([pscustomobject]@{
        market = 'KR'; is_open = $false; evaluated_at = $nowUtc.ToString('o')
    })
    $openSessions = @([pscustomobject]@{
        market = 'KR'; is_open = $true; evaluated_at = $nowUtc.ToString('o')
    })
    $staleSessions = @([pscustomobject]@{
        market = 'KR'; is_open = $false; evaluated_at = $nowUtc.AddSeconds(-31).ToString('o')
    })
    if (-not (Test-LocalSessionsFreshAndClosed $closedSessions $nowUtc @('KR')) -or
        (Test-LocalSessionsFreshAndClosed $openSessions $nowUtc @('KR')) -or
        (Test-LocalSessionsFreshAndClosed $staleSessions $nowUtc @('KR'))) {
        throw 'The environment reload gate did not require a fresh closed domestic session.'
    }
    if ((@(Get-OperationalMarketsFromEnvironment @{ MARKET_OPERATIONAL_SCOPE = 'KR' }) -join ',') -ne 'KR') {
        throw 'The active domestic operational scope was not selected.'
    }
    if ((Get-EnvironmentReloadDecision $true $false) -ne 'DEFER' -or
        (Get-EnvironmentReloadDecision $true $true) -ne 'RESTART' -or
        (Get-EnvironmentReloadDecision $false $true) -ne 'NONE') {
        throw 'The environment reload decision did not defer until configured markets close.'
    }

    [IO.File]::WriteAllLines($fixturePath, @(
        'POSTGRES_PASSWORD=reload-fixture'
        'KIS_PAPER_APP_KEY=TEST_ONLY_PLACEHOLDER_FIRST'
        'KIS_REAL_APP_SECRET=TEST_ONLY_PLACEHOLDER_SECRET'
        'TRADING_MODE=live'
        'LIVE_TRADING_ENABLED=true'
        'KIS_REAL_ORDERS_ENABLED=true'
    ), $utf8)
    $firstHash = Import-LocalEnvironment
    if ($env:KIS_PAPER_APP_KEY -ne 'TEST_ONLY_PLACEHOLDER_FIRST' -or
        -not $env:KIS_REAL_APP_SECRET -or
        $env:TRADING_MODE -ne 'paper' -or
        $env:LIVE_TRADING_ENABLED -ne 'false' -or
        $env:KIS_REAL_ORDERS_ENABLED -ne 'false') {
        throw 'Initial local environment import or trading lock assertion failed.'
    }

    [IO.File]::WriteAllLines($fixturePath, @(
        'POSTGRES_PASSWORD=reload-fixture'
        'KIS_PAPER_APP_KEY=TEST_ONLY_PLACEHOLDER_SECOND'
        'TRADING_MODE=live'
    ), $utf8)
    $secondHash = Import-LocalEnvironment
    if ($env:KIS_PAPER_APP_KEY -ne 'TEST_ONLY_PLACEHOLDER_SECOND' -or
        $env:KIS_REAL_APP_SECRET -or
        $env:TRADING_MODE -ne 'paper' -or
        $env:LIVE_TRADING_ENABLED -ne 'false' -or
        $env:KIS_REAL_ORDERS_ENABLED -ne 'false' -or
        $firstHash -eq $secondHash) {
        throw 'Updated local configuration was not reloaded safely.'
    }

    $script:originalPythonPathWasSet = $true
    $script:originalPythonPath = 'C:\host\python'
    $env:PYTHONPATH = 'C:\repo\services\market-ai;C:\host\python'
    Restore-RunnerPythonPath
    if ($env:PYTHONPATH -ne 'C:\host\python') {
        throw 'Service restart retained an accumulated Python path instead of restoring its original value.'
    }

    $script:originalPythonPathWasSet = $false
    $script:originalPythonPath = $null
    $env:PYTHONPATH = 'C:\repo\services\market-ai'
    Restore-RunnerPythonPath
    if (Test-Path Env:PYTHONPATH) {
        throw 'Service restart left Python path set when the runner inherited no original value.'
    }

    Write-Output 'Local stack .env reload deferral, exchange-session gate, and Python path restoration checks passed; no credential values were displayed.'
} finally {
    if ($originalPythonPathWasSet) {
        [Environment]::SetEnvironmentVariable('PYTHONPATH', $originalPythonPath, 'Process')
    } else {
        Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $fixturePath -Force -ErrorAction SilentlyContinue
}
