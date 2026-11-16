function Test-PaperOrderSmokeExecutionWindow {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [DateTimeOffset]$Now,
        [Parameter(Mandatory)] [DateTimeOffset]$ScheduledStart,
        [TimeSpan]$AllowedLateness = ([TimeSpan]::FromMinutes(5))
    )

    if ($AllowedLateness -le [TimeSpan]::Zero) {
        throw 'The PAPER order smoke lateness window must be positive.'
    }

    return $Now -ge $ScheduledStart -and $Now -lt $ScheduledStart.Add($AllowedLateness)
}

function Test-PaperOrderSmokeSubmissionWindowForKst {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [DateTimeOffset]$Now,
        [Parameter(Mandatory)] [string]$SessionDateKst,
        [Parameter(Mandatory)] [string]$ScheduledStartTimeKst,
        [TimeSpan]$AllowedLateness = ([TimeSpan]::FromMinutes(5))
    )

    if ($SessionDateKst -notmatch '^\d{4}-\d{2}-\d{2}$') {
        throw 'The PAPER order smoke session date must use YYYY-MM-DD.'
    }
    if ($ScheduledStartTimeKst -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') {
        throw 'The PAPER order smoke scheduled start must use HH:mm KST.'
    }
    $zone = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
    $nowKst = [TimeZoneInfo]::ConvertTime($Now, $zone)
    if ($nowKst.ToString('yyyy-MM-dd') -ne $SessionDateKst) { return $false }
    $timeParts = $ScheduledStartTimeKst.Split(':')
    $localStart = [datetime]::new(
        [int]$SessionDateKst.Substring(0, 4), [int]$SessionDateKst.Substring(5, 2),
        [int]$SessionDateKst.Substring(8, 2), [int]$timeParts[0], [int]$timeParts[1], 0,
        [DateTimeKind]::Unspecified)
    $offset = $zone.GetUtcOffset($localStart)
    $scheduledStart = [DateTimeOffset]::new($localStart, $offset)
    return Test-PaperOrderSmokeExecutionWindow -Now $nowKst -ScheduledStart $scheduledStart `
        -AllowedLateness $AllowedLateness
}
