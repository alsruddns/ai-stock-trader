. (Join-Path $PSScriptRoot '../lib/paper-kis-smoke.ps1')

Describe 'PAPER KIS read-only smoke market scope' {
    It 'selects only domestic market by default' {
        @(Get-PaperKisSmokeMarkets).Count | Should Be 1
        @(Get-PaperKisSmokeMarkets)[0] | Should Be 'KR'
    }

    It 'adds the US market only when explicitly requested' {
        $markets = @(Get-PaperKisSmokeMarkets -IncludeUS)
        $markets.Count | Should Be 2
        $markets[0] | Should Be 'KR'
        $markets[1] | Should Be 'US'
    }
}
