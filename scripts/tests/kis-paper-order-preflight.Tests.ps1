. (Join-Path $PSScriptRoot '../lib/kis-paper-order-preflight.ps1')
. (Join-Path $PSScriptRoot '../lib/kis-paper-order-lifecycle.ps1')

Describe 'KIS PAPER order lifecycle completion result' {
    It 'reports a zero-fill cancellation as successful' {
        $result = Get-KisPaperOrderLifecycleResult -Intent @{ status = 'CANCELLED'; filledQuantity = 0 }
        $result.exitCode | Should Be 0
        $result.filledQuantity | Should Be '0'
    }

    It 'flags a cancelled order with a partial fill for position review' {
        $result = Get-KisPaperOrderLifecycleResult -Intent @{ status = 'CANCELLED'; quantity = 3; filledQuantity = 1 }
        $result.exitCode | Should Be 2
        $result.filledQuantity | Should Be '1'
        $result.message | Should Match 'partial fill of 1 share'
        $result.message | Should Match 'remaining simulated position'
    }

    It 'flags a one-share order filled before its cancellation' {
        $result = Get-KisPaperOrderLifecycleResult -Intent @{ status = 'CANCELLED'; quantity = 1; filledQuantity = 1 }
        $result.exitCode | Should Be 2
        $result.message | Should Match 'All 1 requested PAPER share'
    }

    It 'fails closed when a cancelled response omits filled quantity' {
        $result = Get-KisPaperOrderLifecycleResult -Intent @{ status = 'CANCELLED'; quantity = 1; filledQuantity = $null }
        $result.exitCode | Should Be 2
        $result.filledQuantity | Should Be 'unknown'
        $result.message | Should Match 'missing a valid fill or order quantity'
    }

    It 'flags a fill even if the broker does not return the FILLED status' {
        $result = Get-KisPaperOrderLifecycleResult -Intent @{ status = 'CANCEL_PENDING'; filledQuantity = 1 }
        $result.exitCode | Should Be 2
        $result.message | Should Match 'simulated fill occurred'
    }

    It 'reports a terminal rejection with zero fills separately from an unresolved state' {
        $result = Get-KisPaperOrderLifecycleResult -Intent @{ status = 'REJECTED'; filledQuantity = 0 }
        $result.exitCode | Should Be 1
        $result.filledQuantity | Should Be '0'
        $result.message | Should Match 'rejected the order with zero fills'
    }
}

Describe 'KIS PAPER submit and observe result' {
    It 'accepts a broker-accepted zero-fill order but warns that it remains open' {
        $result = Get-KisPaperOrderSubmitObservationResult -Intent @{ status = 'ACCEPTED'; filledQuantity = 0 }
        $result.exitCode | Should Be 2
        $result.orderRemainsOpen | Should Be $true
        $result.message | Should Match 'incomplete lifecycle check; do not resubmit'
        $result.message | Should Match 'Cancelability was not tested'
    }

    It 'requires position review after any observed simulated fill' {
        $result = Get-KisPaperOrderSubmitObservationResult -Intent @{ status = 'PARTIALLY_FILLED'; filledQuantity = 1 }
        $result.exitCode | Should Be 2
        $result.orderRemainsOpen | Should Be $false
        $result.message | Should Match 'review and reconcile the PAPER position'
    }

    It 'reports a clean broker rejection separately' {
        $result = Get-KisPaperOrderSubmitObservationResult -Intent @{ status = 'REJECTED'; filledQuantity = 0 }
        $result.exitCode | Should Be 1
        $result.orderRemainsOpen | Should Be $false
    }

    It 'fails closed for an unknown status or missing fill quantity' {
        $result = Get-KisPaperOrderSubmitObservationResult -Intent @{ status = 'UNKNOWN'; filledQuantity = $null }
        $result.exitCode | Should Be 2
        $result.orderRemainsOpen | Should Be $null
        $result.message | Should Match 'Do not resubmit'
    }
}

Describe 'Isolated KIS PAPER lifecycle port' {
    It 'defaults the temporary Spring API to the dedicated 18081 loopback port' {
        $tokens = $null
        $errors = $null
        $scriptPath = Join-Path $PSScriptRoot '../test-kis-paper-order-lifecycle-isolated.ps1'
        $ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$errors)
        $errors.Count | Should Be 0
        $portParameter = $ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'TemporaryPort' }
        $portParameter.DefaultValue.SafeGetValue() | Should Be 18081
    }
}

Describe 'Domestic KIS PAPER lifecycle fail-closed guard' {
    $expected = 'Domestic PAPER cancel preflight is unsupported; no order was submitted. Use -PreflightOnly or wait until KIS documents a PAPER cancel-quantity query.'

    It 'stops the direct lifecycle script before local API calls' {
        $message = $null
        try {
            & (Join-Path $PSScriptRoot '../test-kis-paper-order-lifecycle.ps1') `
                -Market KR -Symbol '069500' -LimitPrice 50000 -ConfirmPaperOrderAndCancel
        } catch { $message = $_.Exception.Message }
        $message | Should Be $expected
    }

    It 'stops the isolated lifecycle runner before building or starting Spring' {
        $message = $null
        try {
            & (Join-Path $PSScriptRoot '../test-kis-paper-order-lifecycle-isolated.ps1') `
                -Market KR -Symbol '069500' -LimitPrice 50000 -ConfirmPaperOrderAndCancel
        } catch { $message = $_.Exception.Message }
        $message | Should Be $expected
    }
}

Describe 'KIS PAPER submit-and-observe opt-in guard' {
    It 'requires the explicit isolated PAPER order enable before loading credentials or calling an API' {
        $previous = $env:KIS_PAPER_ORDERS_ENABLED
        try {
            $env:KIS_PAPER_ORDERS_ENABLED = 'false'
            $message = $null
            try {
                & (Join-Path $PSScriptRoot '../test-kis-paper-order-lifecycle.ps1') `
                    -Market KR -Symbol '069500' -LimitPrice 50000 -ConfirmPaperOrderSubmitAndObserve
            } catch { $message = $_.Exception.Message }
            $message | Should Be 'PAPER order submission requires an explicit one-run enable switch or a PAPER-orders-enabled isolated service.'
        } finally {
            if ($null -eq $previous) { Remove-Item Env:KIS_PAPER_ORDERS_ENABLED -ErrorAction SilentlyContinue }
            else { $env:KIS_PAPER_ORDERS_ENABLED = $previous }
        }
    }

    It 'never sends a cancellation request from the submit-and-observe branch' {
        $scriptText = Get-Content (Join-Path $PSScriptRoot '../test-kis-paper-order-lifecycle.ps1') -Raw
        $submitStart = $scriptText.IndexOf('if ($ConfirmPaperOrderSubmitAndObserve) {')
        $cancelStart = $scriptText.IndexOf('$cancelUri =', $submitStart)
        $submitStart | Should BeGreaterThan -1
        $cancelStart | Should BeGreaterThan $submitStart
        $submitBranch = $scriptText.Substring($submitStart, $cancelStart - $submitStart)
        $submitBranch | Should Not Match '\$cancelUri|/cancel'
        $submitBranch | Should Match 'do not resubmit'
    }
}

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

    It 'accepts sufficient fresh orderable funds for the requested market and currency' {
        $account = @{
            environment = 'paper'; market = 'KR'; currency = 'KRW'; orderableCash = '100000'
            retrievedAt = $now.AddSeconds(-10).UtcDateTime
        }
        $didThrow = $false
        try { Assert-KisPaperOrderableFunds -Account $account -Market KR -Currency KRW -MinimumNotional 75000 -Now $now }
        catch { $didThrow = $true }
        $didThrow | Should Be $false
    }

    It 'rejects insufficient orderable funds' {
        $account = @{
            environment = 'paper'; market = 'KR'; currency = 'KRW'; orderableCash = '74999'
            retrievedAt = $now.AddSeconds(-10).UtcDateTime
        }
        Assert-QuoteExpectation -Quote @{ retrieved_at = $account.retrievedAt } -Now $now -ShouldThrow $false
        $didThrow = $false
        try { Assert-KisPaperOrderableFunds -Account $account -Market KR -Currency KRW -MinimumNotional 75000 -Now $now }
        catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

    It 'rejects an account snapshot for another environment or currency' {
        $account = @{
            environment = 'real'; market = 'KR'; currency = 'USD'; orderableCash = '100000'
            retrievedAt = $now.AddSeconds(-10).UtcDateTime
        }
        $didThrow = $false
        try { Assert-KisPaperOrderableFunds -Account $account -Market KR -Currency KRW -MinimumNotional 1 -Now $now }
        catch { $didThrow = $true }
        $didThrow | Should Be $true
    }

    It 'rejects missing orderable funds and stale account snapshots' {
        $missing = @{ environment='paper'; market='KR'; currency='KRW'; orderableCash=$null; retrievedAt=$now.UtcDateTime }
        $stale = @{ environment='paper'; market='KR'; currency='KRW'; orderableCash='100000'; retrievedAt=$now.AddSeconds(-61).UtcDateTime }
        $missingThrew = $false
        $staleThrew = $false
        try { Assert-KisPaperOrderableFunds -Account $missing -Market KR -Currency KRW -MinimumNotional 1 -Now $now }
        catch { $missingThrew = $true }
        try { Assert-KisPaperOrderableFunds -Account $stale -Market KR -Currency KRW -MinimumNotional 1 -Now $now }
        catch { $staleThrew = $true }
        $missingThrew | Should Be $true
        $staleThrew | Should Be $true
    }
}

Describe 'KIS quote exchange mapping' {
    It 'maps KIS account exchange codes to FastAPI quote exchange codes' {
        ConvertTo-KisQuoteExchange -Exchange NASD | Should Be 'NAS'
        ConvertTo-KisQuoteExchange -Exchange NAS | Should Be 'NAS'
        ConvertTo-KisQuoteExchange -Exchange NYSE | Should Be 'NYS'
        ConvertTo-KisQuoteExchange -Exchange NYS | Should Be 'NYS'
        ConvertTo-KisQuoteExchange -Exchange AMEX | Should Be 'AMS'
        ConvertTo-KisQuoteExchange -Exchange AMS | Should Be 'AMS'
    }
}

Describe 'Sanitized KIS error reporting' {
    It 'extracts a recognized KIS broker code from an API error detail' {
        Get-SafeKisBrokerCode -ErrorDetail '{"detail":"KIS quote request failed (HTTP 502, code EGW00002)."}' | Should Be 'EGW00002'
    }

    It 'does not expose arbitrary error values as broker codes' {
        Get-SafeKisBrokerCode -ErrorDetail '{"detail":"failed token=hidden code password123"}' | Should Be $null
    }

    It 'formats a broker failure using only the HTTP status, safe path, and recognized code' {
        $message = Format-SafeLocalApiFailure -Status '502' -Path '/api/v1/market/quotes/US/F' `
            -ErrorDetail '{"detail":"KIS quote request failed (HTTP 502, code EGW00002)."}'
        $message | Should Be 'Local preflight request failed (HTTP 502, KIS code EGW00002) for /api/v1/market/quotes/US/F; inspect the sanitized service logs.'
    }

    It 'formats an allowlisted upstream timeout without exposing response details' {
        $message = Format-SafeLocalApiFailure -Status '502' -Path '/api/v1/market/quotes/US/F' `
            -ErrorDetail '{"detail":"KIS quote request failed (transport category: timeout)."}'
        $message | Should Be 'Local preflight request failed (HTTP 502, KIS transport timeout) for /api/v1/market/quotes/US/F; inspect the sanitized service logs.'
    }

    It 'reports the KIS rate-limit category without exposing the broker response' {
        $message = Format-SafeLocalApiFailure -Status '429' -Path '/api/v1/kis/accounts/paper/KR/balance' `
            -ErrorDetail '{"detail":"KIS rate limit reached; wait before requesting again."}'
        $message | Should Be 'Local preflight request failed (HTTP 429, KIS rate limit) for /api/v1/kis/accounts/paper/KR/balance; inspect the sanitized service logs.'
        $message | Should Not Match 'wait before requesting'
    }

    It 'extracts an upstream HTTP status from a KIS-only error signature' {
        Get-SafeKisUpstreamHttpStatus -ErrorDetail '{"detail":"KIS quote request failed (HTTP 502, code unknown)."}' | Should Be '502'
    }

    It 'reports the KIS upstream status without treating it as a local status' {
        $message = Format-SafeLocalApiFailure -Status '502' -Path '/api/v1/market/quotes/US/F' `
            -ErrorDetail '{"detail":"KIS quote request failed (HTTP 502, code unknown)."}'
        $message | Should Be 'Local preflight request failed (HTTP 502, KIS upstream HTTP 502) for /api/v1/market/quotes/US/F; inspect the sanitized service logs.'
    }

    It 'reports only safe fields from a structured quote rejection' {
        $error = '{"detail":"KIS rejected the quote request (HTTP 200, code unknown, content-type application/json, response-bytes 123, fields rt_cd,msg_cd,msg1)"}'
        Get-SafeKisUpstreamHttpStatus -ErrorDetail $error | Should Be '200'
        Get-SafeKisResponseFields -ErrorDetail $error | Should Be 'rt_cd,msg_cd,msg1'
        $message = Format-SafeLocalApiFailure -Status '502' -Path '/api/v1/market/quotes/US/F' -ErrorDetail $error
        $message | Should Be 'Local preflight request failed (HTTP 502, KIS upstream HTTP 200, fields rt_cd,msg_cd,msg1) for /api/v1/market/quotes/US/F; inspect the sanitized service logs.'
        $message | Should Not Match '123|application/json|msg1=.*'
    }

    It 'rejects unsafe response field names' {
        $error = '{"detail":"KIS rejected the quote request (HTTP 200, code unknown, content-type application/json, response-bytes 123, fields token=secret)"}'
        Get-SafeKisResponseFields -ErrorDetail $error | Should Be $null
    }

    It 'marks an unclassified KIS detail while keeping its contents hidden' {
        $message = Format-SafeLocalApiFailure -Status '502' -Path '/api/v1/market/quotes/US/F' `
            -ErrorDetail '{"detail":"KIS error with private content"}'
        $message | Should Be 'Local preflight request failed (HTTP 502, KIS detail withheld) for /api/v1/market/quotes/US/F; inspect the sanitized service logs.'
        $message | Should Not Match 'private content'
    }

    It 'does not expose an unrecognized upstream category' {
        $message = Format-SafeLocalApiFailure -Status '502' -Path '/api/v1/market/quotes/US/F' `
            -ErrorDetail '{"detail":"transport category: token=hidden"}'
        $message | Should Be 'Local preflight request failed (HTTP 502) for /api/v1/market/quotes/US/F; inspect the sanitized service logs.'
    }

    It 'suppresses response text and rejects unsafe status or path values' {
        $message = Format-SafeLocalApiFailure -Status '502 token=hidden' -Path '/path?account=hidden' `
            -ErrorDetail '{"detail":"token=hidden code password123"}'
        $message | Should Be 'Local preflight request failed (HTTP unavailable) for /unknown; inspect the sanitized service logs.'
    }
}

Describe 'KIS PAPER exchange session preflight' {
    $now = [DateTimeOffset]::Parse('2026-11-06T08:30:00Z')
    $openSession = @{
        market = 'KR'; is_session = $true; is_open = $true
        evaluated_at = '2026-11-06T08:30:00Z'; close_at = '2026-11-06T09:30:00Z'
    }

    function Test-SessionAssertionThrows($Session, [string]$Market, [DateTimeOffset]$Now) {
        try {
            Assert-KisPaperSessionOpen -Session $Session -Market $Market -Now $Now
            return $false
        } catch {
            return $true
        }
    }

    It 'accepts a fresh calendar-confirmed open session with time to cancel' {
        Test-SessionAssertionThrows -Session $openSession -Market KR -Now $now | Should Be $false
    }

    It 'rejects an exchange holiday' {
        $holiday = $openSession.Clone()
        $holiday.is_session = $false
        $holiday.is_open = $false
        $holiday.open_at = $null
        $holiday.close_at = $null
        Test-SessionAssertionThrows -Session $holiday -Market KR -Now $now | Should Be $true
    }

    It 'classifies a closed non-session without requiring open or close timestamps' {
        $holiday = @{
            market = 'US'; is_session = $false; is_open = $false
            evaluated_at = $now.ToString('o'); open_at = $null; close_at = $null
        }
        $message = $null
        try { Assert-KisPaperSessionOpen -Session $holiday -Market US -Now $now }
        catch { $message = $_.Exception.Message }
        $message | Should Be 'Exchange calendar does not confirm an open session for the requested market.'
    }

    It 'rejects a closed session' {
        $closed = $openSession.Clone()
        $closed.is_open = $false
        Test-SessionAssertionThrows -Session $closed -Market KR -Now $now | Should Be $true
    }

    It 'rejects another market session' {
        Test-SessionAssertionThrows -Session $openSession -Market US -Now $now | Should Be $true
    }

    It 'rejects stale or future calendar confirmations' {
        $stale = $openSession.Clone()
        $stale.evaluated_at = $now.AddSeconds(-11).ToString('o')
        $future = $openSession.Clone()
        $future.evaluated_at = $now.AddSeconds(3).ToString('o')
        Test-SessionAssertionThrows -Session $stale -Market KR -Now $now | Should Be $true
        Test-SessionAssertionThrows -Session $future -Market KR -Now $now | Should Be $true
    }

    It 'rejects a session within five minutes of the close' {
        $ending = $openSession.Clone()
        $ending.close_at = $now.AddMinutes(4).ToString('o')
        Test-SessionAssertionThrows -Session $ending -Market KR -Now $now | Should Be $true
    }

    It 'rejects missing timestamps' {
        $missing = $openSession.Clone()
        $missing.close_at = $null
        Test-SessionAssertionThrows -Session $missing -Market KR -Now $now | Should Be $true
    }

    It 'rejects session timestamps without an explicit timezone offset' {
        $localEvaluationTime = $openSession.Clone()
        $localEvaluationTime.evaluated_at = '2026-11-06T08:30:00'
        $localCloseTime = $openSession.Clone()
        $localCloseTime.close_at = '2026-11-06T09:30:00'

        Test-SessionAssertionThrows -Session $localEvaluationTime -Market KR -Now $now | Should Be $true
        Test-SessionAssertionThrows -Session $localCloseTime -Market KR -Now $now | Should Be $true
    }
}

Describe 'KIS PAPER read-only preflight evidence' {
    It 'writes a minimal passed result without account amounts or secrets' {
        $directory = Join-Path ([IO.Path]::GetTempPath()) ('kis-paper-preflight-' + [guid]::NewGuid().ToString('N'))
        $path = Join-Path $directory 'latest.json'
        try {
            Write-KisPaperPreflightEvidence -Path $path -Market KR -Symbol '069500' -Currency KRW `
                -Result PASSED -Stage Complete -QuoteFreshnessCheckPassed $true `
                -OrderableFundsCheckPassed $true -Now ([DateTimeOffset]::Parse('2026-11-13T14:29:00Z'))

            $evidence = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
            $evidence.mode | Should Be 'PAPER_READ_ONLY_PREFLIGHT'
            $evidence.result | Should Be 'PASSED'
            $evidence.market | Should Be 'KR'
            $evidence.symbol | Should Be '069500'
            $evidence.currency | Should Be 'KRW'
            $evidence.quote_freshness_check_passed | Should Be $true
            $evidence.orderable_funds_check_passed | Should Be $true
            $evidence.order_sent | Should Be $false
            $evidence.cancel_sent | Should Be $false
            $evidence.PSObject.Properties.Name -join ',' | Should Not Match 'account|cash|amount|price|secret|key|number'
            ([DateTimeOffset]$evidence.checked_at_utc).ToUniversalTime().ToString('o') | Should Be '2026-11-13T14:29:00.0000000+00:00'
        } finally {
            Remove-Item -LiteralPath $directory -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    It 'records a failed checkpoint without copying the error text' {
        $directory = Join-Path ([IO.Path]::GetTempPath()) ('kis-paper-preflight-' + [guid]::NewGuid().ToString('N'))
        $path = Join-Path $directory 'latest.json'
        try {
            Write-KisPaperPreflightEvidence -Path $path -Market KR -Symbol '069500' -Currency KRW `
                -Result PASSED -Stage Complete -QuoteFreshnessCheckPassed $true `
                -OrderableFundsCheckPassed $true
            Write-KisPaperPreflightEvidence -Path $path -Market KR -Symbol '069500' -Currency KRW `
                -Result FAILED -Stage OrderableFunds -QuoteFreshnessCheckPassed $true `
                -OrderableFundsCheckPassed $false

            $evidence = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
            $evidence.result | Should Be 'FAILED'
            $evidence.stage | Should Be 'OrderableFunds'
            $evidence.quote_freshness_check_passed | Should Be $true
            $evidence.orderable_funds_check_passed | Should Be $false
            $evidence.order_sent | Should Be $false
            $evidence.cancel_sent | Should Be $false
            ($evidence.PSObject.Properties.Name -contains 'error') | Should Be $false
        } finally {
            Remove-Item -LiteralPath $directory -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
