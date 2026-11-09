function New-AiTraderDailyReportTriggers {
    param(
        [Parameter(Mandatory)]
        [ValidateCount(2, 2)]
        [ValidatePattern('^([01]\d|2[0-3]):[0-5]\d$')]
        [string[]]$Times
    )

    foreach ($time in $Times) {
        $runAt = [DateTime]::Today.Add([TimeSpan]::ParseExact(
            $time, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture))
        New-ScheduledTaskTrigger -Daily -At $runAt
    }
}
