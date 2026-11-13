. (Join-Path $PSScriptRoot '..\lib\operations-report.ps1')

Describe 'Domestic PAPER monitor bar storage readiness report' {
    It 'reports a blocked pending outbox without exposing storage error text' {
        $path = Join-Path $TestDrive 'paper-storage-pending.jsonl'
        $record = [ordered]@{
            recorded_at_utc = '2026-11-13T07:30:00Z'; market_session = 'KR'; market_session_date = '2026-11-13'
            selected_feed = 'paper'; validation_scope = 'full_session'; result = 'failed'; failure_stage = 'post_close_validation'
            failure_code = 'BAR_STORAGE_NOT_DRAINED'; bar_storage_state = 'RUNNING'
            bar_storage_pending_count = 2; bar_storage_retry_count = 5; bar_storage_error = 'private connection detail'
        }
        [IO.File]::WriteAllText($path, ($record | ConvertTo-Json -Compress -Depth 5))

        $summary = Get-AiTraderDomesticSessionMonitorSummary -Path $path
        $json = $summary | ConvertTo-Json -Compress -Depth 5

        $summary.state | Should Be 'FAILED'
        $summary.failureStage | Should Be 'post_close_validation'
        $summary.failureCode | Should Be 'BAR_STORAGE_NOT_DRAINED'
        $summary.barStorageDiagnosticAvailable | Should Be $true
        $summary.barStorageReady | Should Be $false
        $summary.barStorageState | Should Be 'RUNNING'
        $summary.barStoragePendingCount | Should Be 2
        $summary.barStorageRetryCount | Should Be 5
        $summary.barStorageReason | Should Be 'PENDING_BARS'
        $json | Should Not Match 'private connection detail|bar_storage_error'
    }
}

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
                symbol_market_data_delta = [ordered]@{
                    available = $true
                    symbols = @(
                        [ordered]@{
                            key = 'KR:005930:'; market = 'KR'; symbol = '005930'; exchange = $null
                            trade_ticks_received = 12; bars_closed = 3; orderbook_quotes_received = 19; usable_orderbook_quotes = 18
                            last_trade_at_utc = '2026-11-09T01:00:01+00:00'; last_trade_received_at_utc = '2026-11-09T01:00:02+00:00'
                            last_orderbook_at_utc = '2026-11-09T01:00:03+00:00'; last_orderbook_received_at_utc = '2026-11-09T01:00:04+00:00'
                        },
                        [ordered]@{
                            key = 'KR:000660:'; market = 'KR'; symbol = '000660'; exchange = $null
                            trade_ticks_received = 0; bars_closed = 0; orderbook_quotes_received = 0; usable_orderbook_quotes = 0
                            last_trade_at_utc = $null; last_trade_received_at_utc = $null
                            last_orderbook_at_utc = $null; last_orderbook_received_at_utc = $null
                        }
                    )
                }
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
        $summary.symbolMarketDataStatus | Should Be 'AVAILABLE'
        $summary.symbolMarketData.Count | Should Be 2
        $summary.symbolMarketData[0].symbol | Should Be '005930'
        $summary.symbolMarketData[0].tradeTicksReceived | Should Be 12
        $summary.symbolMarketData[1].symbol | Should Be '000660'
        $summary.symbolMarketData[1].tradeTicksReceived | Should Be 0
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

    It 'rejects non-domestic or malformed per-symbol data without exposing raw fields' {
        $path = Join-Path $TestDrive 'paper-invalid-symbol-market-data.jsonl'
        $record = [ordered]@{
            recorded_at_utc = '2026-11-10T00:00:00+00:00'; market_session = 'KR'; market_session_date = '2026-11-10'
            selected_feed = 'paper'; validation_scope = 'full_session'; result = 'failed'
            symbol_market_data_delta = [ordered]@{
                available = $true
                symbols = @(
                    [ordered]@{
                        key = 'KR:005930:'; market = 'KR'; symbol = '005930'; exchange = $null
                        trade_ticks_received = 1; bars_closed = 1; orderbook_quotes_received = 1; usable_orderbook_quotes = 1
                        last_trade_at_utc = '2026-11-09T01:00:01+00:00'; last_trade_received_at_utc = '2026-11-09T01:00:02+00:00'
                        last_orderbook_at_utc = '2026-11-09T01:00:03+00:00'; last_orderbook_received_at_utc = '2026-11-09T01:00:04+00:00'
                        price = 'sensitive'; payload = 'raw'
                    },
                    [ordered]@{
                        key = 'US:AAPL:NAS'; market = 'US'; symbol = 'AAPL'; exchange = 'NAS'
                        trade_ticks_received = 1; bars_closed = 1; orderbook_quotes_received = 1; usable_orderbook_quotes = 1
                    }
                )
            }
        }
        [IO.File]::WriteAllText($path, ($record | ConvertTo-Json -Compress -Depth 7))

        $summary = Get-AiTraderDomesticSessionMonitorSummary -Path $path
        $json = $summary | ConvertTo-Json -Compress -Depth 5

        $summary.symbolMarketDataStatus | Should Be 'UNVERIFIED'
        $summary.symbolMarketData.Count | Should Be 0
        $json | Should Not Match 'sensitive|payload|AAPL|NAS'
    }
}
