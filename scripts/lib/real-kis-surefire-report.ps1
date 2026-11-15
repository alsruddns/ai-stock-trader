function Assert-SelectedSurefireTestPassed {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)] [string]$ReportPath,
        [Parameter(Mandatory = $true)] [string]$TestMethod,
        [Parameter(Mandatory = $true)] [DateTime]$RunStartedAtUtc
    )

    if (-not (Test-Path -LiteralPath $ReportPath -PathType Leaf)) {
        throw 'The selected KIS integration test report was not generated.'
    }
    $reportFile = Get-Item -LiteralPath $ReportPath
    if ($reportFile.LastWriteTimeUtc -lt $RunStartedAtUtc.ToUniversalTime()) {
        throw 'The selected KIS integration test report is stale.'
    }

    try {
        [xml]$report = Get-Content -LiteralPath $ReportPath -Raw
    } catch {
        throw 'The selected KIS integration test report is invalid.'
    }
    $testCases = @($report.testsuite.testcase | Where-Object { $_.name -eq $TestMethod })
    if ($testCases.Count -ne 1) {
        throw 'The selected KIS integration test does not appear exactly once in its report.'
    }
    $testCase = $testCases[0]
    if ($null -ne $testCase.skipped) {
        throw 'The selected KIS integration test was skipped; a fresh-quote preflight did not run.'
    }
    if ($null -ne $testCase.failure -or $null -ne $testCase.error) {
        throw 'The selected KIS integration test did not pass.'
    }
    Write-Output "Selected KIS integration test passed: $TestMethod"
}
