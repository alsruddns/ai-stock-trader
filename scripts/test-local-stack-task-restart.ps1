$ErrorActionPreference = 'Stop'

$restartPath = Join-Path $PSScriptRoot 'restart-local-stack-task.ps1'
$tokens = $null
$parseErrors = $null
$restartAst = [System.Management.Automation.Language.Parser]::ParseFile($restartPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw 'The local stack restart script has PowerShell syntax errors.' }
$gateAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-SessionResponsesFreshAndClosed'
}, $true)
if ($null -eq $gateAst) { throw 'The closed-session restart gate was not found.' }
. ([ScriptBlock]::Create($gateAst.Extent.Text))
$identityAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-TrackedProcessIdentity'
}, $true)
$parentageAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-TrackedProcessParentage'
}, $true)
if ($null -eq $identityAst -or $null -eq $parentageAst) { throw 'Process identity checks were not found.' }
$repoRoot = Split-Path -Parent $PSScriptRoot
$script:restartScriptDirectory = $PSScriptRoot
. ([ScriptBlock]::Create($identityAst.Extent.Text))
. ([ScriptBlock]::Create($parentageAst.Extent.Text))

$now = [DateTimeOffset]::UtcNow
$closedSessions = @(
    [pscustomobject]@{ market = 'KR'; is_open = $false; evaluated_at = $now.ToString('o') }
    [pscustomobject]@{ market = 'US'; is_open = $false; evaluated_at = $now.ToString('o') }
)
$openUs = @($closedSessions[0], [pscustomobject]@{ market = 'US'; is_open = $true; evaluated_at = $now.ToString('o') })
$staleUs = @($closedSessions[0], [pscustomobject]@{ market = 'US'; is_open = $false; evaluated_at = $now.AddSeconds(-31).ToString('o') })
$futureKr = @([pscustomobject]@{ market = 'KR'; is_open = $false; evaluated_at = $now.AddSeconds(11).ToString('o') }, $closedSessions[1])

if (-not (Test-SessionResponsesFreshAndClosed $closedSessions $now)) { throw 'Fresh closed-session responses should allow restart.' }
if (Test-SessionResponsesFreshAndClosed $openUs $now) { throw 'An open exchange must block restart.' }
if (Test-SessionResponsesFreshAndClosed $staleUs $now) { throw 'A stale exchange response must block restart.' }
if (Test-SessionResponsesFreshAndClosed $futureKr $now) { throw 'A future-dated exchange response must block restart.' }
if (Test-SessionResponsesFreshAndClosed @($closedSessions[0]) $now) { throw 'A missing exchange response must block restart.' }

$runnerProcess = [pscustomobject]@{
    CommandLine = "powershell.exe -File `"$(Join-Path $PSScriptRoot 'run-local-stack-task.ps1')`""
    ExecutablePath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    ProcessId = 100
    ParentProcessId = 1
}
$backendProcess = [pscustomobject]@{
    CommandLine = "powershell.exe -File `"$(Join-Path $PSScriptRoot 'start-local.ps1')`""
    ExecutablePath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    ProcessId = 101
    ParentProcessId = 100
}
$marketAiProcess = [pscustomobject]@{
    CommandLine = 'python.exe -m uvicorn app.main:app --host 127.0.0.1'
    ExecutablePath = Join-Path $repoRoot 'services\market-ai\.venv\Scripts\python.exe'
    ProcessId = 102
    ParentProcessId = 100
}
if (-not (Test-TrackedProcessIdentity $runnerProcess 'runner')) { throw 'Expected repository runner process was rejected.' }
if (-not (Test-TrackedProcessIdentity $backendProcess 'backend')) { throw 'Expected repository backend process was rejected.' }
if (-not (Test-TrackedProcessIdentity $marketAiProcess 'market-ai')) { throw 'Expected repository Python process was rejected.' }
$wrongPython = $marketAiProcess.PSObject.Copy()
$wrongPython.ExecutablePath = 'C:\other\python.exe'
if (Test-TrackedProcessIdentity $wrongPython 'market-ai') { throw 'Python from another path must be rejected.' }
$unrelatedRunner = $runnerProcess.PSObject.Copy()
$unrelatedRunner.CommandLine = 'powershell.exe -File C:\other\run-local-stack-task.ps1'
if (Test-TrackedProcessIdentity $unrelatedRunner 'runner') { throw 'Runner from another repository must be rejected.' }
if (-not (Test-TrackedProcessParentage $runnerProcess $backendProcess $marketAiProcess)) { throw 'Expected process parentage was rejected.' }
$orphanBackend = $backendProcess.PSObject.Copy()
$orphanBackend.ParentProcessId = 999
if (Test-TrackedProcessParentage $runnerProcess $orphanBackend $marketAiProcess) { throw 'Unexpected process parentage must be rejected.' }

Write-Output 'Local stack restart session and process ownership checks passed.'
