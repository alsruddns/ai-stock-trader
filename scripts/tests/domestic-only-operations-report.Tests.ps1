Describe 'Domestic-only daily operations report market scope' {
    It 'does not query out-of-scope market endpoints' {
        . (Join-Path $PSScriptRoot '..\lib\operations-report.ps1')
        . (Join-Path $PSScriptRoot '..\lib\model-retraining-report.ps1')
        $script:requestedUris = @()
        Mock Invoke-RestMethod {
            $script:requestedUris += [string]$Uri
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper'; operationalMarkets = @('KR') } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') { return @{ decisions = @{} } }
            if ($Uri -like '*api/v1/ai/models') { return @{ models = @() } }
            if ($Uri -like '*/api/v1/market/sessions/KR') { return @{ market = 'KR' } }
            throw 'Unexpected report endpoint.'
        }
        Mock Get-ScheduledTask { @() }

        $outputPath = Join-Path $TestDrive 'domestic-only-operations-report.json'
        $retrainingDirectory = Join-Path $TestDrive 'no-retraining-runs'
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory $retrainingDirectory -DomesticSessionEvidencePath (Join-Path $TestDrive 'no-monitor-progress.jsonl') | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json
        $requestedUris = $script:requestedUris -join "`n"

        $report.endpointStatus.'python.market_session_KR'.state | Should Be 'AVAILABLE'
        $report.endpointStatus.'python.market_session_US'.state | Should Be 'OUT_OF_SCOPE'
        $report.endpointStatus.'python.market_data_quality_US'.state | Should Be 'OUT_OF_SCOPE'
        $requestedUris | Should Not Match '/api/v1/market/(sessions|session-quality)/US'
    }
}
