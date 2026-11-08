. (Join-Path $PSScriptRoot '../lib/us-paper-lifecycle-smoke.ps1')

function Test-KisUsPaperSmokeQuoteRejected {
    param([Parameter(Mandatory)] [object]$Quote)

    try {
        Get-KisUsPaperSmokeQuotePrice -Quote $Quote -Symbol 'F' -Exchange NYS | Out-Null
        return $false
    } catch {
        return $true
    }
}

Describe 'US KIS PAPER lifecycle smoke limit price' {
    It 'accepts a fresh-quote contract for the exact US symbol and exchange' {
        $quote = @{ market = 'US'; symbol = 'F'; exchange = 'NYS'; currency = 'USD'; price = '13.42' }
        (Get-KisUsPaperSmokeQuotePrice -Quote $quote -Symbol 'F' -Exchange NYS) | Should Be ([decimal]13.42)
    }

    It 'rejects a quote returned for a different listing exchange' {
        (Test-KisUsPaperSmokeQuoteRejected -Quote @{ market = 'US'; symbol = 'F'; exchange = 'NAS'; currency = 'USD'; price = '13.42' }) | Should Be $true
    }

    It 'rejects a quote for another symbol or market' {
        (Test-KisUsPaperSmokeQuoteRejected -Quote @{ market = 'US'; symbol = 'AAPL'; exchange = 'NYS'; currency = 'USD'; price = '13.42' }) | Should Be $true
        (Test-KisUsPaperSmokeQuoteRejected -Quote @{ market = 'KR'; symbol = 'F'; exchange = 'NYS'; currency = 'USD'; price = '13.42' }) | Should Be $true
    }

    It 'rejects a non-USD quote before an order can be prepared' {
        (Test-KisUsPaperSmokeQuoteRejected -Quote @{ market = 'US'; symbol = 'F'; exchange = 'NYS'; currency = 'KRW'; price = '13.42' }) | Should Be $true
    }

    It 'rejects a malformed or non-positive quote price' {
        (Test-KisUsPaperSmokeQuoteRejected -Quote @{ market = 'US'; symbol = 'F'; exchange = 'NYS'; currency = 'USD'; price = 'unavailable' }) | Should Be $true
        (Test-KisUsPaperSmokeQuoteRejected -Quote @{ market = 'US'; symbol = 'F'; exchange = 'NYS'; currency = 'USD'; price = '0' }) | Should Be $true
        (Test-KisUsPaperSmokeQuoteRejected -Quote @{ market = 'US'; symbol = 'F'; exchange = 'NYS'; currency = 'USD'; price = '-1' }) | Should Be $true
    }

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
        $script | Should Match 'Get-KisUsPaperSmokeQuotePrice -Quote \$quote -Symbol \$smokeSymbol -Exchange \$smokeQuoteExchange'
        $script | Should Not Match 'exchange=NASD|Exchange NASD'
    }
}
