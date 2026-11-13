. (Join-Path $PSScriptRoot '..\lib\paper-session-status-read.ps1')

Describe 'Local PAPER session status endpoint retry' {
    It 'recovers from a transient status request failure' {
        $attempts = New-Object System.Collections.Generic.List[int]
        $retryNumbers = New-Object System.Collections.Generic.List[int]
        $request = {
            [void]$attempts.Add(1)
            if ($attempts.Count -eq 1) { throw 'secret-bearing transport detail' }
            return [pscustomobject]@{ status = 'ok' }
        }

        $status = Invoke-PaperSessionStatusRead -Request $request -OnRetry { param($retryNumber) [void]$retryNumbers.Add($retryNumber) } `
            -RetryCount 2 -RetryDelaySeconds 0

        $status.status | Should Be 'ok'
        $attempts.Count | Should Be 2
        $retryNumbers.Count | Should Be 1
        $retryNumbers[0] | Should Be 1
    }

    It 'fails after the retry limit without returning exception details' {
        $attempts = New-Object System.Collections.Generic.List[int]
        $request = {
            [void]$attempts.Add(1)
            throw 'secret-bearing transport detail'
        }
        $message = $null
        $retryNumbers = New-Object System.Collections.Generic.List[int]
        try {
            Invoke-PaperSessionStatusRead -Request $request -OnRetry { param($retryNumber) [void]$retryNumbers.Add($retryNumber) } `
                -RetryCount 2 -RetryDelaySeconds 0 | Out-Null
        } catch {
            $message = $_.Exception.Message
        }

        $attempts.Count | Should Be 3
        $retryNumbers.Count | Should Be 2
        $retryNumbers[0] | Should Be 1
        $retryNumbers[1] | Should Be 2
        $message | Should Be 'Could not read the local paper-session status endpoint.'
        $message | Should Not Match 'secret-bearing'
    }
}
