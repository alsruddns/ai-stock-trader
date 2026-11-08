. (Join-Path $PSScriptRoot '../lib/lean-cost-inputs.ps1')

Describe 'LEAN backtest cost inputs' {
    It 'reads only the selected market and all four execution cost settings' {
        $environmentFile = Join-Path $TestDrive 'costs.env'
        @(
            'BACKTEST_KR_BUY_COMMISSION_BPS=101'
            'BACKTEST_US_BUY_COMMISSION_BPS=7'
            'BACKTEST_US_SELL_COMMISSION_BPS=9'
            'BACKTEST_US_SELL_TAX_BPS=2'
            'BACKTEST_US_SLIPPAGE_BPS=3'
            'UNRELATED_SECRET=must-not-be-read'
        ) | Set-Content -LiteralPath $environmentFile -Encoding ASCII

        $settings = Get-LeanBacktestCostInputs -Market US -EnvironmentFile $environmentFile

        $settings.Source | Should Be 'env_local'
        $settings.BuyCommissionBps | Should Be '7'
        $settings.SellCommissionBps | Should Be '9'
        $settings.SellTaxBps | Should Be '2'
        $settings.SlippageBps | Should Be '3'
    }

    It 'uses labeled diagnostic defaults when no selected-market costs exist' {
        $settings = Get-LeanBacktestCostInputs -Market KR -EnvironmentFile (Join-Path $TestDrive 'missing.env')

        $settings.Source | Should Be 'diagnostic_defaults'
        $settings.BuyCommissionBps | Should Be '5'
        $settings.SellCommissionBps | Should Be '5'
        $settings.SellTaxBps | Should Be '0'
        $settings.SlippageBps | Should Be '5'
    }

    It 'rejects partial environment cost configuration' {
        $environmentFile = Join-Path $TestDrive 'partial.env'
        'BACKTEST_KR_BUY_COMMISSION_BPS=7' | Set-Content -LiteralPath $environmentFile -Encoding ASCII

        $message = $null
        try { Get-LeanBacktestCostInputs -Market KR -EnvironmentFile $environmentFile | Out-Null }
        catch { $message = $_.Exception.Message }

        $message | Should Match 'Incomplete KR backtest cost assumptions'
    }

    It 'allows a complete explicit diagnostic set to override environment values' {
        $environmentFile = Join-Path $TestDrive 'override.env'
        @(
            'BACKTEST_US_BUY_COMMISSION_BPS=1'
            'BACKTEST_US_SELL_COMMISSION_BPS=1'
            'BACKTEST_US_SELL_TAX_BPS=1'
            'BACKTEST_US_SLIPPAGE_BPS=1'
        ) | Set-Content -LiteralPath $environmentFile -Encoding ASCII

        $settings = Get-LeanBacktestCostInputs -Market US -EnvironmentFile $environmentFile `
            -BuyCommissionBps '7' -SellCommissionBps '9' -SellTaxBps '2' -SlippageBps '3'

        $settings.Source | Should Be 'explicit_parameters'
        $settings.BuyCommissionBps | Should Be '7'
        $settings.SellCommissionBps | Should Be '9'
        $settings.SellTaxBps | Should Be '2'
        $settings.SlippageBps | Should Be '3'
    }

    It 'rejects a partial explicit diagnostic set' {
        $message = $null
        try {
            Get-LeanBacktestCostInputs -Market US -EnvironmentFile (Join-Path $TestDrive 'missing.env') `
                -BuyCommissionBps '7' | Out-Null
        } catch { $message = $_.Exception.Message }

        $message | Should Match 'Pass all four explicit cost values together'
    }

    It 'rejects negative and out-of-range cost settings' {
        $environmentFile = Join-Path $TestDrive 'too-large.env'
        @(
            'BACKTEST_US_BUY_COMMISSION_BPS=1'
            'BACKTEST_US_SELL_COMMISSION_BPS=1'
            'BACKTEST_US_SELL_TAX_BPS=10001'
            'BACKTEST_US_SLIPPAGE_BPS=1'
        ) | Set-Content -LiteralPath $environmentFile -Encoding ASCII

        $message = $null
        try { Get-LeanBacktestCostInputs -Market US -EnvironmentFile $environmentFile | Out-Null }
        catch { $message = $_.Exception.Message }
        $message | Should Match 'zero through 10000'

        $message = $null
        try {
            Get-LeanBacktestCostInputs -Market US -EnvironmentFile (Join-Path $TestDrive 'missing.env') `
                -BuyCommissionBps '-1' -SellCommissionBps '0' -SellTaxBps '0' -SlippageBps '0' | Out-Null
        } catch { $message = $_.Exception.Message }
        $message | Should Match 'zero through 10000'
    }
}
