$ErrorActionPreference = 'Stop'

$restartPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'restart-local-stack-task.ps1'
$tokens = $null
$parseErrors = $null
$restartAst = [System.Management.Automation.Language.Parser]::ParseFile($restartPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw 'The local stack restart script has PowerShell syntax errors.' }

foreach ($functionName in @(
    'Get-RequiredSessionMarkets',
    'Test-SessionResponsesFreshAndClosed',
    'Test-TrackedProcessParentage',
    'Test-LocalTaskRestartable'
)) {
    $functionAst = $restartAst.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $functionName
    }, $true)
    if ($null -eq $functionAst) { throw "Restart guard '$functionName' was not found." }
    . ([ScriptBlock]::Create($functionAst.Extent.Text))
}

Describe 'Domestic local stack restart guards' {
    It 'uses only the KRX calendar when domestic-only restart is selected' {
        $markets = @(Get-RequiredSessionMarkets -DomesticOnly)
        $markets.Count | Should Be 1
        $markets[0] | Should Be 'KR'
    }

    It 'requires a fresh closed KRX session' {
        $now = [DateTimeOffset]::UtcNow
        $closed = @([pscustomobject]@{ market = 'KR'; is_open = $false; evaluated_at = $now.ToString('o') })
        $open = @([pscustomobject]@{ market = 'KR'; is_open = $true; evaluated_at = $now.ToString('o') })
        (Test-SessionResponsesFreshAndClosed $closed $now @('KR')) | Should Be $true
        (Test-SessionResponsesFreshAndClosed $open $now @('KR')) | Should Be $false
    }

    It 'allows only the recorded children when the runner has exited' {
        $backend = [pscustomobject]@{ ProcessId = 101; ParentProcessId = 100 }
        $marketAi = [pscustomobject]@{ ProcessId = 102; ParentProcessId = 100 }
        (Test-TrackedProcessParentage $null $backend $marketAi 100) | Should Be $true
        $marketAi.ParentProcessId = 999
        (Test-TrackedProcessParentage $null $backend $marketAi 100) | Should Be $false
    }

    It 'permits restart only while the scheduled task is running or ready' {
        (Test-LocalTaskRestartable 'Running') | Should Be $true
        (Test-LocalTaskRestartable 'Ready') | Should Be $true
        (Test-LocalTaskRestartable 'Disabled') | Should Be $false
    }
}
