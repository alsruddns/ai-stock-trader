function Get-SafeLocalApiFailureSummary {
    [CmdletBinding()]
    param(
        [AllowNull()] [Nullable[int]]$StatusCode,
        [AllowNull()] [string]$ResponseBody,
        [AllowNull()] [string]$ExceptionType
    )

    $brokerCode = $null
    if ($ResponseBody) {
        $codeMatch = [regex]::Match($ResponseBody, '(?i)\bcode\s+(?<code>EGW\d{5})\b')
        if ($codeMatch.Success) { $brokerCode = $codeMatch.Groups['code'].Value.ToUpperInvariant() }
    }

    $category = switch ($brokerCode) {
        'EGW00001' { 'KIS_BROKER_TEMPORARY_ERROR'; break }
        'EGW00002' { 'KIS_BROKER_SERVER_ERROR'; break }
        'EGW00201' { 'KIS_RATE_LIMIT'; break }
        default {
            switch ($StatusCode) {
                429 { 'LOCAL_RATE_LIMIT'; break }
                502 { 'LOCAL_GATEWAY_ERROR'; break }
                503 { 'LOCAL_SERVICE_UNAVAILABLE'; break }
                504 { 'LOCAL_GATEWAY_TIMEOUT'; break }
                default {
                    if ($ExceptionType -match 'Timeout|TaskCanceled') { 'LOCAL_TIMEOUT' }
                    elseif ($ExceptionType -match 'Http|Web|Socket|Connection') { 'LOCAL_TRANSPORT_ERROR' }
                    else { 'LOCAL_REQUEST_ERROR' }
                }
            }
        }
    }

    $fields = [System.Collections.Generic.List[string]]::new()
    if ($null -ne $StatusCode) { $fields.Add("http_status=$StatusCode") }
    $fields.Add("category=$category")
    if ($brokerCode) { $fields.Add("broker_code=$brokerCode") }
    return $fields -join '; '
}
