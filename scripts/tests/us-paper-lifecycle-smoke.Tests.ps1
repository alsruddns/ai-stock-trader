. (Join-Path $PSScriptRoot '../lib/us-paper-lifecycle-smoke.ps1')

Describe 'US KIS PAPER lifecycle smoke limit price' {
    It 'sets a one-share limit at least five percent below quote, rounded down to cents when affordable' {
        (Get-KisUsPaperSmokeLimitPrice -Price ([decimal]10.05)) | Should Be ([decimal]9.54)
    }

    It 'caps higher-price symbols at ten dollars for a low-balance PAPER lifecycle check' {
        (Get-KisUsPaperSmokeLimitPrice -Price ([decimal]20)) | Should Be ([decimal]10.00)
    }

    It 'keeps the lower quote-based limit for symbols below the smoke cap' {
        (Get-KisUsPaperSmokeLimitPrice -Price ([decimal]8)) | Should Be ([decimal]7.60)
    }

    It 'rejects a non-positive price' {
        $didThrow = $false
        try { Get-KisUsPaperSmokeLimitPrice -Price ([decimal]0) } catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

    It 'rejects a positive price that rounds down to zero' {
        $didThrow = $false
        try { Get-KisUsPaperSmokeLimitPrice -Price ([decimal]0.01) } catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

    It 'refuses to run without the explicit PAPER smoke switch before reading the local environment' {
        $scriptPath = Join-Path $PSScriptRoot '../run-us-paper-lifecycle-smoke.ps1'
        $message = $null
        try { & $scriptPath } catch { $message = $_.Exception.Message }
        $message | Should Be 'No request was sent. The one-share PAPER lifecycle smoke requires -ConfirmPaperLifecycleSmoke.'
    }

    It 'uses the NYSE listing code for Ford in both quote and PAPER order requests' {
        $scriptPath = Join-Path $PSScriptRoot '../run-us-paper-lifecycle-smoke.ps1'
        $script = Get-Content -LiteralPath $scriptPath -Raw
        $script | Should Match '\$smokeSymbol = ''F'''
        $script | Should Match '\$smokeOrderExchange = ''NYSE'''
        $script | Should Match 'ConvertTo-KisQuoteExchange -Exchange \$smokeOrderExchange'
        $script | Should Match '\$smokeQuoteExchange'
        $script | Should Match '-Exchange \$smokeOrderExchange'
        $script | Should Not Match 'exchange=NASD|Exchange NASD'
    }
}
