. (Join-Path $PSScriptRoot '../paper-session-monitor-status.ps1')

Describe 'Paper session monitor failure codes' {
    It 'classifies a stalled WebSocket without persisting the raw exception text' {
        Get-MonitorFailureCode 'No WebSocket ticks arrived for more than 300 seconds.' |
            Should Be 'WEBSOCKET_NO_TICKS'
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
