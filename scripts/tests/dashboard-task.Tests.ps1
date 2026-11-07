. (Join-Path $PSScriptRoot '../lib/dashboard-task.ps1')

Describe 'P7 dashboard startup gate' {
    $healthy = @{
        springConnected = $true
        pythonConnected = $true
        operations = @{
            tradingMode = 'paper'; liveTrading = 'disabled'; paperAutomationEnabled = $false
            kisPaperOrdersEnabled = $false; kisRealOrdersEnabled = $false
            kisRealAccountReadsEnabled = $false; kisRealReconciliationEnabled = $false
        }
        marketAi = @{ live_trading_enabled = $false; paper_automation_enabled = $false }
    }

    It 'accepts connected Spring and FastAPI with every trading switch locked' {
        Test-DashboardOperationsReady $healthy | Should Be $true
    }

    It 'rejects a missing operations response' {
        Test-DashboardOperationsReady $null | Should Be $false
    }

    It 'rejects an unavailable Spring API' {
        $response = $healthy.Clone(); $response.springConnected = $false
        Test-DashboardOperationsReady $response | Should Be $false
    }

    It 'rejects an unavailable FastAPI' {
        $response = $healthy.Clone(); $response.pythonConnected = $false
        Test-DashboardOperationsReady $response | Should Be $false
    }

    It 'rejects a non-PAPER mode' {
        $response = $healthy.Clone(); $response.operations = $healthy.operations.Clone(); $response.operations.tradingMode = 'real'
        Test-DashboardOperationsReady $response | Should Be $false
    }

    It 'rejects live orders, PAPER orders, or automation enabled' {
        foreach ($mutation in @(
            @{ section='operations'; key='liveTrading'; value='enabled' },
            @{ section='operations'; key='kisPaperOrdersEnabled'; value=$true },
            @{ section='operations'; key='kisRealOrdersEnabled'; value=$true },
            @{ section='operations'; key='paperAutomationEnabled'; value=$true },
            @{ section='marketAi'; key='paper_automation_enabled'; value=$true }
        )) {
            $response = $healthy.Clone()
            $response[$mutation.section] = $healthy[$mutation.section].Clone()
            $response[$mutation.section][$mutation.key] = $mutation.value
            Test-DashboardOperationsReady $response | Should Be $false
        }
    }
}
