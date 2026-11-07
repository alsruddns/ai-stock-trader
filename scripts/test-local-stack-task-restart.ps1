$ErrorActionPreference = 'Stop'

$startLocalPath = Join-Path $PSScriptRoot 'start-local.ps1'
if (-not (Select-String -Path $startLocalPath -Pattern 'lib\\local-api-binding\.ps1' -Quiet)) {
    throw 'The local Spring launcher must apply the loopback-only API binding.'
}
$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile($startLocalPath, [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count -gt 0) { throw 'The local Spring launcher has PowerShell syntax errors.' }
$postgresBindingPath = Join-Path $PSScriptRoot 'lib\local-postgres-binding.ps1'
if (-not (Select-String -Path $startLocalPath -Pattern 'lib\\local-postgres-binding\.ps1' -Quiet)) {
    throw 'The local Spring launcher must enforce PostgreSQL loopback binding.'
}
$tokens = $null
$parseErrors = $null
$postgresBindingAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $postgresBindingPath, [ref]$tokens, [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) { throw 'The PostgreSQL loopback binding helper has PowerShell syntax errors.' }
. $postgresBindingPath
Assert-LocalPostgresBinding -ListenerAddresses @('127.0.0.1', '::1')
foreach ($unsafeAddress in @('0.0.0.0', '::', '192.168.1.20')) {
    $blocked = $false
    try { Assert-LocalPostgresBinding -ListenerAddresses @($unsafeAddress) }
    catch { $blocked = $_.Exception.Message -match 'non-loopback address' }
    if (-not $blocked) { throw "PostgreSQL listener address $unsafeAddress must be rejected." }
}
$missingPostgres = $false
try { Assert-LocalPostgresBinding -ListenerAddresses @() }
catch { $missingPostgres = $_.Exception.Message -match 'No PostgreSQL listener' }
if (-not $missingPostgres) { throw 'A missing PostgreSQL listener must be rejected.' }
$invalidPort = $false
try { Assert-LocalPostgresBinding -Port 0 -ListenerAddresses @('127.0.0.1') }
catch { $invalidPort = $true }
if (-not $invalidPort) { throw 'An invalid PostgreSQL port must be rejected.' }
$bindingPath = Join-Path $PSScriptRoot 'lib\local-api-binding.ps1'
$hadServerAddress = Test-Path Env:SERVER_ADDRESS
$previousServerAddress = $env:SERVER_ADDRESS
try {
    $env:SERVER_ADDRESS = '0.0.0.0'
    . $bindingPath
    if ($env:SERVER_ADDRESS -ne '127.0.0.1') {
        throw 'An external server bind address must be overridden by the local binding policy.'
    }
} finally {
    if ($hadServerAddress) { $env:SERVER_ADDRESS = $previousServerAddress }
    else { Remove-Item Env:SERVER_ADDRESS -ErrorAction SilentlyContinue }
}

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
$paperSessionAst = $restartAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Test-PaperSessionRestored'
}, $true)
if ($null -eq $identityAst -or $null -eq $parentageAst -or $null -eq $paperSessionAst) {
    throw 'Process ownership or paper-session restoration checks were not found.'
}
$repoRoot = Split-Path -Parent $PSScriptRoot
$script:restartScriptDirectory = $PSScriptRoot
. ([ScriptBlock]::Create($identityAst.Extent.Text))
. ([ScriptBlock]::Create($parentageAst.Extent.Text))
. ([ScriptBlock]::Create($paperSessionAst.Extent.Text))

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

$previousPaperSession = [pscustomobject]@{
    enabled = $true; state = 'RUNNING'; orders_enabled = $false
    automation_state = 'DISABLED'; bar_storage_state = 'RUNNING'
}
$restoredPaperSession = $previousPaperSession.PSObject.Copy()
if (-not (Test-PaperSessionRestored $restoredPaperSession $previousPaperSession)) {
    throw 'A restored paper session monitor and bar store should pass.'
}
$stoppedPaperSession = $restoredPaperSession.PSObject.Copy()
$stoppedPaperSession.state = 'STOPPED'
if (Test-PaperSessionRestored $stoppedPaperSession $previousPaperSession) {
    throw 'An enabled monitor that failed to restart must be rejected.'
}
$missingStorage = $restoredPaperSession.PSObject.Copy()
$missingStorage.bar_storage_state = 'ERROR'
if (Test-PaperSessionRestored $missingStorage $previousPaperSession) {
    throw 'A previously running bar store that failed to restart must be rejected.'
}
$ordersEnabledSession = $restoredPaperSession.PSObject.Copy()
$ordersEnabledSession.orders_enabled = $true
if (Test-PaperSessionRestored $ordersEnabledSession $previousPaperSession) {
    throw 'Paper order submission must remain disabled after restart.'
}
$automationEnabledSession = $restoredPaperSession.PSObject.Copy()
$automationEnabledSession.automation_state = 'RUNNING'
if (Test-PaperSessionRestored $automationEnabledSession $previousPaperSession) {
    throw 'Paper automation must remain disabled after restart.'
}
$disabledMonitor = [pscustomobject]@{
    enabled = $false; state = 'DISABLED'; orders_enabled = $false
    automation_state = 'DISABLED'; bar_storage_state = 'DISABLED'
}
if (-not (Test-PaperSessionRestored $disabledMonitor $disabledMonitor)) {
    throw 'An intentionally disabled monitor should not block API restart.'
}

Write-Output 'Local stack restart session, process ownership, and monitor recovery checks passed.'
