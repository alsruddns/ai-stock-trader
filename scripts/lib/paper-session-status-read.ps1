function Invoke-PaperSessionStatusRead {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [scriptblock]$Request,
        [scriptblock]$OnRetry,
        [ValidateRange(0, 5)] [int]$RetryCount = 2,
        [ValidateRange(0, 10)] [int]$RetryDelaySeconds = 1
    )

    for ($attempt = 0; $attempt -le $RetryCount; $attempt++) {
        try {
            $response = & $Request
            if ($null -eq $response) { throw 'Empty local paper-session status response.' }
            return $response
        } catch {
            if ($attempt -lt $RetryCount) {
                if ($OnRetry) { try { & $OnRetry ($attempt + 1) } catch { } }
                if ($RetryDelaySeconds -gt 0) { Start-Sleep -Seconds $RetryDelaySeconds }
            }
        }
    }

    throw 'Could not read the local paper-session status endpoint.'
}
