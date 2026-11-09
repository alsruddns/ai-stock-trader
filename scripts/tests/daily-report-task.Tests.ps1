. (Join-Path $PSScriptRoot '..\lib\daily-report-task.ps1')

Describe 'Daily operations report task schedule' {
    It 'creates a domestic post-close report trigger in addition to the morning report' {
        Mock New-ScheduledTaskTrigger { [pscustomobject]@{ at = $At } }

        $triggers = @(New-AiTraderDailyReportTriggers -Times @('07:30', '16:00'))

        $triggers.Count | Should Be 2
        (($triggers | ForEach-Object { ([datetime]$_.at).ToString('HH:mm') }) -join ',') | Should Be '07:30,16:00'
        Assert-MockCalled New-ScheduledTaskTrigger -Times 2 -ParameterFilter { $Daily -eq $true }
    }
}
