. (Join-Path $PSScriptRoot '../lib/safe-http-diagnostic.ps1')

Describe 'Safe local API failure diagnostics' {
    It 'reports a KIS server code without including the response body' {
        $body = '{"detail":"KIS quote request failed (HTTP 502, code EGW00002). account=1234567890 secret=do-not-print"}'
        $summary = Get-SafeLocalApiFailureSummary -StatusCode 502 -ResponseBody $body -ExceptionType 'HttpResponseException'

        $summary | Should Match 'http_status=502'
        $summary | Should Match 'category=KIS_BROKER_SERVER_ERROR'
        $summary | Should Match 'broker_code=EGW00002'
        $summary | Should Not Match '1234567890|do-not-print|account|secret|detail'
    }

    It 'classifies a KIS rate limit without echoing response text' {
        $summary = Get-SafeLocalApiFailureSummary -StatusCode 429 `
            -ResponseBody '{"detail":"KIS rate limit reached; code EGW00201; token=secret"}' `
            -ExceptionType 'HttpResponseException'

        $summary | Should Match 'category=KIS_RATE_LIMIT'
        $summary | Should Match 'broker_code=EGW00201'
        $summary | Should Not Match 'token|secret|EGW00201;'
    }

    It 'reports a transport timeout without inventing an HTTP or KIS code' {
        $summary = Get-SafeLocalApiFailureSummary -StatusCode $null -ResponseBody 'account=1234567890' `
            -ExceptionType 'TaskCanceledException'

        $summary | Should Be 'category=LOCAL_TIMEOUT'
        $summary | Should Not Match '1234567890|http_status|broker_code'
    }
}
