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

$fixturePath = Join-Path ([IO.Path]::GetTempPath()) ("ai-stock-trader-env-reload-{0}.env" -f [Guid]::NewGuid())
$utf8 = [Text.UTF8Encoding]::new($false)
$originalPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$originalPythonPathWasSet = $null -ne $originalPythonPath
try {
    $envPath = $fixturePath
    $script:loadedEnvironmentNames = @()
    . ([ScriptBlock]::Create($importFunction.Extent.Text))
    . ([ScriptBlock]::Create($restorePythonPathFunction.Extent.Text))

    [IO.File]::WriteAllLines($fixturePath, @(
        'POSTGRES_PASSWORD=reload-fixture'
        'KIS_PAPER_APP_KEY=first-fixture-key'
        'KIS_REAL_APP_SECRET=stale-fixture-secret'
        'TRADING_MODE=live'
        'LIVE_TRADING_ENABLED=true'
        'KIS_REAL_ORDERS_ENABLED=true'
    ), $utf8)
    $firstHash = Import-LocalEnvironment
    if ($env:KIS_PAPER_APP_KEY -ne 'first-fixture-key' -or
        -not $env:KIS_REAL_APP_SECRET -or
        $env:TRADING_MODE -ne 'paper' -or
        $env:LIVE_TRADING_ENABLED -ne 'false' -or
        $env:KIS_REAL_ORDERS_ENABLED -ne 'false') {
        throw 'Initial local environment import or trading lock assertion failed.'
    }

    [IO.File]::WriteAllLines($fixturePath, @(
        'POSTGRES_PASSWORD=reload-fixture'
        'KIS_PAPER_APP_KEY=second-fixture-key'
        'TRADING_MODE=live'
    ), $utf8)
    $secondHash = Import-LocalEnvironment
    if ($env:KIS_PAPER_APP_KEY -ne 'second-fixture-key' -or
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

    Write-Output 'Local stack .env reload and Python path restoration checks passed; no credential values were displayed.'
} finally {
    if ($originalPythonPathWasSet) {
        [Environment]::SetEnvironmentVariable('PYTHONPATH', $originalPythonPath, 'Process')
    } else {
        Remove-Item -LiteralPath Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $fixturePath -Force -ErrorAction SilentlyContinue
}
