$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'krx-market-session.ps1')

$cases = @(
    @{ Instant = '2026-11-08T23:59:00+00:00'; Expected = 'PRE_OPEN' },
    @{ Instant = '2026-11-09T00:00:00+00:00'; Expected = 'CONTINUOUS' },
    @{ Instant = '2026-11-09T06:19:59+00:00'; Expected = 'CONTINUOUS' },
    @{ Instant = '2026-11-09T06:20:00+00:00'; Expected = 'CLOSING_AUCTION' },
    @{ Instant = '2026-11-09T06:29:59+00:00'; Expected = 'CLOSING_AUCTION' },
    @{ Instant = '2026-11-09T06:30:00+00:00'; Expected = 'CLOSED' }
)

foreach ($case in $cases) {
    $actual = Get-KrxSessionPhase ([DateTimeOffset]::Parse($case.Instant))
    if ($actual -ne $case.Expected) {
        throw "KRX phase mismatch for $($case.Instant): expected $($case.Expected), got $actual."
    }
}

$open = Get-KrxSessionOpenUtc ([DateTimeOffset]::Parse('2026-11-09T00:15:00+00:00'))
if ($open -ne [DateTimeOffset]::Parse('2026-11-09T00:00:00+00:00')) {
    throw "KRX session open UTC mismatch: got $open."
}

if (Test-KrxSessionTickTimestamp ([DateTimeOffset]::Parse('2026-11-08T06:19:58+00:00')) $open) {
    throw 'A previous session tick was accepted as current-session data.'
}
if (-not (Test-KrxSessionTickTimestamp $open $open)) {
    throw 'The first continuous-session tick was rejected.'
}
foreach ($phase in @('PRE_OPEN', 'CLOSING_AUCTION', 'CLOSED')) {
    if (Test-KrxNoTickThresholdApplies $phase) { throw "No-tick threshold incorrectly applies during $phase." }
}
foreach ($phase in @('CONTINUOUS', 'NOT_APPLICABLE')) {
    if (-not (Test-KrxNoTickThresholdApplies $phase)) {
        throw "No-tick threshold does not apply during $phase."
    }
}

Write-Output 'KRX session tests passed: phase boundaries, session-open UTC, stale prior-session tick rejection, and no-tick threshold phases.'
