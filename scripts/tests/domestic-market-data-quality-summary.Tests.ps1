. (Join-Path $PSScriptRoot '..\lib\operations-report.ps1')

Describe 'Domestic market data quality report summary' {
    It 'evaluates the domestic session while ignoring the out-of-scope US endpoint' {
        $endpointStatus = @{
            'python.market_data_quality_KR' = @{ state = 'AVAILABLE' }
            'python.market_data_quality_US' = @{ state = 'OUT_OF_SCOPE' }
        }
        $quality = @{
            KR = @{ sessions = @(@{ full_session_complete = $false; observed_completed_bars = 302; spread_bars = 302 }) }
            US = $null
        }

        $summary = Get-AiTraderMarketDataQualitySummary -EndpointStatus $endpointStatus -MarketDataQuality $quality -OperationalMarkets @('KR')

        $summary.sessionCompleteness | Should Be 'INCOMPLETE'
        $summary.spreadCoverage | Should Be 'COMPLETE'
        $summary.sessionsReported | Should Be 1
        $summary.incompleteSessions | Should Be 1
        $summary.sessionsMissingSpreads | Should Be 0
    }

    It 'fails closed when the domestic quality endpoint is unavailable' {
        $endpointStatus = @{
            'python.market_data_quality_KR' = @{ state = 'UNREACHABLE' }
            'python.market_data_quality_US' = @{ state = 'OUT_OF_SCOPE' }
        }
        $quality = @{ KR = $null; US = $null }

        $summary = Get-AiTraderMarketDataQualitySummary -EndpointStatus $endpointStatus -MarketDataQuality $quality -OperationalMarkets @('KR')

        $summary.sessionCompleteness | Should Be 'UNAVAILABLE'
        $summary.spreadCoverage | Should Be 'UNAVAILABLE'
        $summary.sessionsReported | Should Be 0
    }
}
