. (Join-Path $PSScriptRoot '../lib/paper-order-smoke-window.ps1')

Describe 'PAPER order smoke execution window' {
    It 'allows the scheduled start and only the configured short grace period' {
        $start = [DateTimeOffset]::Parse('2026-11-17T00:05:00+09:00')
        (Test-PaperOrderSmokeExecutionWindow $start $start) | Should Be $true
        (Test-PaperOrderSmokeExecutionWindow $start.AddMinutes(4).AddSeconds(59) $start) | Should Be $true
        (Test-PaperOrderSmokeExecutionWindow $start.AddMinutes(5) $start) | Should Be $false
    }

    It 'rejects early, next-day, and invalid windows' {
        $start = [DateTimeOffset]::Parse('2026-11-17T00:05:00+09:00')
        (Test-PaperOrderSmokeExecutionWindow $start.AddSeconds(-1) $start) | Should Be $false
        (Test-PaperOrderSmokeExecutionWindow $start.AddDays(1) $start) | Should Be $false

        $threw = $false
        try { Test-PaperOrderSmokeExecutionWindow $start $start ([TimeSpan]::Zero) | Out-Null }
        catch { $threw = $true }
        $threw | Should Be $true
    }
}
