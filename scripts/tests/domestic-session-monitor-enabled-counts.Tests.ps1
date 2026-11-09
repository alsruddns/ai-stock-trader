. (Join-Path $PSScriptRoot '..\lib\operations-report.ps1')

Describe 'Domestic session monitor enabled task counts' {
    It 'separates enabled domestic tasks from registered tasks' {
        Mock Get-ScheduledTask {
            @(
                [pscustomobject]@{
                    TaskName = 'AIStockTrader-KRX-Session-Monitor-20261110'
                    State = 'Ready'
                    Principal = [pscustomobject]@{ LogonType = 'Interactive' }
                    Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $true; StartWhenAvailable = $true; RestartCount = 2; RestartInterval = 'PT1M' }
                },
                [pscustomobject]@{
                    TaskName = 'AIStockTrader-KRX-Session-Monitor-20261111'
                    State = 'Disabled'
                    Principal = [pscustomobject]@{ LogonType = 'Interactive' }
                    Settings = [pscustomobject]@{ Enabled = $false; WakeToRun = $true; StartWhenAvailable = $true; RestartCount = 2; RestartInterval = 'PT1M' }
                }
            )
        }
        Mock Get-ScheduledTaskInfo {
            [pscustomobject]@{
                NextRunTime = [datetime]::MinValue
                LastRunTime = [datetime]::MinValue
                LastTaskResult = 267011
            }
        }

        $summary = Get-AiTraderSessionMonitorScheduleSummary

        $summary.state | Should Be 'AVAILABLE'
        $summary.registeredCount | Should Be 2
        $summary.enabledCount | Should Be 1
        $summary.krRegisteredCount | Should Be 2
        $summary.krEnabledCount | Should Be 1
        $summary.usRegisteredCount | Should Be 0
        $summary.usEnabledCount | Should Be 0
    }
}
