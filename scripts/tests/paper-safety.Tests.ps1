. (Join-Path $PSScriptRoot '..\lib\paper-safety.ps1')

Describe 'Scheduled model work PAPER safety gate' {
    It 'accepts only PAPER mode with every order and automation switch disabled' {
        $settings = @{
            TRADING_MODE = 'paper'
            LIVE_TRADING_ENABLED = 'false'
            KIS_PAPER_ORDERS_ENABLED = 'false'
            KIS_REAL_ORDERS_ENABLED = 'false'
            REAL_TRADING_APPROVAL_CONFIRMED = 'false'
            PAPER_AUTOTRADING_ENABLED = 'false'
        }

        $didThrow = $false
        try { Assert-AiTraderPaperSafetyLocks -Settings $settings } catch { $didThrow = $true }
        $didThrow | Should Be $false
    }

    It 'rejects a missing local setting' {
        $settings = @{ TRADING_MODE = 'paper' }

        $message = $null
        try { Assert-AiTraderPaperSafetyLocks -Settings $settings } catch { $message = $_.Exception.Message }
        $message | Should Match 'Required local safety setting'
    }

    It 'rejects live mode or any enabled order/automation switch' {
        $keys = @('TRADING_MODE', 'LIVE_TRADING_ENABLED', 'KIS_PAPER_ORDERS_ENABLED',
            'KIS_REAL_ORDERS_ENABLED', 'REAL_TRADING_APPROVAL_CONFIRMED', 'PAPER_AUTOTRADING_ENABLED')
        foreach ($key in $keys) {
            $settings = @{
                TRADING_MODE = 'paper'
                LIVE_TRADING_ENABLED = 'false'
                KIS_PAPER_ORDERS_ENABLED = 'false'
                KIS_REAL_ORDERS_ENABLED = 'false'
                REAL_TRADING_APPROVAL_CONFIRMED = 'false'
                PAPER_AUTOTRADING_ENABLED = 'false'
            }
            $settings[$key] = if ($key -eq 'TRADING_MODE') { 'real' } else { 'true' }

            $message = $null
            try { Assert-AiTraderPaperSafetyLocks -Settings $settings } catch { $message = $_.Exception.Message }
            $message | Should Match 'PAPER mode'
        }
    }

    It 'reads only local safety settings without displaying credential values' {
        $directory = Join-Path $TestDrive 'repo'
        New-Item -ItemType Directory -Path $directory | Out-Null
        @'
TRADING_MODE=paper
LIVE_TRADING_ENABLED=false
KIS_PAPER_ORDERS_ENABLED=false
KIS_REAL_ORDERS_ENABLED=false
REAL_TRADING_APPROVAL_CONFIRMED=false
PAPER_AUTOTRADING_ENABLED=false
KIS_APP_SECRET=TEST_ONLY_PLACEHOLDER
'@ | Set-Content -LiteralPath (Join-Path $directory '.env_local')

        $settings = Read-AiTraderLocalSafetySettings -RepositoryRoot $directory

        $settings.TRADING_MODE | Should Be 'paper'
        ($settings | ConvertTo-Json -Compress) | Should Not Match 'TEST_ONLY_PLACEHOLDER'
    }
}
