. (Join-Path $PSScriptRoot '../lib/kis-paper-order-preflight.ps1')

function Assert-QuoteExpectation {
    param([object]$Quote, [DateTimeOffset]$Now, [bool]$ShouldThrow)
    $didThrow = $false
    try {
        Assert-KisPaperQuoteFresh -Quote $Quote -Now $Now
    } catch {
        $didThrow = $true
    }
    $didThrow | Should Be $ShouldThrow
}

Describe 'KIS PAPER quote freshness preflight' {
    $now = [DateTimeOffset]::Parse('2026-11-06T08:30:00Z')

    It 'accepts a recent UTC DateTime returned by Invoke-RestMethod' {
        $quote = @{ retrieved_at = $now.AddSeconds(-10).UtcDateTime }
        Assert-QuoteExpectation -Quote $quote -Now $now -ShouldThrow $false
    }

    It 'accepts a recent ISO 8601 timestamp with an explicit offset' {
        $quote = @{ retrieved_at = $now.AddSeconds(-10).ToString('o') }
        Assert-QuoteExpectation -Quote $quote -Now $now -ShouldThrow $false
    }

    It 'rejects a quote older than 60 seconds' {
        $quote = @{ retrieved_at = $now.AddSeconds(-61).UtcDateTime }
        Assert-QuoteExpectation -Quote $quote -Now $now -ShouldThrow $true
    }

    It 'rejects a timestamp more than five seconds in the future' {
        $quote = @{ retrieved_at = $now.AddSeconds(6).UtcDateTime }
        Assert-QuoteExpectation -Quote $quote -Now $now -ShouldThrow $true
    }

    It 'rejects a missing timestamp' {
        $quote = @{ retrieved_at = $null }
        Assert-QuoteExpectation -Quote $quote -Now $now -ShouldThrow $true
    }

    It 'rejects a timestamp without an explicit timezone' {
        $quote = @{ retrieved_at = '2026-11-06T08:29:50' }
        Assert-QuoteExpectation -Quote $quote -Now $now -ShouldThrow $true
    }
}
