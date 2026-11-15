. (Join-Path $PSScriptRoot '../lib/real-kis-surefire-report.ps1')

Describe 'REAL KIS selected Surefire report verification' {
    It 'accepts only a fresh report with one passing selected test' {
        $path = Join-Path $TestDrive 'selected-test.xml'
        Set-Content -LiteralPath $path -Value '<testsuite><testcase name="preflightsDomesticRealBuyWithFreshQuoteWithoutSubmittingAnOrder" /></testsuite>'

        $threw = $false
        try {
            Assert-SelectedSurefireTestPassed -ReportPath $path `
                -TestMethod 'preflightsDomesticRealBuyWithFreshQuoteWithoutSubmittingAnOrder' `
                -RunStartedAtUtc ([DateTime]::UtcNow.AddSeconds(-2)) | Out-Null
        } catch { $threw = $true }

        $threw | Should Be $false
    }

    It 'rejects a skipped test even when Surefire has no failure' {
        $path = Join-Path $TestDrive 'skipped-test.xml'
        Set-Content -LiteralPath $path -Value '<testsuite><testcase name="target"><skipped /></testcase></testsuite>'

        $threw = $false
        try { Assert-SelectedSurefireTestPassed -ReportPath $path -TestMethod 'target' -RunStartedAtUtc ([DateTime]::UtcNow.AddSeconds(-2)) | Out-Null }
        catch { $threw = $true }

        $threw | Should Be $true
    }

    It 'rejects old reports and a missing selected test' {
        $path = Join-Path $TestDrive 'old-report.xml'
        Set-Content -LiteralPath $path -Value '<testsuite><testcase name="other" /></testsuite>'

        $staleThrew = $false
        $missingThrew = $false
        try { Assert-SelectedSurefireTestPassed -ReportPath $path -TestMethod 'target' -RunStartedAtUtc ([DateTime]::UtcNow.AddMinutes(1)) | Out-Null }
        catch { $staleThrew = $true }
        try { Assert-SelectedSurefireTestPassed -ReportPath $path -TestMethod 'target' -RunStartedAtUtc ([DateTime]::UtcNow.AddSeconds(-2)) | Out-Null }
        catch { $missingThrew = $true }

        $staleThrew | Should Be $true
        $missingThrew | Should Be $true
    }
}
