. (Join-Path $PSScriptRoot '../paper-session-monitor-status.ps1')

Describe 'Paper session monitor failure codes' {
    It 'classifies a stalled WebSocket without persisting the raw exception text' {
        Get-MonitorFailureCode 'No WebSocket ticks arrived for more than 300 seconds.' |
            Should Be 'WEBSOCKET_NO_TICKS'
    }

    It 'classifies a completed sample with no ticks separately from a stalled connection' {
        Get-MonitorFailureCode 'No increase in WebSocket ticks was observed during the selected sample window.' |
            Should Be 'NO_TICKS_IN_SAMPLE_WINDOW'
    }

    It 'uses a safe generic code for unknown exception text' {
        Get-MonitorFailureCode 'account=private-key-value' | Should Be 'MONITOR_FAILED'
    }
}

Describe 'Paper session feed quote-quality diagnostics' {
    It 'records the four valid counters independently from frame diagnostics' {
        $status = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 12; real = 2 }
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
}

Describe 'Paper session feed diagnostics deltas' {
    It 'reports per-monitor increases for frame, quote, and processing counters' {
        $initial = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 100 }
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
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 20; H0STASP0 = 70; HDFSCNT0 = 0; OTHER = 10 }
            }
        }
        $current = [pscustomobject]@{
            frames_received_by_feed = [pscustomobject]@{ paper = 5 }
            frame_transaction_counts_by_feed = [pscustomobject]@{
                paper = [pscustomobject]@{ H0STCNT0 = 2; H0STASP0 = 2; HDFSCNT0 = 0; OTHER = 1 }
            }
        }

        $delta = Get-MonitorFeedDiagnosticsDelta $initial $current 'paper'

        $delta.paper.frames_available | Should Be $false
        $delta.paper.frames_received | Should Be $null
        $delta.paper.transaction_counts_available | Should Be $false
        $delta.paper.transaction_counts | Should Be $null
        $delta.paper.quote_quality_available | Should Be $false
        $delta.paper.processing_diagnostics_available | Should Be $false
    }
}
