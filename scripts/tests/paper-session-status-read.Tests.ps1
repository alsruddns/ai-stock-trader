. (Join-Path $PSScriptRoot '..\lib\paper-session-status-read.ps1')

Describe 'Local PAPER session status endpoint retry' {
    It 'recovers from a transient status request failure' {
        $attempts = New-Object System.Collections.Generic.List[int]
        $request = {
            [void]$attempts.Add(1)
            if ($attempts.Count -eq 1) { throw 'secret-bearing transport detail' }
            return [pscustomobject]@{ status = 'ok' }
        }

        $status = Invoke-PaperSessionStatusRead -Request $request -RetryCount 2 -RetryDelaySeconds 0

        $status.status | Should Be 'ok'
        $attempts.Count | Should Be 2
    }

    It 'fails after the retry limit without returning exception details' {
        $attempts = New-Object System.Collections.Generic.List[int]
        $request = {
            [void]$attempts.Add(1)
            throw 'secret-bearing transport detail'
        }
        $message = $null
        try {
            Invoke-PaperSessionStatusRead -Request $request -RetryCount 2 -RetryDelaySeconds 0 | Out-Null
        } catch {
            $message = $_.Exception.Message
        }

        $attempts.Count | Should Be 3
        $message | Should Be 'Could not read the local paper-session status endpoint.'
        $message | Should Not Match 'secret-bearing'
    }
}
