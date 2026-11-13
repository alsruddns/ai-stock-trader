function Get-PaperSessionStatusFailureCategory {
    param([Parameter(Mandatory)][object]$Failure)

    $exception = if ($Failure -is [System.Management.Automation.ErrorRecord]) {
        $Failure.Exception
    } else { $Failure }
    for ($depth = 0; $exception -and $depth -lt 4; $depth++) {
        $typeName = $exception.GetType().Name
        if ($exception -is [System.TimeoutException] -or $typeName -match 'Timeout|TaskCanceled') {
            return 'TIMEOUT'
        }
        $response = $exception.PSObject.Properties['Response']
        if ($response -and $response.Value) {
            $status = $response.Value.PSObject.Properties['StatusCode']
            $statusCode = 0
            if ($status -and [int]::TryParse([string]$status.Value, [ref]$statusCode) -and
                $statusCode -ge 400 -and $statusCode -le 599) {
                return "HTTP_$statusCode"
            }
        }
        if ($exception -is [System.Net.WebException]) {
            switch ([string]$exception.Status) {
                'Timeout' { return 'TIMEOUT' }
                'ConnectFailure' { return 'CONNECTION_FAILURE' }
                'NameResolutionFailure' { return 'DNS_FAILURE' }
                'ProxyNameResolutionFailure' { return 'DNS_FAILURE' }
                'SecureChannelFailure' { return 'TLS_FAILURE' }
                'TrustFailure' { return 'TLS_FAILURE' }
                'ConnectionClosed' { return 'CONNECTION_CLOSED' }
                'ReceiveFailure' { return 'NETWORK_FAILURE' }
                'SendFailure' { return 'NETWORK_FAILURE' }
            }
        }
        if ($typeName -eq 'HttpRequestException') { return 'CONNECTION_FAILURE' }
        $exception = $exception.InnerException
    }
    return 'REQUEST_FAILURE'
}

function Invoke-PaperSessionStatusRead {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [scriptblock]$Request,
        [scriptblock]$OnRetry,
        [scriptblock]$OnFailure,
        [ValidateRange(0, 5)] [int]$RetryCount = 2,
        [ValidateRange(0, 10)] [int]$RetryDelaySeconds = 1
    )

    for ($attempt = 0; $attempt -le $RetryCount; $attempt++) {
        try {
            $response = & $Request
            if ($null -eq $response) { throw 'Empty local paper-session status response.' }
            return $response
        } catch {
            if ($OnFailure) {
                $category = Get-PaperSessionStatusFailureCategory $_
                try { & $OnFailure ($attempt + 1) $category } catch { }
            }
            if ($attempt -lt $RetryCount) {
                if ($OnRetry) { try { & $OnRetry ($attempt + 1) } catch { } }
                if ($RetryDelaySeconds -gt 0) { Start-Sleep -Seconds $RetryDelaySeconds }
            }
        }
    }

    throw 'Could not read the local paper-session status endpoint.'
}
