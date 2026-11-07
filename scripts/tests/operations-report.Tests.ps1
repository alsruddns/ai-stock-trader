. (Join-Path $PSScriptRoot '..\lib\operations-report.ps1')
. (Join-Path $PSScriptRoot '..\lib\model-retraining-report.ps1')

Describe 'Optional operations report endpoint diagnostics' {
    It 'marks a successful endpoint as available and retains its JSON data' {
        Mock Invoke-RestMethod { return @{ status = 'ok' } }

        $result = Invoke-AiTraderOptionalJsonRequest 'http://127.0.0.1:18000/api/v1/health'

        $result.state | Should Be 'AVAILABLE'
        $result.data.status | Should Be 'ok'
        $result.http_status | Should Be $null
        $result.error_type | Should Be $null
    }

    It 'records a safe failure class without retaining exception text' {
        Mock Invoke-RestMethod { throw [System.Net.WebException]::new('secret-bearing exception detail') }

        $result = Invoke-AiTraderOptionalJsonRequest 'http://127.0.0.1:18000/api/v1/health'
        $serialized = $result | ConvertTo-Json -Compress

        $result.state | Should Be 'UNREACHABLE'
        $result.error_type | Should Be 'WebException'
        $serialized | Should Not Match 'secret-bearing exception detail'
    }

    It 'atomically replaces an existing JSON report without leaving a temporary file' {
        $directory = Join-Path $TestDrive 'atomic-report'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $path = Join-Path $directory 'operations.json'
        Set-Content -LiteralPath $path -Value '{"version":1}' -Encoding utf8

        Set-AiTraderAtomicJsonFile -Path $path -Value ([pscustomobject]@{ version = 2; state = 'COMPLETE' })

        $report = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $report.version | Should Be 2
        $report.state | Should Be 'COMPLETE'
        @(Get-ChildItem -LiteralPath $directory -Filter '*.tmp').Count | Should Be 0
    }
}

Describe 'Daily operations report endpoint health summary' {
    It 'marks intentionally skipped market quality endpoints as incomplete' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') { return @{ decisions = @{} } }
            if ($Uri -like '*api/v1/ai/models') { return @{ models = @() } }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            throw 'Unexpected report endpoint.'
        }

        $outputPath = Join-Path $TestDrive 'operations-report.json'
        $retrainingDirectory = Join-Path $TestDrive 'no-retraining-runs'
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory $retrainingDirectory | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json

        $report.reportSchemaVersion | Should Be 5
        $report.pythonConnected | Should Be $true
        $report.optionalDataStatus | Should Be 'INCOMPLETE'
        $report.marketDataQualitySummary.sessionCompleteness | Should Be 'UNVERIFIED'
        $report.marketDataQualitySummary.spreadCoverage | Should Be 'UNVERIFIED'
        $report.endpointStatus.'python.market_data_quality_KR'.state | Should Be 'SKIPPED_NO_SYMBOLS'
        $report.endpointStatus.'local.model_retraining'.state | Should Be 'NO_RUNS'
    }

    It 'separates available endpoints from incomplete market bars and spread coverage' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') {
                return @{ decisions = @{ kr = @{ market = 'KR'; symbol = '005930' }; us = @{ market = 'US'; symbol = 'AAPL' } } }
            }
            if ($Uri -like '*api/v1/ai/models') { return @{ models = @() } }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            if ($Uri -like '*session-quality/KR*') {
                return @{ market = 'KR'; sessions = @(@{ full_session_complete = $false; observed_completed_bars = 302; spread_bars = 302 }) }
            }
            if ($Uri -like '*session-quality/US*') {
                return @{ market = 'US'; sessions = @(@{ full_session_complete = $true; observed_completed_bars = 390; spread_bars = 300 }) }
            }
            throw 'Unexpected report endpoint.'
        }

        $outputPath = Join-Path $TestDrive 'market-quality-operations-report.json'
        $retrainingDirectory = Join-Path $TestDrive 'no-retraining-runs'
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory $retrainingDirectory | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json

        $report.optionalDataStatus | Should Be 'INCOMPLETE'
        $report.endpointStatus.'python.market_data_quality_KR'.state | Should Be 'AVAILABLE'
        $report.marketDataQualitySummary.sessionCompleteness | Should Be 'INCOMPLETE'
        $report.marketDataQualitySummary.spreadCoverage | Should Be 'INCOMPLETE'
        $report.marketDataQualitySummary.sessionsReported | Should Be 2
        $report.marketDataQualitySummary.incompleteSessions | Should Be 1
        $report.marketDataQualitySummary.sessionsMissingSpreads | Should Be 1
    }

    It 'fails closed when the session completeness field is not a JSON boolean' {
        $endpointStatus = @{
            'python.market_data_quality_KR' = @{ state = 'AVAILABLE' }
            'python.market_data_quality_US' = @{ state = 'AVAILABLE' }
        }
        $quality = @{
            KR = @{ sessions = @(@{ full_session_complete = 'false'; observed_completed_bars = 10; spread_bars = 10 }) }
            US = @{ sessions = @(@{ full_session_complete = $true; observed_completed_bars = 10; spread_bars = 10 }) }
        }

        $summary = Get-AiTraderMarketDataQualitySummary -EndpointStatus $endpointStatus -MarketDataQuality $quality

        $summary.sessionCompleteness | Should Be 'INCOMPLETE'
        $summary.spreadCoverage | Should Be 'COMPLETE'
    }

    It 'marks an optional Python endpoint failure as partial and omits its error message' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') { return @{ decisions = @{} } }
            if ($Uri -like '*api/v1/ai/models') { throw [System.Net.WebException]::new('secret-bearing exception detail') }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            throw 'Unexpected report endpoint.'
        }

        $outputPath = Join-Path $TestDrive 'partial-operations-report.json'
        $retrainingDirectory = Join-Path $TestDrive 'no-retraining-runs'
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory $retrainingDirectory | Out-Null
        $json = Get-Content -LiteralPath $outputPath -Raw
        $report = $json | ConvertFrom-Json

        $report.optionalDataStatus | Should Be 'PARTIAL'
        $report.endpointStatus.'python.ai_models'.state | Should Be 'UNREACHABLE'
        $report.endpointStatus.'python.ai_models'.errorType | Should Be 'WebException'
        $json | Should Not Match 'secret-bearing exception detail'
    }

    It 'includes the latest model retraining outcome in the local operations report' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') { return @{ decisions = @{} } }
            if ($Uri -like '*api/v1/ai/models') { return @{ models = @() } }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            throw 'Unexpected report endpoint.'
        }

        $outputPath = Join-Path $TestDrive 'retraining-operations-report.json'
        $retrainingDirectory = Join-Path $TestDrive 'training-run'
        New-Item -ItemType Directory -Path $retrainingDirectory | Out-Null
        Write-AiTraderModelRetrainingFailureReport `
            -Path (Join-Path $retrainingDirectory 'latest.json') `
            -RunId 'us-aapl-20261107-cccccccccccc' -FailureStage 'candidate_training' `
            -Market 'US' -Symbol 'AAPL' -FromDate '2026-11-02' -ToDate '2026-11-06' `
            -DatasetPath 'data/model-candidates/test.csv' -Exception ([System.ArgumentException]::new('private diagnostic'))
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory $retrainingDirectory | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json

        $report.modelRetraining.latestRun.status | Should Be 'training_failed'
        $report.modelRetraining.latestRun.failureStage | Should Be 'candidate_training'
        $report.endpointStatus.'local.model_retraining'.errorType | Should Be 'ArgumentException'
        $json = Get-Content -LiteralPath $outputPath -Raw
        $json | Should Not Match 'private diagnostic'
    }
}
