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

$fixturePath = Join-Path ([IO.Path]::GetTempPath()) ("ai-stock-trader-env-reload-{0}.env" -f [Guid]::NewGuid())
$utf8 = [Text.UTF8Encoding]::new($false)
try {
    $envPath = $fixturePath
    $script:loadedEnvironmentNames = @()
    . ([ScriptBlock]::Create($importFunction.Extent.Text))

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

    Write-Output 'Local stack .env reload checks passed; no credential values were displayed.'
} finally {
    Remove-Item -LiteralPath $fixturePath -Force -ErrorAction SilentlyContinue
}
