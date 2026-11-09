. (Join-Path $PSScriptRoot '../lib/exchange-session-monitor.ps1')
. (Join-Path $PSScriptRoot '../lib/market-session-scheduler.ps1')

Describe 'Domestic KRX session monitoring' {
    It 'excludes the closing auction from continuous KRX matching' {
        $session = ConvertTo-ExchangeSessionWindow @{
            market = 'KR'; session_date = '2026-11-06'; is_session = $true
            open_at = '2026-11-06T09:00:00+09:00'; close_at = '2026-11-06T15:30:00+09:00'
        }

        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T09:00:00+09:00')) $session) | Should Be 'CONTINUOUS'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T15:19:59+09:00')) $session) | Should Be 'CONTINUOUS'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T15:20:00+09:00')) $session) | Should Be 'CLOSING_AUCTION'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T15:30:00+09:00')) $session) | Should Be 'CLOSED'
    }

    It 'accepts a complete spread-covered domestic PAPER KIS session' {
        $quality = @{ market='KR'; session_date='2026-11-06'; sessions=@(@{
            market='KR'; symbol='005930'; session_date='2026-11-06'; status='COMPLETE'
            full_session_complete=$true; expected_bars=380; completed_minutes=380
            observed_bars=380; observed_completed_bars=380; missing_bars=0
            off_grid_bars=0; duplicate_bars=0; spread_bars=380; spread_coverage_percent=100.0
            source_counts=[pscustomobject]@{ KIS_PAPER_WEBSOCKET = 380 }
        }) }

        $verified = @(Assert-ExchangeSessionQuality $quality 'KR' '2026-11-06' @('005930') 380 'KIS_PAPER_WEBSOCKET')

        $verified.Count | Should Be 1
        $verified[0].status | Should Be 'COMPLETE'
        $verified[0].spread_coverage_percent | Should Be 100.0
        $verified[0].source_counts.KIS_PAPER_WEBSOCKET | Should Be 380
    }

    It 'rejects a domestic session with a missing minute or spread' {
        $quality = @{ market='KR'; session_date='2026-11-06'; sessions=@(@{
            market='KR'; symbol='005930'; session_date='2026-11-06'; status='INCOMPLETE'
            full_session_complete=$false; expected_bars=380; completed_minutes=379
            observed_bars=379; observed_completed_bars=379; missing_bars=1
            off_grid_bars=0; duplicate_bars=0; spread_bars=378; spread_coverage_percent=99.74
            source_counts=[pscustomobject]@{ KIS_PAPER_WEBSOCKET = 379 }
        }) }

        $didThrow = $false
        try { Assert-ExchangeSessionQuality $quality 'KR' '2026-11-06' @('005930') 380 'KIS_PAPER_WEBSOCKET' | Out-Null }
        catch { $didThrow = $true }

        $didThrow | Should Be $true
    }

    It 'starts the KRX monitor at 08:45 KST and selects domestic markets by default' {
        $start = Get-ExchangeSessionMonitorStart 'KR' ([DateTimeOffset]::Parse('2026-11-09T00:00:00Z'))

        $start.ToString('yyyy-MM-dd HH:mm zzz') | Should Be '2026-11-09 08:45 +09:00'
        (@(Get-SessionMonitorMarkets) -join ',') | Should Be 'KR'
    }
}
