. (Join-Path $PSScriptRoot '../lib/paper-kis-smoke.ps1')

Describe 'PAPER KIS read-only smoke market scope' {
    It 'selects only domestic market by default' {
        @(Get-PaperKisSmokeMarkets).Count | Should Be 1
        @(Get-PaperKisSmokeMarkets)[0] | Should Be 'KR'
    }

    It 'does not expose an overseas opt-in on broker-backed smoke checks' {
        (Get-Command Get-PaperKisSmokeMarkets).Parameters.ContainsKey('IncludeUS') | Should Be $false
    }
}
