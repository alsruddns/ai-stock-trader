. (Join-Path $PSScriptRoot '../paper-session-monitor-status.ps1')

Describe 'Domestic PAPER monitor status recovery window' {
    It 'tolerates short local status outages but expires after the configured grace period' {
        $start = [DateTimeOffset]::Parse('2026-11-12T01:00:00Z')
        $afterGrace = $start.AddSeconds(61)

        (Test-MonitorStatusUnavailableExpired $start $start.AddSeconds(45) 60) | Should Be $false
        (Test-MonitorStatusUnavailableExpired $start $start.AddSeconds(60) 60) | Should Be $false
        (Test-MonitorStatusUnavailableExpired $start $afterGrace 60) | Should Be $true
    }

    It 'rejects invalid grace windows and backwards timestamps' {
        $start = [DateTimeOffset]::Parse('2026-11-12T01:00:00Z')
        $afterStart = $start.AddSeconds(1)
        $beforeStart = $start.AddSeconds(-1)

        $invalidGraceThrew = $false
        $backwardsTimeThrew = $false
        try { Test-MonitorStatusUnavailableExpired $start $afterStart 0 | Out-Null }
        catch { $invalidGraceThrew = $true }
        try { Test-MonitorStatusUnavailableExpired $start $beforeStart 60 | Out-Null }
        catch { $backwardsTimeThrew = $true }
        $invalidGraceThrew | Should Be $true
        $backwardsTimeThrew | Should Be $true
    }

    It 'continues full-session validation through a status outage but still stops bounded samples' {
        $start = [DateTimeOffset]::Parse('2026-11-12T01:00:00Z')
        $afterGrace = $start.AddSeconds(61)

        (Test-MonitorStatusUnavailableShouldAbort $start $afterGrace 60 $true) | Should Be $false
        (Test-MonitorStatusUnavailableShouldAbort $start $afterGrace 60 $false) | Should Be $true
    }
}

Describe 'Domestic PAPER monitor reconnect tolerance' {
    It 'continues read-only observation during a selected feed reconnect with no aggregate error code' {
        $status = [pscustomobject]@{
            orders_enabled = $false
            automation_state = 'DISABLED'
            stream_connections = [pscustomobject]@{ paper = 'RECONNECTING'; real = 'RUNNING' }
            enabled = $true
            state = 'RECONNECTING'
            last_error = $null
        }

        { Assert-SafeRunningStatus $status 'paper' $false $true } | Should Not Throw
    }

    It 'still rejects a non-feed runtime error during reconnect' {
        $status = [pscustomobject]@{
            orders_enabled = $false
            automation_state = 'DISABLED'
            stream_connections = [pscustomobject]@{ paper = 'RECONNECTING'; real = 'RUNNING' }
            enabled = $true
            state = 'RECONNECTING'
            last_error = 'DATABASE_UNAVAILABLE'
        }

        $threw = $false
        try { Assert-SafeRunningStatus $status 'paper' $false $true }
        catch { $threw = $true }
        $threw | Should Be $true
    }
}

Describe 'Domestic PAPER bar storage readiness' {
    It 'accepts a running PostgreSQL writer only when the durable outbox is empty' {
        $status = [pscustomobject]@{ bar_storage_state = 'RUNNING'; bar_storage_pending_count = 0; bar_storage_retry_count = 2 }

        $diagnostic = Get-MonitorBarStorageDiagnostic $status

        $diagnostic.bar_storage_diagnostic_available | Should Be $true
        $diagnostic.bar_storage_ready | Should Be $true
        $diagnostic.bar_storage_pending_count | Should Be 0
        $diagnostic.bar_storage_retry_count | Should Be 2
        $diagnostic.bar_storage_reason | Should Be 'READY'
    }

    It 'keeps a pending outbox blocked while exposing only bounded counters' {
        $status = [pscustomobject]@{ bar_storage_state = 'RUNNING'; bar_storage_pending_count = 3; bar_storage_retry_count = 4; bar_storage_error = 'private detail' }

        $diagnostic = Get-MonitorBarStorageDiagnostic $status
        $json = $diagnostic | ConvertTo-Json -Compress -Depth 3

        $diagnostic.bar_storage_ready | Should Be $false
        $diagnostic.bar_storage_pending_count | Should Be 3
        $diagnostic.bar_storage_reason | Should Be 'PENDING_BARS'
        $json | Should Not Match 'private detail|bar_storage_error'
    }

    It 'fails closed for missing or malformed storage status' {
        $missing = Get-MonitorBarStorageDiagnostic ([pscustomobject]@{ state = 'RUNNING' })
        $malformed = Get-MonitorBarStorageDiagnostic ([pscustomobject]@{
            bar_storage_state = 'secret-bearing-state'; bar_storage_pending_count = -1
        })

        $missing.bar_storage_diagnostic_available | Should Be $false
        $missing.bar_storage_ready | Should Be $false
        $malformed.bar_storage_state | Should Be $null
        $malformed.bar_storage_pending_count | Should Be $null
        $malformed.bar_storage_ready | Should Be $false
    }
}

Describe 'Domestic minute-level spread diagnostics' {
    It 'distinguishes quote gaps from quotes that did not produce a spread bar' {
        $status = [pscustomobject]@{
            market_data_minute_quality_by_watch_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{
                    'KR:005930:' = [pscustomobject]@{
                        '2026-11-10T09:00:00+09:00' = [pscustomobject]@{
                            market = 'KR'; symbol = '005930'; exchange = $null
                            bar_closed = $true; bar_spread_present = $true
                            bar_spread_observed_at = '2026-11-10T09:00:20+09:00'
                            trade_ticks_received = 4; orderbook_quotes_received = 2; usable_orderbook_quotes = 2
                            empty_or_nonpositive_quotes = 0; crossed_quotes = 0; stale_quotes = 0
                        }
                        '2026-11-10T09:01:00+09:00' = [pscustomobject]@{
                            market = 'KR'; symbol = '005930'; exchange = $null
                            bar_closed = $true; bar_spread_present = $false
                            bar_spread_observed_at = $null
                            trade_ticks_received = 3; orderbook_quotes_received = 1; usable_orderbook_quotes = 1
                            empty_or_nonpositive_quotes = 1; crossed_quotes = 0; stale_quotes = 0
                        }
                        '2026-11-10T09:02:00+09:00' = [pscustomobject]@{
                            market = 'KR'; symbol = '005930'; exchange = $null
                            bar_closed = $true; bar_spread_present = $false
                            bar_spread_observed_at = $null
                            trade_ticks_received = 2; orderbook_quotes_received = 0; usable_orderbook_quotes = 0
                            empty_or_nonpositive_quotes = 0; crossed_quotes = 0; stale_quotes = 0
                        }
                    }
                }
                real = [pscustomobject]@{}
            }
        }

        $diagnostics = Get-MonitorMinuteQualityDiagnostics $status 'paper'

        $diagnostics.available | Should Be $true
        ($diagnostics.symbols -is [array]) | Should Be $true
        $diagnostics.symbols.Count | Should Be 1
        $diagnostics.symbols[0].closed_minutes | Should Be 3
        $diagnostics.symbols[0].spread_minutes | Should Be 1
        $diagnostics.symbols[0].spread_coverage_percent | Should Be 33.33
        $diagnostics.symbols[0].minutes_with_trade_ticks | Should Be 3
        $diagnostics.symbols[0].minutes_with_orderbook_quotes | Should Be 2
        $diagnostics.symbols[0].minutes_with_quotes_without_spread | Should Be 1
        $diagnostics.symbols[0].minutes_without_orderbook_quotes | Should Be 1
        $diagnostics.symbols[0].trade_ticks | Should Be 9
        $diagnostics.symbols[0].empty_or_nonpositive_quotes | Should Be 1
        $diagnostics.symbols[0].missing_spread_samples.Count | Should Be 2
    }

    It 'reports missing or malformed per-minute diagnostics as unavailable' {
        (Get-MonitorMinuteQualityDiagnostics ([pscustomobject]@{}) 'paper').reason |
            Should Be 'FIELD_UNAVAILABLE'
        $status = [pscustomobject]@{
            market_data_minute_quality_by_watch_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{
                    'KR:005930:' = [pscustomobject]@{
                        'not-a-time' = [pscustomobject]@{
                            market = 'KR'; symbol = '005930'; exchange = $null
                            bar_closed = $true; bar_spread_present = $false
                            bar_spread_observed_at = $null
                            trade_ticks_received = 0; orderbook_quotes_received = 0
                            usable_orderbook_quotes = 0; empty_or_nonpositive_quotes = 0
                            crossed_quotes = 0; stale_quotes = 0
                        }
                    }
                }
                real = [pscustomobject]@{}
            }
        }
        (Get-MonitorMinuteQualityDiagnostics $status 'paper').reason |
            Should Be 'INVALID_MINUTE_QUALITY_DATA'
    }
}

Describe 'Paper session monitor failure codes' {
    It 'classifies a stalled WebSocket without persisting the raw exception text' {
        Get-MonitorFailureCode 'No WebSocket ticks arrived for more than 300 seconds.' |
            Should Be 'WEBSOCKET_NO_TICKS'
    }

    It 'classifies a completed sample with no ticks separately from a stalled connection' {
        Get-MonitorFailureCode 'No increase in WebSocket ticks was observed during the selected sample window.' |
            Should Be 'NO_TICKS_IN_SAMPLE_WINDOW'
    }

    It 'classifies exchange close and finalization timeouts separately' {
        Get-MonitorFailureCode 'The monitored exchange session did not reach its calendar-confirmed close.' |
            Should Be 'EXCHANGE_SESSION_CLOSE_NOT_REACHED'
        Get-MonitorFailureCode 'The final stored session did not reach complete bars, spread, and source verification within the post-close grace period.' |
            Should Be 'SESSION_FINALIZATION_TIMEOUT'
    }

    It 'classifies missing timestamps, reset counters, and safety lock failures' {
        Get-MonitorFailureCode 'The running market-data service does not expose a tick receive timestamp; restart it with the current code before monitoring.' |
            Should Be 'FEED_TIMESTAMP_UNAVAILABLE'
        Get-MonitorFailureCode 'The closed bar counter moved backwards during the sample.' |
            Should Be 'BAR_COUNTER_RESET'
        Get-MonitorFailureCode 'Paper-session monitor is not running in read-only, automation-disabled mode.' |
            Should Be 'SAFETY_LOCK_VIOLATION'
    }

    It 'uses a safe generic code for unknown exception text' {
        Get-MonitorFailureCode 'account=private-key-value' | Should Be 'MONITOR_FAILED'
    }
}

Describe 'Paper session WebSocket failure diagnostics' {
    It 'keeps only approved stage and category codes' {
        $status = [pscustomobject]@{
            last_stream_failure_stage = 'websocket_connect'
            last_stream_error_category = 'NETWORK_DISCONNECT'
            last_stream_failure_at = '2026-11-05T00:00:00+00:00'
            last_stream_failure_environment = 'paper'
            stream_failures_by_feed = [pscustomobject]@{ paper = 3; real = 0 }
        }

        $diagnostic = Get-MonitorStreamFailureDiagnostic $status

        $diagnostic.stream_failure_diagnostic_available | Should Be $true
        $diagnostic.stream_failure_stage | Should Be 'websocket_connect'
        $diagnostic.stream_error_category | Should Be 'NETWORK_DISCONNECT'
        $diagnostic.stream_failure_at | Should Be '2026-11-05T00:00:00.0000000+00:00'
        $diagnostic.stream_failure_environment | Should Be 'paper'
        $diagnostic.stream_failure_count_available | Should Be $true
        $diagnostic.stream_failure_count | Should Be 3
    }

    It 'omits arbitrary exception text and distinguishes older API payloads' {
        $unsafeStatus = [pscustomobject]@{
            last_stream_failure_stage = 'password=private-value'
            last_stream_error_category = 'appsecret=private-value'
            last_stream_failure_environment = 'paper'
            stream_failures_by_feed = [pscustomobject]@{ paper = -1; real = 0 }
        }
        $oldStatus = [pscustomobject]@{ state = 'RUNNING' }

        $unsafe = Get-MonitorStreamFailureDiagnostic $unsafeStatus
        $old = Get-MonitorStreamFailureDiagnostic $oldStatus
        $combined = (@($unsafe, $old) | ConvertTo-Json -Compress -Depth 4)

        $unsafe.stream_failure_stage | Should Be $null
        $unsafe.stream_error_category | Should Be $null
        $unsafe.stream_failure_count_available | Should Be $false
        $unsafe.stream_failure_count | Should Be $null
        $old.stream_failure_diagnostic_available | Should Be $false
        $combined | Should Not Match 'private-value'
    }
}

Describe 'Paper session WebSocket recovery diagnostics' {
    It 'normalizes feed recovery timestamps and retains the measured duration' {
        $status = [pscustomobject]@{
            stream_reconnecting_since_by_feed = [pscustomobject]@{ paper = '2026-11-13T06:00:00+00:00'; real = $null }
            stream_last_recovered_at_by_feed = [pscustomobject]@{ paper = '2026-11-13T06:00:15.250+00:00'; real = $null }
            stream_last_recovery_seconds_by_feed = [pscustomobject]@{ paper = 15.25; real = $null }
        }

        $diagnostic = Get-MonitorStreamRecoveryDiagnostic $status 'paper'

        $diagnostic.stream_recovery_diagnostic_available | Should Be $true
        $diagnostic.stream_reconnecting_since | Should Be '2026-11-13T06:00:00.0000000+00:00'
        $diagnostic.stream_last_recovered_at | Should Be '2026-11-13T06:00:15.2500000+00:00'
        $diagnostic.stream_last_recovery_seconds | Should Be 15.25
    }

    It 'fails closed on invalid duration and distinguishes old API payloads' {
        $invalid = [pscustomobject]@{
            stream_reconnecting_since_by_feed = [pscustomobject]@{ paper = $null }
            stream_last_recovered_at_by_feed = [pscustomobject]@{ paper = $null }
            stream_last_recovery_seconds_by_feed = [pscustomobject]@{ paper = 'appsecret=private-value' }
        }
        $old = [pscustomobject]@{ state = 'RUNNING' }

        $badDiagnostic = Get-MonitorStreamRecoveryDiagnostic $invalid 'paper'
        $oldDiagnostic = Get-MonitorStreamRecoveryDiagnostic $old 'paper'
        $combined = (@($badDiagnostic, $oldDiagnostic) | ConvertTo-Json -Compress -Depth 4)

        $badDiagnostic.stream_recovery_diagnostic_available | Should Be $false
        $badDiagnostic.stream_last_recovery_seconds | Should Be $null
        $oldDiagnostic.stream_recovery_diagnostic_available | Should Be $false
        $combined | Should Not Match 'private-value'
    }
}

Describe 'Paper session feed quote-quality diagnostics' {
    It 'records the four valid counters independently from frame diagnostics' {
        $status = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 12; real = 2 }
            orderbook_updates_by_feed = [pscustomobject]@{ paper = 4; real = 1 }
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 5; H0STASP0 = 7; HDFSCNT0 = 0; OTHER = 0 }
                real = [pscustomobject]@{ H0STCNT0 = 0; H0STASP0 = 0; HDFSCNT0 = 2; OTHER = 0 }
            }
            orderbook_quote_quality_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ usable = 5; empty_or_nonpositive = 1; crossed = 1; stale_rejected = 2 }
                real = [pscustomobject]@{ usable = 0; empty_or_nonpositive = 0; crossed = 0; stale_rejected = 0 }
            }
            market_data_diagnostics_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{
                    trade_market_class_filtered = 3; orderbook_market_class_filtered = 4
                    trade_parse_errors = 0; orderbook_parse_errors = 0
                    trade_symbol_mismatches = 0; orderbook_symbol_mismatches = 0
                    encrypted_market_frames_rejected = 0
                }
                real = [pscustomobject]@{
                    trade_market_class_filtered = 0; orderbook_market_class_filtered = 0
                    trade_parse_errors = 0; orderbook_parse_errors = 0
                    trade_symbol_mismatches = 0; orderbook_symbol_mismatches = 0
                    encrypted_market_frames_rejected = 0
                }
            }
        }

        $diagnostics = Get-MonitorFeedDiagnostics $status 'paper'

        $diagnostics.paper.available | Should Be $true
        $diagnostics.paper.orderbook_updates_available | Should Be $true
        $diagnostics.paper.orderbook_updates | Should Be 4
        $diagnostics.paper.quote_quality_available | Should Be $true
        $diagnostics.paper.quote_quality.usable | Should Be 5
        $diagnostics.paper.quote_quality.empty_or_nonpositive | Should Be 1
        $diagnostics.paper.quote_quality.crossed | Should Be 1
        $diagnostics.paper.quote_quality.stale_rejected | Should Be 2
        $diagnostics.paper.processing_diagnostics_available | Should Be $true
        $diagnostics.paper.processing_diagnostics.trade_market_class_filtered | Should Be 3
        $diagnostics.paper.processing_diagnostics.orderbook_market_class_filtered | Should Be 4
    }

    It 'marks quote-quality diagnostics unavailable for older runtime payloads without losing frame counts' {
        $status = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 12 }
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 5; H0STASP0 = 7; HDFSCNT0 = 0; OTHER = 0 }
            }
        }

        $diagnostics = Get-MonitorFeedDiagnostics $status 'paper'

        $diagnostics.paper.available | Should Be $true
        $diagnostics.paper.frames_received | Should Be 12
        $diagnostics.paper.orderbook_updates_available | Should Be $false
        $diagnostics.paper.orderbook_updates | Should Be $null
        $diagnostics.paper.quote_quality_available | Should Be $false
        $diagnostics.paper.quote_quality | Should Be $null
        $diagnostics.paper.processing_diagnostics_available | Should Be $false
        $diagnostics.paper.processing_diagnostics | Should Be $null
    }

    It 'rejects a negative quote-quality count without failing closed on frame availability' {
        $status = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 12 }
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 5; H0STASP0 = 7; HDFSCNT0 = 0; OTHER = 0 }
            }
            orderbook_quote_quality_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ usable = 5; empty_or_nonpositive = -1; crossed = 1; stale_rejected = 2 }
            }
        }

        $diagnostics = Get-MonitorFeedDiagnostics $status 'paper'

        $diagnostics.paper.available | Should Be $true
        $diagnostics.paper.quote_quality_available | Should Be $false
        $diagnostics.paper.quote_quality | Should Be $null
    }

    It 'rejects a negative accepted orderbook-update count without losing frame diagnostics' {
        $status = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 12 }
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 5; H0STASP0 = 7; HDFSCNT0 = 0; OTHER = 0 }
            }
            orderbook_updates_by_feed = [pscustomobject]@{ paper = -1 }
        }

        $diagnostics = Get-MonitorFeedDiagnostics $status 'paper'

        $diagnostics.paper.available | Should Be $true
        $diagnostics.paper.frames_received | Should Be 12
        $diagnostics.paper.orderbook_updates_available | Should Be $false
        $diagnostics.paper.orderbook_updates | Should Be $null
    }
}

Describe 'Per-symbol feed diagnostics for domestic session evidence' {
    It 'validates and returns only domestic symbol counts and UTC timestamps' {
        $status = [pscustomobject]@{
            market_data_by_watch_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{
                    'KR:005930:' = [pscustomobject]@{
                        market = 'KR'; symbol = '005930'; exchange = $null
                        trade_ticks_received = 12; bars_closed = 3
                        orderbook_quotes_received = 19; usable_orderbook_quotes = 18
                        last_trade_at = '2026-11-10T09:00:01+09:00'
                        last_trade_received_at = '2026-11-10T09:00:02+09:00'
                        last_orderbook_at = '2026-11-10T09:00:03+09:00'
                        last_orderbook_received_at = '2026-11-10T09:00:04+09:00'
                    }
                }
            }
        }

        $diagnostic = Get-MonitorSymbolMarketData $status 'paper'
        $json = $diagnostic | ConvertTo-Json -Compress -Depth 5

        $diagnostic.available | Should Be $true
        $diagnostic.symbols.Count | Should Be 1
        $diagnostic.symbols[0].key | Should Be 'KR:005930:'
        $diagnostic.symbols[0].trade_ticks_received | Should Be 12
        $diagnostic.symbols[0].orderbook_quotes_received | Should Be 19
        $diagnostic.symbols[0].last_trade_at_utc | Should Match '\+00:00$'
        $json | Should Not Match 'price|payload|account|secret'
    }

    It 'reports per-symbol counter deltas and fails closed on a counter reset' {
        $initial = [pscustomobject]@{
            market_data_by_watch_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{
                    'KR:005930:' = [pscustomobject]@{
                        market = 'KR'; symbol = '005930'; exchange = $null
                        trade_ticks_received = 10; bars_closed = 2
                        orderbook_quotes_received = 15; usable_orderbook_quotes = 14
                        last_trade_at = '2026-11-10T09:00:01+09:00'
                        last_trade_received_at = '2026-11-10T09:00:02+09:00'
                        last_orderbook_at = '2026-11-10T09:00:03+09:00'
                        last_orderbook_received_at = '2026-11-10T09:00:04+09:00'
                    }
                }
            }
        }
        $current = [pscustomobject]@{
            market_data_by_watch_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{
                    'KR:005930:' = [pscustomobject]@{
                        market = 'KR'; symbol = '005930'; exchange = $null
                        trade_ticks_received = 16; bars_closed = 3
                        orderbook_quotes_received = 24; usable_orderbook_quotes = 22
                        last_trade_at = '2026-11-10T09:01:01+09:00'
                        last_trade_received_at = '2026-11-10T09:01:02+09:00'
                        last_orderbook_at = '2026-11-10T09:01:03+09:00'
                        last_orderbook_received_at = '2026-11-10T09:01:04+09:00'
                    }
                }
            }
        }
        $delta = Get-MonitorSymbolMarketDataDelta $initial $current 'paper'
        $delta.available | Should Be $true
        $delta.symbols[0].trade_ticks_received | Should Be 6
        $delta.symbols[0].bars_closed | Should Be 1
        $delta.symbols[0].orderbook_quotes_received | Should Be 9

        $current.market_data_by_watch_by_feed.paper.'KR:005930:'.trade_ticks_received = 1
        $reset = Get-MonitorSymbolMarketDataDelta $initial $current 'paper'
        $reset.available | Should Be $false
        $reset.reason | Should Be 'COUNTER_DECREASED'
    }
}

Describe 'Paper session feed diagnostics deltas' {
    It 'reports per-monitor increases for frame, quote, and processing counters' {
        $initial = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 100 }
            orderbook_updates_by_feed = [pscustomobject]@{ paper = 50 }
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 20; H0STASP0 = 70; HDFSCNT0 = 0; OTHER = 10 }
            }
            orderbook_quote_quality_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ usable = 50; empty_or_nonpositive = 3; crossed = 2; stale_rejected = 1 }
            }
            market_data_diagnostics_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{
                    trade_market_class_filtered = 5; orderbook_market_class_filtered = 8
                    trade_parse_errors = 0; orderbook_parse_errors = 1
                    trade_symbol_mismatches = 2; orderbook_symbol_mismatches = 0
                    encrypted_market_frames_rejected = 0
                }
            }
        }
        $current = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 145 }
            orderbook_updates_by_feed = [pscustomobject]@{ paper = 68 }
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 35; H0STASP0 = 100; HDFSCNT0 = 0; OTHER = 10 }
            }
            orderbook_quote_quality_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ usable = 75; empty_or_nonpositive = 4; crossed = 2; stale_rejected = 2 }
            }
            market_data_diagnostics_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{
                    trade_market_class_filtered = 9; orderbook_market_class_filtered = 14
                    trade_parse_errors = 0; orderbook_parse_errors = 1
                    trade_symbol_mismatches = 2; orderbook_symbol_mismatches = 1
                    encrypted_market_frames_rejected = 0
                }
            }
        }

        $delta = Get-MonitorFeedDiagnosticsDelta $initial $current 'paper'

        $delta.paper.frames_available | Should Be $true
        $delta.paper.frames_received | Should Be 45
        $delta.paper.orderbook_updates_available | Should Be $true
        $delta.paper.orderbook_updates | Should Be 18
        $delta.paper.transaction_counts.H0STCNT0 | Should Be 15
        $delta.paper.transaction_counts.H0STASP0 | Should Be 30
        $delta.paper.quote_quality.usable | Should Be 25
        $delta.paper.quote_quality.stale_rejected | Should Be 1
        $delta.paper.processing_diagnostics.trade_market_class_filtered | Should Be 4
        $delta.paper.processing_diagnostics.orderbook_symbol_mismatches | Should Be 1
    }

    It 'marks a reset counter unavailable instead of reporting a negative delta' {
        $initial = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 100 }
            orderbook_updates_by_feed = [pscustomobject]@{ paper = 10 }
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 20; H0STASP0 = 70; HDFSCNT0 = 0; OTHER = 10 }
            }
        }
        $current = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 5 }
            orderbook_updates_by_feed = [pscustomobject]@{ paper = 3 }
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 2; H0STASP0 = 2; HDFSCNT0 = 0; OTHER = 1 }
            }
        }

        $delta = Get-MonitorFeedDiagnosticsDelta $initial $current 'paper'

        $delta.paper.frames_available | Should Be $false
        $delta.paper.frames_received | Should Be $null
        $delta.paper.transaction_counts_available | Should Be $false
        $delta.paper.transaction_counts | Should Be $null
        $delta.paper.orderbook_updates_available | Should Be $false
        $delta.paper.orderbook_updates | Should Be $null
        $delta.paper.quote_quality_available | Should Be $false
        $delta.paper.processing_diagnostics_available | Should Be $false
    }
}
