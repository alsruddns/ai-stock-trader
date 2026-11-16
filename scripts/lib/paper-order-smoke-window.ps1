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
