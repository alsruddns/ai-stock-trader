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

Write-Output 'Local stack restart session gate checks passed.'
