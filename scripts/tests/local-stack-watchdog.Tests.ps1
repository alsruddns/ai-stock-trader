$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../lib/local-stack-activation.ps1')

function Test-LocalStackReadinessRejects($PythonHealth, $SpringSummary) {
    try {
        Assert-LocalStackReadyResponses -PythonHealth $PythonHealth -SpringSummary $SpringSummary
        return $false
    } catch {
        return $true
    }
}

Describe 'Local stack API listener watchdog' {
    It 'accepts the Spring and FastAPI listeners only on loopback' {
        $listeners = @(
            [pscustomobject]@{ LocalPort = 18000; LocalAddress = '127.0.0.1'; OwningProcess = 1001 },
            [pscustomobject]@{ LocalPort = 18080; LocalAddress = '127.0.0.1'; OwningProcess = 1002 }
        )
        (Test-LocalStackApiListenersReady -Listeners $listeners) | Should Be $true
    }

    It 'rejects a missing API listener' {
        $listeners = @(
            [pscustomobject]@{ LocalPort = 18080; LocalAddress = '127.0.0.1'; OwningProcess = 1002 }
        )
        (Test-LocalStackApiListenersReady -Listeners $listeners) | Should Be $false
    }

    It 'rejects a listener exposed outside loopback' {
        $listeners = @(
            [pscustomobject]@{ LocalPort = 18000; LocalAddress = '0.0.0.0'; OwningProcess = 1001 },
            [pscustomobject]@{ LocalPort = 18080; LocalAddress = '127.0.0.1'; OwningProcess = 1002 }
        )
        (Test-LocalStackApiListenersReady -Listeners $listeners) | Should Be $false
    }

    It 'rejects ambiguous listeners for the same API port' {
        $listeners = @(
            [pscustomobject]@{ LocalPort = 18000; LocalAddress = '127.0.0.1'; OwningProcess = 1001 },
            [pscustomobject]@{ LocalPort = 18000; LocalAddress = '127.0.0.1'; OwningProcess = 1003 },
            [pscustomobject]@{ LocalPort = 18080; LocalAddress = '127.0.0.1'; OwningProcess = 1002 }
        )
        (Test-LocalStackApiListenersReady -Listeners $listeners) | Should Be $false
    }
}

Describe 'Local stack API health watchdog safety response' {
    It 'accepts healthy PAPER responses with automated trading locked' {
        $pythonHealth = [pscustomobject]@{
            status = 'ok'; live_trading_enabled = $false; paper_automation_enabled = $false
        }
        $springSummary = [pscustomobject]@{
            tradingMode = 'paper'; liveTrading = 'disabled'; paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
            kisPaperHistoryCancelFallbackEnabled = $false
        }
        { Assert-LocalStackReadyResponses -PythonHealth $pythonHealth -SpringSummary $springSummary } | Should Not Throw
    }

    It 'rejects an unhealthy Python API response' {
        $pythonHealth = [pscustomobject]@{
            status = 'unavailable'; live_trading_enabled = $false; paper_automation_enabled = $false
        }
        $springSummary = [pscustomobject]@{
            tradingMode = 'paper'; liveTrading = 'disabled'; paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
            kisPaperHistoryCancelFallbackEnabled = $false
        }
        (Test-LocalStackReadinessRejects $pythonHealth $springSummary) | Should Be $true
    }

    It 'rejects live or PAPER automation enabled in Python' {
        $pythonHealth = [pscustomobject]@{
            status = 'ok'; live_trading_enabled = $true; paper_automation_enabled = $false
        }
        $springSummary = [pscustomobject]@{
            tradingMode = 'paper'; liveTrading = 'disabled'; paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
            kisPaperHistoryCancelFallbackEnabled = $false
        }
        (Test-LocalStackReadinessRejects $pythonHealth $springSummary) | Should Be $true
        $pythonHealth.live_trading_enabled = $false
        $pythonHealth.paper_automation_enabled = $true
        (Test-LocalStackReadinessRejects $pythonHealth $springSummary) | Should Be $true
    }

    It 'rejects a non-PAPER Spring mode or enabled REAL orders' {
        $pythonHealth = [pscustomobject]@{
            status = 'ok'; live_trading_enabled = $false; paper_automation_enabled = $false
        }
        $springSummary = [pscustomobject]@{
            tradingMode = 'real'; liveTrading = 'disabled'; paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
            kisPaperHistoryCancelFallbackEnabled = $false
        }
        (Test-LocalStackReadinessRejects $pythonHealth $springSummary) | Should Be $true
        $springSummary.tradingMode = 'paper'
        $springSummary.kisRealOrdersEnabled = $true
        (Test-LocalStackReadinessRejects $pythonHealth $springSummary) | Should Be $true
    }

    It 'records which local API failed without exposing exception or response details' {
        $python = Get-LocalStackHealthComponentOutcome -Component Python -FailureCategory 'TIMEOUT'
        $spring = Get-LocalStackHealthComponentOutcome -Component Spring -Response ([pscustomobject]@{
            tradingMode = 'paper'; liveTrading = 'disabled'; paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
            kisPaperHistoryCancelFallbackEnabled = $false
        })
        $summary = Get-LocalStackHealthFailureSummary -PythonFailureCategory $python -SpringSummary ([pscustomobject]@{
            tradingMode = 'paper'; liveTrading = 'disabled'; paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
            kisPaperHistoryCancelFallbackEnabled = $false
        })

        $summary | Should Be 'python=TIMEOUT; spring=PAPER_SAFE'
        $summary | Should Not Match 'secret|account|response body'
    }

    It 'normalizes unrecognized health failure categories' {
        Get-LocalStackHealthComponentOutcome -Component Spring -FailureCategory 'password=hidden' |
            Should Be 'REQUEST_FAILURE'
    }

    It 'retries transient API timeouts before restarting the local stack' {
        Get-LocalStackHealthRecoveryDecision -FailureSummary 'python=TIMEOUT; spring=TIMEOUT' `
            -ConsecutiveFailureCount 2 -RestartThreshold 4 | Should Be 'RETRY'
        Get-LocalStackHealthRecoveryDecision -FailureSummary 'python=TIMEOUT; spring=PAPER_SAFE' `
            -ConsecutiveFailureCount 3 -RestartThreshold 4 | Should Be 'RETRY'
        Get-LocalStackHealthRecoveryDecision -FailureSummary 'python=TIMEOUT; spring=TIMEOUT' `
            -ConsecutiveFailureCount 4 -RestartThreshold 4 | Should Be 'RESTART'
    }

    It 'restarts immediately when an API reports trading unlocked or missing safety fields' {
        Get-LocalStackHealthRecoveryDecision -FailureSummary 'python=TRADING_UNLOCKED; spring=PAPER_SAFE' `
            -ConsecutiveFailureCount 1 -RestartThreshold 4 | Should Be 'RESTART'
        Get-LocalStackHealthRecoveryDecision -FailureSummary 'python=PAPER_SAFE; spring=MISSING_SAFETY_FIELDS' `
            -ConsecutiveFailureCount 1 -RestartThreshold 4 | Should Be 'RESTART'
    }

    It 'rejects the experimental PAPER history-cancel fallback in the managed service' {
        $pythonHealth = [pscustomobject]@{
            status = 'ok'; live_trading_enabled = $false; paper_automation_enabled = $false
        }
        $springSummary = [pscustomobject]@{
            tradingMode = 'paper'; liveTrading = 'disabled'; paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
            kisPaperHistoryCancelFallbackEnabled = $true
        }
        (Test-LocalStackReadinessRejects $pythonHealth $springSummary) | Should Be $true
    }
}
