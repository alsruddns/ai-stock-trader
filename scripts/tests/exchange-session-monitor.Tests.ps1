. (Join-Path $PSScriptRoot '../lib/exchange-session-monitor.ps1')
. (Join-Path $PSScriptRoot '../lib/market-session-scheduler.ps1')

Describe 'Exchange session monitor calendar boundaries' {
    It 'requires the scheduled date to match the current KST and exchange session date' {
        Assert-ExpectedExchangeSessionDate '2026-11-16' '2026-11-16' 'test' | Should Be $null

        $didThrow = $false
        try { Assert-ExpectedExchangeSessionDate '2026-11-16' '2026-11-17' 'test' }
        catch { $didThrow = $true }
        $didThrow | Should Be $true

        $didThrow = $false
        try { Assert-ExpectedExchangeSessionDate '2026-11-16' 'not-a-date' 'test' }
        catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

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

    It 'waits for stored session quality when the live counter remains one bar behind' {
        $probeState = [pscustomobject]@{ calls = 0 }
        $probe = {
            $probeState.calls++
            [pscustomobject]@{ ready = ($probeState.calls -ge 3); bars_delta = 389 }
        }.GetNewClosure()
        $noSleep = { param([int]$Seconds) }

        $result = Wait-ForExchangeSessionFinalization 10 1 $probe $noSleep

        $result.ready | Should Be $true
        $result.result.bars_delta | Should Be 389
        $probeState.calls | Should Be 3
    }

    It 'retries a temporary status endpoint failure during post-close finalization' {
        $probeState = [pscustomobject]@{ calls = 0 }
        $probe = {
            $probeState.calls++
            if ($probeState.calls -eq 1) {
                [pscustomobject]@{ ready = $false; status_available = $false }
            } else {
                [pscustomobject]@{ ready = $true; status_available = $true }
            }
        }.GetNewClosure()
        $noSleep = { param([int]$Seconds) }

        $result = Wait-ForExchangeSessionFinalization 10 1 $probe $noSleep

        $result.ready | Should Be $true
        $result.result.status_available | Should Be $true
        $probeState.calls | Should Be 2
    }

    It 'keeps a 389-bar session incomplete instead of accepting it at close' {
        $probe = { [pscustomobject]@{ ready = $false; bars_delta = 389 } }

        $result = Wait-ForExchangeSessionFinalization 0 1 $probe

        $result.ready | Should Be $false
        $result.result.bars_delta | Should Be 389
    }

    It 'requires stored quality for full sessions and counter minimums for shorter samples' {
        (Test-ExchangeSessionCompletion 389 390 $true $true 0 300 $true) | Should Be $true
        (Test-ExchangeSessionCompletion 389 390 $true $true 0 300 $false) | Should Be $false
        (Test-ExchangeSessionCompletion 389 390 $true $false 0 300 $true) | Should Be $false
        (Test-ExchangeSessionCompletion 389 390 $false $true) | Should Be $false
        (Test-ExchangeSessionCompletion 390 390 $false $false) | Should Be $true
    }

    It 'accepts only a complete stored session with spread on every minute' {
        $quality = @{
            market='US'; session_date='2026-11-06'; sessions=@(@{
                market='US'; symbol='AAPL'; session_date='2026-11-06'; status='COMPLETE'
                full_session_complete=$true; expected_bars=390; completed_minutes=390
                observed_bars=390; observed_completed_bars=390; missing_bars=0
                off_grid_bars=0; duplicate_bars=0; spread_bars=390; spread_coverage_percent=100.0
                source_counts=[pscustomobject]@{ KIS_REAL_US_WEBSOCKET = 390 }
            })
        }
        $verified = @(Assert-ExchangeSessionQuality $quality 'US' '2026-11-06' @('AAPL') 390 'KIS_REAL_US_WEBSOCKET')
        $verified.Count | Should Be 1
        $verified[0].status | Should Be 'COMPLETE'
        $verified[0].spread_coverage_percent | Should Be 100.0
        $verified[0].source_counts.KIS_REAL_US_WEBSOCKET | Should Be 390
    }

    It 'returns only sanitized completeness, spread, and KIS source counts for failed sessions' {
        $quality = @{ market='KR'; session_date='2026-11-09'; sessions=@(@{
            market='KR'; symbol='005930'; session_date='2026-11-09'; status='COMPLETE'
            full_session_complete=$true; expected_bars=380; completed_minutes=380
            observed_bars=380; observed_completed_bars=380; missing_bars=0
            off_grid_bars=0; duplicate_bars=0; spread_bars=52; spread_coverage_percent=13.68
            source_counts=[pscustomobject]@{ KIS_PAPER_WEBSOCKET = 52; KIS_REST_MINUTE_SESSION = 328; PRIVATE_SOURCE = 5 }
            close='private-price'; account_number='private-account'; raw='private-payload'
        }) }

        $diagnostics = @(Get-SessionQualityDiagnostics $quality)
        $json = $diagnostics | ConvertTo-Json -Compress -Depth 5

        $diagnostics.Count | Should Be 1
        $diagnostics[0].spread_bars | Should Be 52
        $diagnostics[0].spread_coverage_percent | Should Be 13.68
        $diagnostics[0].source_counts.KIS_PAPER_WEBSOCKET | Should Be 52
        $diagnostics[0].source_counts.KIS_REST_MINUTE_SESSION | Should Be 328
        $json | Should Not Match 'private-price|private-account|private-payload|PRIVATE_SOURCE'
    }

    It 'rejects a complete row whose identity does not match the requested session' {
        $quality = @{
            market='US'; session_date='2026-11-06'; sessions=@(@{
                market='US'; symbol='AAPL'; session_date='2026-11-06'; status='COMPLETE'
                full_session_complete=$true; expected_bars=390; completed_minutes=390
                observed_bars=390; observed_completed_bars=390; missing_bars=0
                off_grid_bars=0; duplicate_bars=0; spread_bars=390; spread_coverage_percent=100.0
                source_counts=[pscustomobject]@{ KIS_REAL_US_WEBSOCKET = 390 }
            })
        }
        $validRow = $quality.sessions[0]
        foreach ($identityField in @('market', 'session_date', 'symbol')) {
            $row = $validRow.Clone()
            $row[$identityField] = switch ($identityField) {
                'market' { 'KR' }
                'session_date' { '2026-11-05' }
                'symbol' { 'MSFT' }
            }
            $quality.sessions = @($row)
            $didThrow = $false
            try { Assert-ExchangeSessionQuality $quality 'US' '2026-11-06' @('AAPL') 390 'KIS_REAL_US_WEBSOCKET' | Out-Null }
            catch { $didThrow = $true }
            $didThrow | Should Be $true
        }
    }

    It 'rejects a complete session stored under a different source' {
        $quality = @{ market='US'; session_date='2026-11-06'; sessions=@(@{
            market='US'; symbol='AAPL'; session_date='2026-11-06'; status='COMPLETE'
            full_session_complete=$true; expected_bars=390; completed_minutes=390
            observed_bars=390; observed_completed_bars=390; missing_bars=0
            off_grid_bars=0; duplicate_bars=0; spread_bars=390; spread_coverage_percent=100.0
            source_counts=[pscustomobject]@{ KIS_REST_MINUTE_CHART = 390 }
        }) }
        $didThrow = $false
        try { Assert-ExchangeSessionQuality $quality 'US' '2026-11-06' @('AAPL') 390 'KIS_REAL_US_WEBSOCKET' | Out-Null }
        catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

    It 'rejects mixed provenance even when the total bar count is complete' {
        $quality = @{ market='US'; session_date='2026-11-06'; sessions=@(@{
            market='US'; symbol='AAPL'; session_date='2026-11-06'; status='COMPLETE'
            full_session_complete=$true; expected_bars=390; completed_minutes=390
            observed_bars=390; observed_completed_bars=390; missing_bars=0
            off_grid_bars=0; duplicate_bars=0; spread_bars=390; spread_coverage_percent=100.0
            source_counts=[pscustomobject]@{ KIS_REAL_US_WEBSOCKET = 389; KIS_REST_MINUTE_CHART = 1 }
        }) }
        $didThrow = $false
        try { Assert-ExchangeSessionQuality $quality 'US' '2026-11-06' @('AAPL') 390 'KIS_REAL_US_WEBSOCKET' | Out-Null }
        catch { $didThrow = $true }
        $didThrow | Should Be $true
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
        try { Assert-ExchangeSessionQuality $quality 'US' '2026-11-06' @('AAPL') 390 'KIS_REAL_US_WEBSOCKET' | Out-Null }
        catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

    It 'rejects quality for another session date or a missing symbol' {
        $wrongDate = @{ market='US'; session_date='2026-11-05'; sessions=@() }
        $noSymbol = @{ market='US'; session_date='2026-11-06'; sessions=@() }
        $dateThrew = $false
        $symbolThrew = $false
        try { Assert-ExchangeSessionQuality $wrongDate 'US' '2026-11-06' @('AAPL') 390 'KIS_REAL_US_WEBSOCKET' | Out-Null }
        catch { $dateThrew = $true }
        try { Assert-ExchangeSessionQuality $noSymbol 'US' '2026-11-06' @('AAPL') 390 'KIS_REAL_US_WEBSOCKET' | Out-Null }
        catch { $symbolThrew = $true }
        $dateThrew | Should Be $true
        $symbolThrew | Should Be $true
    }
}

Describe 'Exchange session monitor registration times' {
    It 'starts KRX monitoring 15 minutes before its 09:00 KST open' {
        $start = Get-ExchangeSessionMonitorStart 'KR' ([DateTimeOffset]::Parse('2026-11-09T00:00:00Z'))
        $start.ToString('yyyy-MM-dd HH:mm zzz') | Should Be '2026-11-09 08:45 +09:00'
    }

    It 'uses the winter US open and starts five minutes early' {
        $start = Get-ExchangeSessionMonitorStart 'US' ([DateTimeOffset]::Parse('2026-11-09T14:30:00Z'))
        $start.ToString('yyyy-MM-dd HH:mm zzz') | Should Be '2026-11-09 23:25 +09:00'
    }

    It 'uses the summer US open and starts five minutes early' {
        $start = Get-ExchangeSessionMonitorStart 'US' ([DateTimeOffset]::Parse('2026-07-06T13:30:00Z'))
        $start.ToString('yyyy-MM-dd HH:mm zzz') | Should Be '2026-07-06 22:25 +09:00'
    }

    It 'retries transient session monitor failures without starting overlapping captures' {
        $settings = New-ExchangeSessionMonitorTaskSettings
        $settings.RestartCount | Should Be 4
        $settings.RestartInterval | Should Be 'PT1M'
        $settings.ExecutionTimeLimit | Should Be 'PT8H'
        $settings.MultipleInstances | Should Be 'IgnoreNew'
        $settings.StartWhenAvailable | Should Be $false
        $settings.WakeToRun | Should Be $true
        $settings.DisallowStartIfOnBatteries | Should Be $false
        $settings.StopIfGoingOnBatteries | Should Be $false
    }
}

Describe 'Domestic full-session feed continuity gate' {
    It 'continues a full-session observation after a tick gap but keeps the final result ineligible' {
        (Test-ExchangeSessionMonitorShouldDeferNoTickFailure 301 300 $true) | Should Be $true
        (Test-ExchangeSessionMonitorShouldDeferNoTickFailure 301 300 $false) | Should Be $false
        (Test-ExchangeSessionMonitorShouldDeferNoTickFailure 300 300 $true) | Should Be $false
        (Test-ExchangeSessionCompletion 380 380 $true $true 299 300 $true) | Should Be $true
        (Test-ExchangeSessionCompletion 380 380 $true $true 299 300 $false) | Should Be $false
        (Test-ExchangeSessionCompletion 380 380 $true $true 301 300 $true) | Should Be $false
        (Test-ExchangeSessionCompletion 380 380 $true $false 0 300 $true) | Should Be $false
    }
}

function New-TestScheduledTask($State, $Arguments, $TriggerType, $StartBoundary, $DaysInterval = 1) {
    [pscustomobject]@{
        State = $State
        Actions = @([pscustomobject]@{
            Execute = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
            Arguments = $Arguments
            WorkingDirectory = 'C:\repo'
        })
        Triggers = @([pscustomobject]@{
            Enabled = $true
            CimClass = [pscustomobject]@{ CimClassName = $TriggerType }
            StartBoundary = $StartBoundary
            DaysInterval = $DaysInterval
        })
        Principal = [pscustomobject]@{ RunLevel = 'Limited'; LogonType = 'Interactive' }
        Settings = [pscustomobject]@{
            WakeToRun = $true; StartWhenAvailable = ($TriggerType -eq 'MSFT_TaskDailyTrigger')
            DisallowStartIfOnBatteries = $false; StopIfGoingOnBatteries = $false
            MultipleInstances = 'IgnoreNew'
            RestartCount = 4; RestartInterval = 'PT1M'; ExecutionTimeLimit = 'PT8H'
        }
    }
}

function Test-ExpectedPostSessionRefreshTask($Task) {
    Test-PostSessionRefreshTaskDefinition -Task $Task `
        -RestartPath 'C:\repo\scripts\restart-local-stack-task.ps1' `
        -PowerShellPath 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' `
        -WorkingDirectory 'C:\repo' `
        -ExpectedRunAt ([DateTimeOffset]::Parse('2026-11-16T15:45:00+09:00'))
}

function New-TestPostSessionRefreshTask($Arguments, $StartBoundary, $StartWhenAvailable = $false) {
    [pscustomobject]@{
        State = 'Ready'
        Actions = @([pscustomobject]@{
            Execute = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
            Arguments = $Arguments
            WorkingDirectory = 'C:\repo'
        })
        Triggers = @([pscustomobject]@{
            Enabled = $true
            CimClass = [pscustomobject]@{ CimClassName = 'MSFT_TaskTimeTrigger' }
            StartBoundary = $StartBoundary
        })
        Principal = [pscustomobject]@{ RunLevel = 'Limited'; LogonType = 'Interactive' }
        Settings = [pscustomobject]@{
            WakeToRun = $true; StartWhenAvailable = $StartWhenAvailable
            DisallowStartIfOnBatteries = $false; StopIfGoingOnBatteries = $false
            MultipleInstances = 'IgnoreNew'; ExecutionTimeLimit = 'PT5M'
        }
    }
}

Describe 'Domestic post-session refresh task verification' {
    It 'computes the scheduled KRX refresh in KST' {
        (Get-KrxPostSessionRefreshTime '2026-11-16').ToString('o') | Should Be '2026-11-16T15:45:00.0000000+09:00'
    }

    It 'accepts a matching domestic no-catch-up restart task' {
        $task = New-TestPostSessionRefreshTask `
            '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\repo\scripts\restart-local-stack-task.ps1" -DomesticOnly' `
            '2026-11-16T15:45:00+09:00'
        (Test-ExpectedPostSessionRefreshTask $task) | Should Be $true
    }

    It 'rejects the Confirm string that broke the prior Windows task action' {
        $task = New-TestPostSessionRefreshTask `
            '-File "C:\repo\scripts\restart-local-stack-task.ps1" -DomesticOnly -Confirm:$false' `
            '2026-11-16T15:45:00+09:00'
        (Test-ExpectedPostSessionRefreshTask $task) | Should Be $false
    }

    It 'rejects delayed execution and any task that is not domestic-only' {
        $delayed = New-TestPostSessionRefreshTask `
            '-File "C:\repo\scripts\restart-local-stack-task.ps1" -DomesticOnly' `
            '2026-11-16T15:45:00+09:00' $true
        $wrongScope = New-TestPostSessionRefreshTask `
            '-File "C:\repo\scripts\restart-local-stack-task.ps1"' `
            '2026-11-16T15:45:00+09:00'
        $batteryRestricted = New-TestPostSessionRefreshTask `
            '-File "C:\repo\scripts\restart-local-stack-task.ps1" -DomesticOnly' `
            '2026-11-16T15:45:00+09:00'
        $batteryRestricted.Settings.DisallowStartIfOnBatteries = $true
        $batteryRestricted.Settings.StopIfGoingOnBatteries = $true
        (Test-ExpectedPostSessionRefreshTask $delayed) | Should Be $false
        (Test-ExpectedPostSessionRefreshTask $wrongScope) | Should Be $false
        (Test-ExpectedPostSessionRefreshTask $batteryRestricted) | Should Be $false

        $wrongExecutable = New-TestPostSessionRefreshTask `
            '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\repo\scripts\restart-local-stack-task.ps1" -DomesticOnly' `
            '2026-11-16T15:45:00+09:00'
        $wrongExecutable.Actions[0].Execute = 'C:\Windows\System32\cmd.exe'
        (Test-ExpectedPostSessionRefreshTask $wrongExecutable) | Should Be $false

        $wrongDirectory = New-TestPostSessionRefreshTask `
            '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\repo\scripts\restart-local-stack-task.ps1" -DomesticOnly' `
            '2026-11-16T15:45:00+09:00'
        $wrongDirectory.Actions[0].WorkingDirectory = 'C:\Windows\System32'
        (Test-ExpectedPostSessionRefreshTask $wrongDirectory) | Should Be $false

        $extraArguments = New-TestPostSessionRefreshTask `
            '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\repo\scripts\restart-local-stack-task.ps1" -DomesticOnly -Confirm:$false' `
            '2026-11-16T15:45:00+09:00'
        (Test-ExpectedPostSessionRefreshTask $extraArguments) | Should Be $false
    }
}

Describe 'Existing market session task verification' {
    It 'registers domestic sessions by default and permits the overseas horizon explicitly' {
        (@(Get-SessionMonitorMarkets) -join ',') | Should Be 'KR'
        (@(Get-SessionMonitorMarkets -IncludeUS) -join ',') | Should Be 'KR,US'
    }

    It 'accepts only a matching limited KST monitor schedule' {
        $task = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\test-paper-websocket-monitor.ps1" -Feed real -ExpectedSessionDate 2026-11-09 -DurationSeconds 25200 -RequireSessionClose' `
            'MSFT_TaskTimeTrigger' '2026-11-09T23:25:00+09:00'
        (Test-SessionMonitorTaskDefinition $task 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00'))) | Should Be $true
    }

    It 'rejects a monitor task without its exact scheduled session date' {
        $missingDate = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\test-paper-websocket-monitor.ps1" -Feed real -DurationSeconds 25200 -RequireSessionClose' `
            'MSFT_TaskTimeTrigger' '2026-11-09T23:25:00+09:00'
        $wrongDate = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\test-paper-websocket-monitor.ps1" -Feed real -ExpectedSessionDate 2026-11-08 -DurationSeconds 25200 -RequireSessionClose' `
            'MSFT_TaskTimeTrigger' '2026-11-09T23:25:00+09:00'
        (Test-SessionMonitorTaskDefinition $missingDate 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00'))) | Should Be $false
        (Test-SessionMonitorTaskDefinition $wrongDate 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00'))) | Should Be $false
    }

    It 'rejects disabled or mismatched monitor tasks' {
        $disabled = New-TestScheduledTask 'Disabled' '-File "C:\repo\scripts\test-paper-websocket-monitor.ps1" -Feed real -ExpectedSessionDate 2026-11-09 -DurationSeconds 25200 -RequireSessionClose' `
            'MSFT_TaskTimeTrigger' '2026-11-09T23:25:00+09:00'
        $wrongFeed = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\test-paper-websocket-monitor.ps1" -Feed paper -ExpectedSessionDate 2026-11-09 -DurationSeconds 25200 -RequireSessionClose' `
            'MSFT_TaskTimeTrigger' '2026-11-09T23:25:00+09:00'
        (Test-SessionMonitorTaskDefinition $disabled 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00'))) | Should Be $false
        (Test-SessionMonitorTaskDefinition $wrongFeed 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00'))) | Should Be $false

        $catchUp = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\test-paper-websocket-monitor.ps1" -Feed real -ExpectedSessionDate 2026-11-09 -DurationSeconds 25200 -RequireSessionClose' `
            'MSFT_TaskTimeTrigger' '2026-11-09T23:25:00+09:00'
        $catchUp.Settings.StartWhenAvailable = $true
        (Test-SessionMonitorTaskDefinition $catchUp 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00'))) | Should Be $false

        $batteryRestricted = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\test-paper-websocket-monitor.ps1" -Feed real -ExpectedSessionDate 2026-11-09 -DurationSeconds 25200 -RequireSessionClose' `
            'MSFT_TaskTimeTrigger' '2026-11-09T23:25:00+09:00'
        $batteryRestricted.Settings.DisallowStartIfOnBatteries = $true
        (Test-SessionMonitorTaskDefinition $batteryRestricted 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00'))) | Should Be $false
    }

    It 'rejects a monitor task without the bounded transient-failure retries' {
        $task = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\test-paper-websocket-monitor.ps1" -Feed real -ExpectedSessionDate 2026-11-09 -DurationSeconds 25200 -RequireSessionClose' `
            'MSFT_TaskTimeTrigger' '2026-11-09T23:25:00+09:00'
        $task.Settings.RestartCount = 0
        (Test-SessionMonitorTaskDefinition $task 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00'))) | Should Be $false
    }

    It 'rejects a monitor duration that ends exactly at exchange close' {
        $task = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\test-paper-websocket-monitor.ps1" -Feed real -ExpectedSessionDate 2026-11-09 -DurationSeconds 24300 -RequireSessionClose' `
            'MSFT_TaskTimeTrigger' '2026-11-09T23:25:00+09:00'
        (Test-SessionMonitorTaskDefinition $task 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00'))) | Should Be $false
        (Test-SessionMonitorTaskDefinition $task 'C:\repo\scripts\test-paper-websocket-monitor.ps1' 'real' `
            ([DateTimeOffset]::Parse('2026-11-09T23:25:00+09:00')) -ExpectedDurationSeconds 24300) | Should Be $true
    }

    It 'requires the daily refresh job to match the intended registrar and horizon' {
        $task = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\register-market-data-session-horizon.ps1" -SessionCount 10' `
            'MSFT_TaskDailyTrigger' '2026-11-08T00:05:00+09:00'
        $taskWithUS = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\register-market-data-session-horizon.ps1" -SessionCount 10 -IncludeUS' `
            'MSFT_TaskDailyTrigger' '2026-11-08T00:05:00+09:00'
        (Test-SessionMonitorRefreshTaskDefinition $task 'C:\repo\scripts\register-market-data-session-horizon.ps1' 10) | Should Be $true
        (Test-SessionMonitorRefreshTaskDefinition $task 'C:\repo\scripts\register-market-data-session-horizon.ps1' 12) | Should Be $false
        (Test-SessionMonitorRefreshTaskDefinition $taskWithUS 'C:\repo\scripts\register-market-data-session-horizon.ps1' 10 -IncludeUS) | Should Be $true
        (Test-SessionMonitorRefreshTaskDefinition $taskWithUS 'C:\repo\scripts\register-market-data-session-horizon.ps1' 10) | Should Be $false

        $batteryRestricted = New-TestScheduledTask 'Ready' '-File "C:\repo\scripts\register-market-data-session-horizon.ps1" -SessionCount 10' `
            'MSFT_TaskDailyTrigger' '2026-11-08T00:05:00+09:00'
        $batteryRestricted.Settings.DisallowStartIfOnBatteries = $true
        (Test-SessionMonitorRefreshTaskDefinition $batteryRestricted 'C:\repo\scripts\register-market-data-session-horizon.ps1' 10) | Should Be $false
    }
}

Describe 'Exchange session evidence scope' {
    It 'labels short monitoring runs as bounded samples' {
        (Get-ExchangeSessionValidationScope $false) | Should Be 'bounded_sample'
    }

    It 'labels close-and-quality gated monitoring as full-session validation' {
        (Get-ExchangeSessionValidationScope $true) | Should Be 'full_session'
    }
}
