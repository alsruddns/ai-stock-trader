. (Join-Path $PSScriptRoot '..\lib\operations-report.ps1')

Describe 'Domestic KRX session monitor report summary' {
    It 'reports only the latest domestic PAPER run with a bounded sample label' {
        $path = Join-Path $TestDrive 'paper-progress.jsonl'
        $records = @(
            [ordered]@{
                recorded_at_utc = '2026-11-09T00:07:41+00:00'; market_session = 'KR'; market_session_date = '2026-11-09'
                selected_feed = 'paper'; validation_scope = 'full_session'; result = 'failed'; failure_stage = 'stream_monitoring'
                failure_code = 'WEBSOCKET_NO_TICKS'; max_observed_no_tick_seconds = 300.4; ticks_delta = 0; bars_delta = 0
            },
            [ordered]@{
                recorded_at_utc = '2026-11-09T04:36:55+00:00'; market_session = 'KR'; market_session_date = '2026-11-09'
                selected_feed = 'paper'; validation_scope = 'bounded_sample'; result = 'passed'; session_close_reached = $false
                session_quality_status = 'not_checked'; ticks_delta = 2006; bars_delta = 5; max_observed_no_tick_seconds = 1.9
                feed_diagnostics_delta = [ordered]@{
                    paper = [ordered]@{
                        transaction_counts_available = $true; transaction_counts = [ordered]@{ H0STASP0 = 900 }
                        orderbook_updates_available = $true; orderbook_updates = 850
                    }
                }
            }
        )
        foreach ($record in $records) {
            [IO.File]::AppendAllText($path, ($record | ConvertTo-Json -Compress -Depth 5) + [Environment]::NewLine)
        }

        $summary = Get-AiTraderDomesticSessionMonitorSummary -Path $path

        $summary.state | Should Be 'PASSED'
        $summary.validationScope | Should Be 'bounded_sample'
        $summary.sessionCloseReached | Should Be $false
        $summary.sessionQualityStatus | Should Be 'not_checked'
        $summary.ticksDelta | Should Be 2006
        $summary.barsDelta | Should Be 5
        $summary.orderbookFramesDelta | Should Be 900
        $summary.orderbookUpdatesDelta | Should Be 850
        $summary.failureCode | Should Be $null
    }

    It 'keeps known failure diagnostics and excludes arbitrary raw fields' {
        $path = Join-Path $TestDrive 'paper-failure.jsonl'
        $record = [ordered]@{
            recorded_at_utc = '2026-11-09T00:07:41+00:00'; market_session = 'KR'; market_session_date = '2026-11-09'
            selected_feed = 'paper'; validation_scope = 'full_session'; result = 'failed'; failure_stage = 'stream_monitoring'
            failure_code = 'WEBSOCKET_NO_TICKS'; failure_type = 'RuntimeException'; exception = 'private diagnostic'
            max_observed_no_tick_seconds = 300.4; ticks_delta = 0; bars_delta = 0
        }
        [IO.File]::WriteAllText($path, ($record | ConvertTo-Json -Compress -Depth 5))

        $summary = Get-AiTraderDomesticSessionMonitorSummary -Path $path
        $json = $summary | ConvertTo-Json -Compress -Depth 5

        $summary.state | Should Be 'FAILED'
        $summary.failureStage | Should Be 'stream_monitoring'
        $summary.failureCode | Should Be 'WEBSOCKET_NO_TICKS'
        $summary.maxNoTickSeconds | Should Be 300.4
        $json | Should Not Match 'private diagnostic|RuntimeException'
    }

    It 'returns unverified for missing or malformed local evidence' {
        $missing = Get-AiTraderDomesticSessionMonitorSummary -Path (Join-Path $TestDrive 'missing.jsonl')
        $path = Join-Path $TestDrive 'malformed.jsonl'
        $validRecord = [ordered]@{
            recorded_at_utc = '2026-11-09T00:07:41+00:00'; market_session = 'KR'; market_session_date = '2026-11-09'
            selected_feed = 'paper'; validation_scope = 'full_session'; result = 'passed'
        }
        [IO.File]::WriteAllText($path, ($validRecord | ConvertTo-Json -Compress) + "`nnot-json`n")
        $malformed = Get-AiTraderDomesticSessionMonitorSummary -Path $path

        $missing.state | Should Be 'UNVERIFIED'
        $malformed.state | Should Be 'UNVERIFIED'
        $malformed.failureCode | Should Be $null
    }

    It 'omits invalid or unavailable orderbook counters from the safe report' {
        $path = Join-Path $TestDrive 'paper-invalid-orderbook-diagnostics.jsonl'
        $record = [ordered]@{
            recorded_at_utc = '2026-11-10T00:00:00+00:00'; market_session = 'KR'; market_session_date = '2026-11-10'
            selected_feed = 'paper'; validation_scope = 'full_session'; result = 'running'
            feed_diagnostics_delta = [ordered]@{
                paper = [ordered]@{
                    transaction_counts_available = $true; transaction_counts = [ordered]@{ H0STASP0 = '-1' }
                    orderbook_updates_available = $true; orderbook_updates = '-1'
                    raw_payload = 'private diagnostic'
                }
            }
        }
        [IO.File]::WriteAllText($path, ($record | ConvertTo-Json -Compress -Depth 5))

        $summary = Get-AiTraderDomesticSessionMonitorSummary -Path $path
        $json = $summary | ConvertTo-Json -Compress -Depth 5

        $summary.state | Should Be 'RUNNING'
        $summary.orderbookFramesDelta | Should Be $null
        $summary.orderbookUpdatesDelta | Should Be $null
        $json | Should Not Match 'private diagnostic|raw_payload'
    }
}
