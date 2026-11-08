[CmdletBinding()]
param([string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot))

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib/tracked-credential-scan.ps1')
$findings = @(Get-KisTrackedCredentialFindings -RepositoryRoot $RepositoryRoot)
if ($findings.Count -gt 0) {
    foreach ($finding in $findings) {
        Write-Error ("Potential committed credential at {0}:{1} ({2}); value suppressed." -f `
            $finding.Path, $finding.Line, $finding.Name)
    }
    throw 'Tracked KIS credential scan failed.'
}
Write-Output 'Tracked KIS credential scan passed; no credential values were printed.'
