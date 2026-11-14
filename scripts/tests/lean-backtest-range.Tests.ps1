. (Join-Path $PSScriptRoot '../lib/lean-backtest-range.ps1')

function Assert-ThrowsContaining {
    param([scriptblock]$Action, [string]$ExpectedText)
    $message = $null
    try { & $Action | Out-Null } catch { $message = $_.Exception.Message }
    $message | Should Match ([regex]::Escape($ExpectedText))
}

Describe 'LEAN multi-session backtest summary' {
    BeforeEach {
        $script:results = @(
            [pscustomobject]@{
                market = 'KR'; symbol = '005930'; session = '2026-11-13'; bars = 380
                signals = 0; trades = 0; orders = 0; python_net_pnl = '0'; lean_net_pnl = '0'
                pnl_delta = '0'; parity = 'passed'; model_id = ''; model_artifact_sha256 = ''
                replay_session = '2026-11-13'; dataset_sha256 = ('a' * 64); staged_dataset_sha256 = ('b' * 64)
                rights_reviewed = $true; cost_source = 'env_local'
                cost_assumptions_bps = @{ buy = '1'; sell = '2' }
            },
            [pscustomobject]@{
                market = 'KR'; symbol = '005930'; session = '2026-11-11'; bars = 380
                signals = 3; trades = 3; orders = 6; python_net_pnl = '-1728.25'; lean_net_pnl = '-1728.25'
                pnl_delta = '0'; parity = 'passed'; model_id = ''; model_artifact_sha256 = ''
                replay_session = '2026-11-11'; dataset_sha256 = ('c' * 64); staged_dataset_sha256 = ('d' * 64)
                rights_reviewed = $true; cost_source = 'env_local'
                cost_assumptions_bps = @{ buy = '1'; sell = '2' }
            }
        )
    }

    It 'sorts dates and labels net P&L as an independent-session sum' {
        $summary = New-LeanBacktestRangeSummary -Market KR -Symbol '005930' -SessionResults $script:results

        $summary.replay_type | Should Be 'independent_single_session_replays'
        $summary.session_count | Should Be 2
        $summary.sessions[0].session | Should Be '2026-11-11'
        $summary.independent_session_net_pnl_sum | Should Be '-1728.25'
        $summary.executable | Should Be $false
    }

    It 'rejects a failed parity result' {
        $script:results[0].parity = 'failed'
        Assert-ThrowsContaining {
            New-LeanBacktestRangeSummary -Market KR -Symbol '005930' -SessionResults $script:results
        } 'parity did not pass'
    }

    It 'rejects duplicate session dates' {
        $script:results[0].session = '2026-11-11'
        Assert-ThrowsContaining {
            New-LeanBacktestRangeSummary -Market KR -Symbol '005930' -SessionResults $script:results
        } 'duplicate session'
    }

    It 'rejects mixed symbols and inconsistent costs' {
        $script:results[0].symbol = '000660'
        Assert-ThrowsContaining {
            New-LeanBacktestRangeSummary -Market KR -Symbol '005930' -SessionResults $script:results
        } 'match the requested market and symbol'

        $script:results[0].symbol = '005930'
        $script:results[0].cost_assumptions_bps = @{ buy = '9'; sell = '2' }
        Assert-ThrowsContaining {
            New-LeanBacktestRangeSummary -Market KR -Symbol '005930' -SessionResults $script:results
        } 'identical cost assumptions'
    }

    It 'rejects mixed model artifacts' {
        $script:results[0].model_id = 'candidate-b'
        $script:results[0].model_artifact_sha256 = 'hash-b'
        $script:results[1].model_id = 'candidate-a'
        $script:results[1].model_artifact_sha256 = 'hash-a'
        Assert-ThrowsContaining {
            New-LeanBacktestRangeSummary -Market KR -Symbol '005930' -SessionResults $script:results
        } 'same model artifact'
    }

    It 'preserves dataset hashes and rights review for each session' {
        $summary = New-LeanBacktestRangeSummary -Market KR -Symbol '005930' -SessionResults $script:results

        $summary.cost_source | Should Be 'env_local'
        $summary.sessions[0].dataset_sha256 | Should Be ('c' * 64)
        $summary.sessions[0].staged_dataset_sha256 | Should Be ('d' * 64)
        $summary.sessions[0].rights_reviewed | Should Be $true

        $script:results[0].rights_reviewed = $false
        Assert-ThrowsContaining {
            New-LeanBacktestRangeSummary -Market KR -Symbol '005930' -SessionResults $script:results
        } 'rights have not been reviewed'
    }
}
