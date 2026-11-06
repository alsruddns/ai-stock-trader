function Get-KrxSessionPhase([DateTimeOffset]$Timestamp) {
    $koreaTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
    $koreaLocal = [TimeZoneInfo]::ConvertTime($Timestamp, $koreaTimeZone)
    if ($koreaLocal.TimeOfDay -lt [TimeSpan]::Parse('09:00:00')) { return 'PRE_OPEN' }
    if ($koreaLocal.TimeOfDay -ge [TimeSpan]::Parse('15:30:00')) { return 'CLOSED' }
    if ($koreaLocal.TimeOfDay -ge [TimeSpan]::Parse('15:20:00')) { return 'CLOSING_AUCTION' }
    return 'CONTINUOUS'
}

function Get-KrxSessionOpenUtc([DateTimeOffset]$Timestamp) {
    $koreaTimeZone = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
    $koreaLocal = [TimeZoneInfo]::ConvertTime($Timestamp, $koreaTimeZone)
    $openLocal = [DateTime]::SpecifyKind($koreaLocal.Date.AddHours(9), [DateTimeKind]::Unspecified)
    return ([DateTimeOffset]::new($openLocal, $koreaTimeZone.GetUtcOffset($openLocal))).ToUniversalTime()
}

function Test-KrxSessionTickTimestamp([DateTimeOffset]$TickTimestamp, [DateTimeOffset]$SessionOpenUtc) {
    return $TickTimestamp.ToUniversalTime() -ge $SessionOpenUtc.ToUniversalTime()
}

function Test-KrxNoTickThresholdApplies([string]$SessionPhase) {
    return $SessionPhase -in @('CONTINUOUS', 'NOT_APPLICABLE')
}
