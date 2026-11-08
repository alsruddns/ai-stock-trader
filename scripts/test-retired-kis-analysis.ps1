[CmdletBinding()]
param([string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot))

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib/retired-kis-analysis-scan.ps1')
$findings = @(Get-RetiredKisDomesticAnalysisFindings -RepositoryRoot $RepositoryRoot)
if ($findings.Count -gt 0) {
    foreach ($finding in $findings) {
        Write-Error ("Retired KIS domestic analysis API reference {0} at {1}:{2}; use an independently approved data source." -f `
            $finding.Function, $finding.Path, $finding.Line)
    }
    throw 'Retired KIS domestic analysis API calls are not allowed in runtime source.'
}
Write-Output 'Retired KIS domestic analysis runtime scan passed; no deprecated API calls were found.'
