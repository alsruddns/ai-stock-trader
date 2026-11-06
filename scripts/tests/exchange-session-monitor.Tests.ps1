. (Join-Path $PSScriptRoot '../lib/exchange-session-monitor.ps1')

Describe 'Exchange session monitor calendar boundaries' {
    It 'tracks the US regular session across Korea midnight' {
        $session = ConvertTo-ExchangeSessionWindow @{
            market = 'US'; session_date = '2026-11-06'; is_session = $true
            open_at = '2026-11-06T09:30:00-05:00'; close_at = '2026-11-06T16:00:00-05:00'
        }
        $session.open_utc.ToString('o') | Should Be '2026-11-06T14:30:00.0000000+00:00'
        $session.close_utc.ToString('o') | Should Be '2026-11-06T21:00:00.0000000+00:00'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T23:29:59+09:00')) $session) | Should Be 'PRE_OPEN'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T23:30:00+09:00')) $session) | Should Be 'CONTINUOUS'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-07T05:59:59+09:00')) $session) | Should Be 'CONTINUOUS'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-07T06:00:00+09:00')) $session) | Should Be 'CLOSED'
        (Test-ExchangeSessionTickTimestamp ([DateTimeOffset]::Parse('2026-11-06T23:29:59+09:00')) $session) | Should Be $false
        (Test-ExchangeSessionTickTimestamp ([DateTimeOffset]::Parse('2026-11-06T23:30:00+09:00')) $session) | Should Be $true
        (Test-ExchangeSessionTickTimestamp ([DateTimeOffset]::Parse('2026-11-07T06:00:00+09:00')) $session) | Should Be $false
    }

    It 'excludes the KRX closing auction from continuous matching' {
        $session = ConvertTo-ExchangeSessionWindow @{
            market = 'KR'; session_date = '2026-11-06'; is_session = $true
            open_at = '2026-11-06T09:00:00+09:00'; close_at = '2026-11-06T15:30:00+09:00'
        }
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T09:00:00+09:00')) $session) | Should Be 'CONTINUOUS'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T15:19:59+09:00')) $session) | Should Be 'CONTINUOUS'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T15:20:00+09:00')) $session) | Should Be 'CLOSING_AUCTION'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T15:29:59+09:00')) $session) | Should Be 'CLOSING_AUCTION'
        (Get-ExchangeSessionPhase ([DateTimeOffset]::Parse('2026-11-06T15:30:00+09:00')) $session) | Should Be 'CLOSED'
        (Test-ExchangeSessionNoTickThresholdApplies 'CLOSING_AUCTION') | Should Be $false
        (Test-ExchangeSessionNoTickThresholdApplies 'CONTINUOUS') | Should Be $true
    }

    It 'rejects a non-session calendar response' {
        $didThrow = $false
        try {
            $invalidSession = @{ market = 'US'; session_date = '2026-11-07'; is_session = $false; open_at = $null; close_at = $null }
            ConvertTo-ExchangeSessionWindow $invalidSession | Out-Null
        } catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

    It 'rejects an invalid session interval' {
        $didThrow = $false
        try {
            $invalidSession = @{ market = 'US'; session_date = '2026-11-06'; is_session = $true; open_at = '2026-11-06T16:00:00Z'; close_at = '2026-11-06T09:30:00Z' }
            ConvertTo-ExchangeSessionWindow $invalidSession | Out-Null
        } catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

    It 'counts regular-session bars from the opening counter and excludes premarket bars' {
        $count = Get-ExchangeSessionBarsDelta 430 25 40
        $count.baseline | Should Be 40
        $count.delta | Should Be 390
    }

    It 'keeps the final pre-open count so the first post-open bar is counted' {
        $open = [DateTimeOffset]::Parse('2026-11-06T14:30:00Z')
        $preOpen = Update-ExchangeSessionOpenBarsBaseline $true $open.AddSeconds(-15) $open 157 157 157
        $preOpen.awaiting | Should Be $true
        $preOpen.baseline | Should Be 157

        $afterOpen = Update-ExchangeSessionOpenBarsBaseline $preOpen.awaiting $open.AddSeconds(15) $open 158 $preOpen.baseline 157
        $afterOpen.awaiting | Should Be $false
        $afterOpen.baseline | Should Be 157
        (Get-ExchangeSessionBarsDelta 547 157 $afterOpen.baseline).delta | Should Be 390
    }

    It 'accepts only a complete stored session with spread on every minute' {
        $quality = @{
            market='US'; session_date='2026-11-06'; sessions=@(@{
                market='US'; symbol='AAPL'; session_date='2026-11-06'; status='COMPLETE'
                full_session_complete=$true; expected_bars=390; completed_minutes=390
                observed_bars=390; observed_completed_bars=390; missing_bars=0
                off_grid_bars=0; duplicate_bars=0; spread_bars=390; spread_coverage_percent=100.0
            })
        }
        $verified = @(Assert-ExchangeSessionQuality $quality 'US' '2026-11-06' @('AAPL') 390)
        $verified.Count | Should Be 1
        $verified[0].status | Should Be 'COMPLETE'
        $verified[0].spread_coverage_percent | Should Be 100.0
    }

    It 'rejects a complete bar count when a stored minute is missing' {
        $quality = @{
            market='US'; session_date='2026-11-06'; sessions=@(@{
                market='US'; symbol='AAPL'; session_date='2026-11-06'; status='INCOMPLETE'
                full_session_complete=$false; expected_bars=390; completed_minutes=390
                observed_bars=389; observed_completed_bars=389; missing_bars=1
                off_grid_bars=0; duplicate_bars=0; spread_bars=389; spread_coverage_percent=100.0
            })
        }
        $didThrow = $false
        try { Assert-ExchangeSessionQuality $quality 'US' '2026-11-06' @('AAPL') 390 | Out-Null }
        catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

    It 'rejects quality for another session date or a missing symbol' {
        $wrongDate = @{ market='US'; session_date='2026-11-05'; sessions=@() }
        $noSymbol = @{ market='US'; session_date='2026-11-06'; sessions=@() }
        $dateThrew = $false
        $symbolThrew = $false
        try { Assert-ExchangeSessionQuality $wrongDate 'US' '2026-11-06' @('AAPL') 390 | Out-Null }
        catch { $dateThrew = $true }
        try { Assert-ExchangeSessionQuality $noSymbol 'US' '2026-11-06' @('AAPL') 390 | Out-Null }
        catch { $symbolThrew = $true }
        $dateThrew | Should Be $true
        $symbolThrew | Should Be $true
    }
}
