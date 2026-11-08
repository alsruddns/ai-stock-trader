. (Join-Path $PSScriptRoot '..\lib\operations-report.ps1')
. (Join-Path $PSScriptRoot '..\lib\model-retraining-report.ps1')

Describe 'Operations report run archive paths' {
    It 'creates unique UTC timestamped JSON filenames without overwriting prior runs' {
        $directory = Join-Path $TestDrive 'reports'
        $timestamp = [DateTimeOffset]::Parse('2026-11-08T01:30:00+00:00')

        $first = New-AiTraderOperationsReportRunPath -Directory $directory -Timestamp $timestamp
        $second = New-AiTraderOperationsReportRunPath -Directory $directory -Timestamp $timestamp

        (Split-Path -Leaf $first) | Should Match '^operations-run-2026-11-08T013000000Z-[a-f0-9]{8}\.json$'
        $first | Should Not Be $second
    }
}

Describe 'Optional operations report endpoint diagnostics' {
    It 'summarizes only the registered KR and US market monitor tasks' {
        $nextRun = [datetime]'2026-11-09T08:45:00'
        Mock Get-ScheduledTask {
            @(
                [pscustomobject]@{ TaskName = 'AIStockTrader-KRX-Session-Monitor-20261109'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $true; StartWhenAvailable = $true } },
                [pscustomobject]@{ TaskName = 'AIStockTrader-US-Session-Monitor-20261109'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $false; StartWhenAvailable = $true } },
                [pscustomobject]@{ TaskName = 'Unrelated-Task'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true } }
            )
        }
        Mock Get-ScheduledTaskInfo {
            return [pscustomobject]@{
                NextRunTime = $nextRun
                LastRunTime = [datetime]::MinValue
                LastTaskResult = 267011
            }
        }

        $summary = Get-AiTraderSessionMonitorScheduleSummary
        $expectedTimeZoneId = [TimeZoneInfo]::Local.Id

        $summary.state | Should Be 'AVAILABLE'
        $summary.timeZoneId | Should Be $expectedTimeZoneId
        $summary.registeredCount | Should Be 2
        $summary.krRegisteredCount | Should Be 1
        $summary.usRegisteredCount | Should Be 1
        $summary.tasks[0].lastRunTime | Should Be $null
        $summary.tasks[0].lastTaskResult | Should Be 267011
        $summary.tasks[0].wakeToRun | Should Be $true
        $summary.tasks[0].startWhenAvailable | Should Be $true
        $summary.tasks[1].wakeToRun | Should Be $false
    }

    It 'marks missing session monitor schedules as unverified' {
        Mock Get-ScheduledTask { @() }

        $summary = Get-AiTraderSessionMonitorScheduleSummary

        $summary.state | Should Be 'UNVERIFIED'
        $summary.registeredCount | Should Be 0
        $summary.tasks.Count | Should Be 0
    }

    It 'records a safe error class when Task Scheduler cannot be queried' {
        Mock Get-ScheduledTask { throw [System.InvalidOperationException]::new('private task details') }

        $summary = Get-AiTraderSessionMonitorScheduleSummary
        $json = $summary | ConvertTo-Json -Compress

        $summary.state | Should Be 'UNAVAILABLE'
        $summary.errorType | Should Be 'InvalidOperationException'
        $json | Should Not Match 'private task details'
    }

    It 'marks the schedule summary partial when one task status cannot be read' {
        Mock Get-ScheduledTask {
            @(
                [pscustomobject]@{ TaskName = 'AIStockTrader-KRX-Session-Monitor-20261109'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true } },
                [pscustomobject]@{ TaskName = 'AIStockTrader-US-Session-Monitor-20261109'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true } }
            )
        }
        Mock Get-ScheduledTaskInfo {
            if ($TaskName -like '*US-*') { throw [System.InvalidOperationException]::new('private status detail') }
            return [pscustomobject]@{ NextRunTime = [datetime]::Now; LastRunTime = [datetime]::MinValue; LastTaskResult = 267011 }
        }

        $summary = Get-AiTraderSessionMonitorScheduleSummary
        $json = $summary | ConvertTo-Json -Compress

        $summary.state | Should Be 'PARTIAL'
        @($summary.tasks | Where-Object { $_.state -eq 'UNKNOWN' }).Count | Should Be 1
        $json | Should Not Match 'private status detail'
    }

    It 'reports only registered PAPER lifecycle and P4 model validation tasks' {
        $nextRun = [datetime]'2026-11-09T23:45:00'
        Mock Get-ScheduledTask {
            @(
                [pscustomobject]@{ TaskName = 'AIStockTrader-US-Paper-Lifecycle-Smoke-20261109'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $true; StartWhenAvailable = $true } },
                [pscustomobject]@{ TaskName = 'AIStockTrader-P4-Model-Train-KR-005930-20261128'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $true; StartWhenAvailable = $true } },
                [pscustomobject]@{ TaskName = 'AIStockTrader-P4-Model-Holdout-US-AAPL-20261205'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $false; StartWhenAvailable = $false } },
                [pscustomobject]@{ TaskName = 'AIStockTrader-US-Session-Monitor-20261109'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true } },
                [pscustomobject]@{ TaskName = 'Unrelated-Task'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true } }
            )
        }
        Mock Get-ScheduledTaskInfo {
            return [pscustomobject]@{
                NextRunTime = $nextRun
                LastRunTime = [datetime]::MinValue
                LastTaskResult = 267011
            }
        }

        $summary = Get-AiTraderValidationTaskScheduleSummary
        $expectedTimeZoneId = [TimeZoneInfo]::Local.Id
        $paperTask = @($summary.tasks | Where-Object { $_.kind -eq 'PAPER_LIFECYCLE' })[0]
        $trainTask = @($summary.tasks | Where-Object { $_.kind -eq 'P4_MODEL_TRAIN' })[0]
        $holdoutTask = @($summary.tasks | Where-Object { $_.kind -eq 'P4_MODEL_HOLDOUT' })[0]

        $summary.state | Should Be 'AVAILABLE'
        $summary.timeZoneId | Should Be $expectedTimeZoneId
        $summary.registeredCount | Should Be 3
        $summary.paperLifecycleCount | Should Be 1
        $summary.modelTrainingCount | Should Be 1
        $summary.modelHoldoutCount | Should Be 1
        $paperTask.market | Should Be 'US'
        $paperTask.taskDate | Should Be '20261109'
        $paperTask.nextRunTime | Should Be $nextRun.ToString('o')
        $paperTask.wakeToRun | Should Be $true
        $paperTask.startWhenAvailable | Should Be $true
        $trainTask.market | Should Be 'KR'
        $trainTask.symbol | Should Be '005930'
        $trainTask.wakeToRun | Should Be $true
        $holdoutTask.market | Should Be 'US'
        $holdoutTask.symbol | Should Be 'AAPL'
        $holdoutTask.startWhenAvailable | Should Be $false
    }

    It 'marks missing validation tasks unverified' {
        Mock Get-ScheduledTask { @() }

        $summary = Get-AiTraderValidationTaskScheduleSummary

        $summary.state | Should Be 'UNVERIFIED'
        $summary.registeredCount | Should Be 0
        $summary.tasks.Count | Should Be 0
    }

    It 'marks validation scheduling partial when a task status cannot be read' {
        Mock Get-ScheduledTask {
            @(
                [pscustomobject]@{ TaskName = 'AIStockTrader-US-Paper-Lifecycle-Smoke-20261109'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true } },
                [pscustomobject]@{ TaskName = 'AIStockTrader-P4-Model-Train-US-AAPL-20261128'; State = 'Ready'; Settings = [pscustomobject]@{ Enabled = $true } }
            )
        }
        Mock Get-ScheduledTaskInfo {
            if ($TaskName -like '*P4-*') { throw [System.InvalidOperationException]::new('private status detail') }
            return [pscustomobject]@{ NextRunTime = [datetime]::Now; LastRunTime = [datetime]::MinValue; LastTaskResult = 267011 }
        }

        $summary = Get-AiTraderValidationTaskScheduleSummary
        $json = $summary | ConvertTo-Json -Compress

        $summary.state | Should Be 'PARTIAL'
        @($summary.tasks | Where-Object { $_.state -eq 'UNKNOWN' }).Count | Should Be 1
        $json | Should Not Match 'private status detail'
    }

    It 'returns only a safe error type when Task Scheduler cannot be queried' {
        Mock Get-ScheduledTask { throw [System.InvalidOperationException]::new('private task details') }

        $summary = Get-AiTraderValidationTaskScheduleSummary
        $json = $summary | ConvertTo-Json -Compress

        $summary.state | Should Be 'UNAVAILABLE'
        $summary.errorType | Should Be 'InvalidOperationException'
        $json | Should Not Match 'private task details'
    }

    It 'marks a successful endpoint as available and retains its JSON data' {
        Mock Invoke-RestMethod { return @{ status = 'ok' } }

        $result = Invoke-AiTraderOptionalJsonRequest 'http://127.0.0.1:18000/api/v1/health'

        $result.state | Should Be 'AVAILABLE'
        $result.data.status | Should Be 'ok'
        $result.http_status | Should Be $null
        $result.error_type | Should Be $null
    }

    It 'records a safe failure class without retaining exception text' {
        Mock Invoke-RestMethod { throw [System.Net.WebException]::new('secret-bearing exception detail') }

        $result = Invoke-AiTraderOptionalJsonRequest 'http://127.0.0.1:18000/api/v1/health'
        $serialized = $result | ConvertTo-Json -Compress

        $result.state | Should Be 'UNREACHABLE'
        $result.error_type | Should Be 'WebException'
        $serialized | Should Not Match 'secret-bearing exception detail'
    }

    It 'atomically replaces an existing JSON report without leaving a temporary file' {
        $directory = Join-Path $TestDrive 'atomic-report'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $path = Join-Path $directory 'operations.json'
        Set-Content -LiteralPath $path -Value '{"version":1}' -Encoding utf8

        Set-AiTraderAtomicJsonFile -Path $path -Value ([pscustomobject]@{ version = 2; state = 'COMPLETE' })

        $report = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $report.version | Should Be 2
        $report.state | Should Be 'COMPLETE'
        @(Get-ChildItem -LiteralPath $directory -Filter '*.tmp').Count | Should Be 0
    }
}

Describe 'Daily operations report endpoint health summary' {
    It 'marks intentionally skipped market quality endpoints as incomplete' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') { return @{ decisions = @{} } }
            if ($Uri -like '*api/v1/ai/models') { return @{ models = @() } }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            throw 'Unexpected report endpoint.'
        }

        $outputPath = Join-Path $TestDrive 'operations-report.json'
        $archivePath = Join-Path $TestDrive 'archive\operations-run-2026-11-08T013000000Z-12345678.json'
        $retrainingDirectory = Join-Path $TestDrive 'no-retraining-runs'
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ArchiveOutputPath $archivePath `
            -ModelRetrainingReportDirectory $retrainingDirectory | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json
        $archivedReport = Get-Content -LiteralPath $archivePath -Raw | ConvertFrom-Json

        $report.reportSchemaVersion | Should Be 9
        $archivedReport.generatedAt | Should Be $report.generatedAt
        $archivedReport.tradingMode | Should Be $report.tradingMode
        $report.pythonConnected | Should Be $true
        $report.optionalDataStatus | Should Be 'INCOMPLETE'
        $report.marketDataQualitySummary.sessionCompleteness | Should Be 'UNVERIFIED'
        $report.marketDataQualitySummary.spreadCoverage | Should Be 'UNVERIFIED'
        $report.endpointStatus.'python.market_data_quality_KR'.state | Should Be 'SKIPPED_NO_SYMBOLS'
        $report.endpointStatus.'local.model_retraining'.state | Should Be 'NO_RUNS'
        $report.endpointStatus.'local.session_monitor_schedule'.state | Should Be $report.sessionMonitorSchedule.state
        $report.endpointStatus.'local.session_monitor_schedule'.registeredCount | Should Be $report.sessionMonitorSchedule.registeredCount
        $report.endpointStatus.'local.session_monitor_schedule'.timeZoneId | Should Be $report.sessionMonitorSchedule.timeZoneId
        $report.endpointStatus.'local.validation_task_schedule'.state | Should Be $report.validationTaskSchedule.state
        $report.endpointStatus.'local.validation_task_schedule'.registeredCount | Should Be $report.validationTaskSchedule.registeredCount
        $report.endpointStatus.'local.validation_task_schedule'.timeZoneId | Should Be $report.validationTaskSchedule.timeZoneId
    }

    It 'separates available endpoints from incomplete market bars and spread coverage' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') {
                return @{ decisions = @{ kr = @{ market = 'KR'; symbol = '005930' }; us = @{ market = 'US'; symbol = 'AAPL' } } }
            }
            if ($Uri -like '*api/v1/ai/models') { return @{ models = @() } }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            if ($Uri -like '*session-quality/KR*') {
                return @{ market = 'KR'; sessions = @(@{ full_session_complete = $false; observed_completed_bars = 302; spread_bars = 302 }) }
            }
            if ($Uri -like '*session-quality/US*') {
                return @{ market = 'US'; sessions = @(@{ full_session_complete = $true; observed_completed_bars = 390; spread_bars = 300 }) }
            }
            throw 'Unexpected report endpoint.'
        }

        $outputPath = Join-Path $TestDrive 'market-quality-operations-report.json'
        $retrainingDirectory = Join-Path $TestDrive 'no-retraining-runs'
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory $retrainingDirectory | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json

        $report.optionalDataStatus | Should Be 'INCOMPLETE'
        $report.endpointStatus.'python.market_data_quality_KR'.state | Should Be 'AVAILABLE'
        $report.marketDataQualitySummary.sessionCompleteness | Should Be 'INCOMPLETE'
        $report.marketDataQualitySummary.spreadCoverage | Should Be 'INCOMPLETE'
        $report.marketDataQualitySummary.sessionsReported | Should Be 2
        $report.marketDataQualitySummary.incompleteSessions | Should Be 1
        $report.marketDataQualitySummary.sessionsMissingSpreads | Should Be 1
    }

    It 'fails closed when the session completeness field is not a JSON boolean' {
        $endpointStatus = @{
            'python.market_data_quality_KR' = @{ state = 'AVAILABLE' }
            'python.market_data_quality_US' = @{ state = 'AVAILABLE' }
        }
        $quality = @{
            KR = @{ sessions = @(@{ full_session_complete = 'false'; observed_completed_bars = 10; spread_bars = 10 }) }
            US = @{ sessions = @(@{ full_session_complete = $true; observed_completed_bars = 10; spread_bars = 10 }) }
        }

        $summary = Get-AiTraderMarketDataQualitySummary -EndpointStatus $endpointStatus -MarketDataQuality $quality

        $summary.sessionCompleteness | Should Be 'INCOMPLETE'
        $summary.spreadCoverage | Should Be 'COMPLETE'
    }

    It 'marks an optional Python endpoint failure as partial and omits its error message' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') { return @{ decisions = @{} } }
            if ($Uri -like '*api/v1/ai/models') { throw [System.Net.WebException]::new('secret-bearing exception detail') }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            throw 'Unexpected report endpoint.'
        }

        $outputPath = Join-Path $TestDrive 'partial-operations-report.json'
        $retrainingDirectory = Join-Path $TestDrive 'no-retraining-runs'
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory $retrainingDirectory | Out-Null
        $json = Get-Content -LiteralPath $outputPath -Raw
        $report = $json | ConvertFrom-Json

        $report.optionalDataStatus | Should Be 'PARTIAL'
        $report.endpointStatus.'python.ai_models'.state | Should Be 'UNREACHABLE'
        $report.endpointStatus.'python.ai_models'.errorType | Should Be 'WebException'
        $json | Should Not Match 'secret-bearing exception detail'
    }

    It 'includes the latest model retraining outcome in the local operations report' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') { return @{ decisions = @{} } }
            if ($Uri -like '*api/v1/ai/models') { return @{ models = @() } }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            throw 'Unexpected report endpoint.'
        }

        $outputPath = Join-Path $TestDrive 'retraining-operations-report.json'
        $retrainingDirectory = Join-Path $TestDrive 'training-run'
        New-Item -ItemType Directory -Path $retrainingDirectory | Out-Null
        Write-AiTraderModelRetrainingFailureReport `
            -Path (Join-Path $retrainingDirectory 'latest.json') `
            -RunId 'us-aapl-20261107-cccccccccccc' -FailureStage 'candidate_training' `
            -Market 'US' -Symbol 'AAPL' -FromDate '2026-11-02' -ToDate '2026-11-06' `
            -DatasetPath 'data/model-candidates/test.csv' -Exception ([System.ArgumentException]::new('private diagnostic'))
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory $retrainingDirectory | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json

        $report.modelRetraining.latestRun.status | Should Be 'training_failed'
        $report.modelRetraining.latestRun.failureStage | Should Be 'candidate_training'
        $report.endpointStatus.'local.model_retraining'.errorType | Should Be 'ArgumentException'
        $json = Get-Content -LiteralPath $outputPath -Raw
        $json | Should Not Match 'private diagnostic'
    }
}
