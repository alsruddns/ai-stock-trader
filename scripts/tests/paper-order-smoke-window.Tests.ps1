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

    It 'rechecks the configured KST window at the broker submission boundary' {
        $before = [DateTimeOffset]::Parse('2026-11-17T09:04:59+09:00')
        $start = [DateTimeOffset]::Parse('2026-11-17T09:05:00+09:00')
        $lastAllowed = [DateTimeOffset]::Parse('2026-11-17T09:09:59+09:00')
        $closed = [DateTimeOffset]::Parse('2026-11-17T09:10:00+09:00')

        (Test-PaperOrderSmokeSubmissionWindowForKst $before '2026-11-17' '09:05') | Should Be $false
        (Test-PaperOrderSmokeSubmissionWindowForKst $start '2026-11-17' '09:05') | Should Be $true
        (Test-PaperOrderSmokeSubmissionWindowForKst $lastAllowed '2026-11-17' '09:05') | Should Be $true
        (Test-PaperOrderSmokeSubmissionWindowForKst $closed '2026-11-17' '09:05') | Should Be $false
        (Test-PaperOrderSmokeSubmissionWindowForKst $start.AddDays(1) '2026-11-17' '09:05') | Should Be $false
    }
}
