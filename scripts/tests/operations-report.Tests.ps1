. (Join-Path $PSScriptRoot '..\lib\operations-report.ps1')
. (Join-Path $PSScriptRoot '..\lib\model-retraining-report.ps1')

Describe 'Domestic PAPER session status failure summary' {
    It 'allows only bounded retry categories and omits transport details' {
        $path = Join-Path $TestDrive 'domestic-session-monitor.jsonl'
        @{
            result = 'failed'; recorded_at_utc = '2026-11-13T07:10:00Z'; market_session = 'KR'
            selected_feed = 'paper'; market_session_date = '2026-11-13';
            max_status_unavailable_seconds = 59.8
            status_read_retry_count = 2
            status_read_failure_categories = @{ TIMEOUT = 1; HTTP_503 = 1; appSecret = 9 }
            status_read_last_failure_category = 'HTTP_503'
            failure_stage = 'stream_monitoring'; failure_code = 'SESSION_STATUS_UNAVAILABLE'
            exception_detail = 'must-not-leak'
        } | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath $path -Encoding utf8

        $summary = Get-AiTraderDomesticSessionMonitorSummary -Path $path
        $json = $summary | ConvertTo-Json -Compress -Depth 5

        $summary.state | Should Be 'FAILED'
        $summary.maxStatusUnavailableSeconds | Should Be 59.8
        $summary.statusReadRetryCount | Should Be 2
        $summary.statusReadLastFailureCategory | Should Be 'HTTP_503'
        @($summary.statusReadFailureCategories | Where-Object { $_.category -eq 'TIMEOUT' })[0].count | Should Be 1
        @($summary.statusReadFailureCategories | Where-Object { $_.category -eq 'HTTP_503' })[0].count | Should Be 1
        $json | Should Not Match 'appSecret|must-not-leak|exception_detail'
    }

    It 'omits missing or out-of-range unavailable duration values' {
        $path = Join-Path $TestDrive 'domestic-session-monitor-invalid-duration.jsonl'
        foreach ($value in @(-1, 601, 'NaN', 'Infinity')) {
            @{
                result = 'failed'; recorded_at_utc = '2026-11-13T07:10:00Z'; market_session = 'KR'
                selected_feed = 'paper'; market_session_date = '2026-11-13'
                max_status_unavailable_seconds = $value
            } | ConvertTo-Json -Depth 5 -Compress | Set-Content -LiteralPath $path -Encoding utf8

            $summary = Get-AiTraderDomesticSessionMonitorSummary -Path $path
            $summary.maxStatusUnavailableSeconds | Should Be $null
        }
    }
}

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
                [pscustomobject]@{ TaskName = 'AIStockTrader-KRX-Session-Monitor-20261109'; State = 'Ready'; Principal = [pscustomobject]@{ LogonType = 'Interactive' }; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $true; StartWhenAvailable = $true; RestartCount = 4; RestartInterval = 'PT1M' } },
                [pscustomobject]@{ TaskName = 'AIStockTrader-US-Session-Monitor-20261109'; State = 'Ready'; Principal = [pscustomobject]@{ LogonType = 'Password' }; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $false; StartWhenAvailable = $true; RestartCount = 0; RestartInterval = $null } },
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
        $krTask = @($summary.tasks | Where-Object { $_.market -eq 'KR' })[0]
        $usTask = @($summary.tasks | Where-Object { $_.market -eq 'US' })[0]

        $summary.state | Should Be 'AVAILABLE'
        $summary.timeZoneId | Should Be $expectedTimeZoneId
        $summary.registeredCount | Should Be 2
        $summary.krRegisteredCount | Should Be 1
        $summary.usRegisteredCount | Should Be 1
        $summary.tasks[0].lastRunTime | Should Be $null
        $summary.tasks[0].lastTaskResult | Should Be 267011
        $krTask.wakeToRun | Should Be $true
        $krTask.principalLogonType | Should Be 'Interactive'
        $krTask.requiresInteractiveLogon | Should Be $true
        $krTask.startWhenAvailable | Should Be $true
        $krTask.restartCount | Should Be 4
        $krTask.restartInterval | Should Be 'PT1M'
        $usTask.wakeToRun | Should Be $false
        $usTask.principalLogonType | Should Be 'Password'
        $usTask.requiresInteractiveLogon | Should Be $false
    }

    It 'marks missing session monitor schedules as unverified' {
        Mock Get-ScheduledTask { @() }

        $summary = Get-AiTraderSessionMonitorScheduleSummary

        $summary.state | Should Be 'UNVERIFIED'
        $summary.registeredCount | Should Be 0
        $summary.tasks.Count | Should Be 0
    }

    It 'reports local stack login dependence and does not expose the task principal identity' {
        Mock Get-ScheduledTask {
            return [pscustomobject]@{
                TaskName = 'AIStockTrader-LocalStack'
                State = 'Running'
                Principal = [pscustomobject]@{ UserId = 'PRIVATE\\account'; LogonType = 'Interactive' }
                Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $false; StartWhenAvailable = $true }
            }
        }
        Mock Get-ScheduledTaskInfo {
            return [pscustomobject]@{ NextRunTime = [datetime]::MinValue; LastRunTime = [datetime]::Now; LastTaskResult = 0 }
        }

        $summary = Get-AiTraderLocalStackTaskSummary
        $json = $summary | ConvertTo-Json -Compress

        $summary.state | Should Be 'Running'
        $summary.principalLogonType | Should Be 'Interactive'
        $summary.requiresInteractiveLogon | Should Be $true
        $summary.enabled | Should Be $true
        $json | Should Not Match 'PRIVATE'
    }

    It 'reports an absent local stack task as unverified' {
        Mock Get-ScheduledTask { return $null }

        $summary = Get-AiTraderLocalStackTaskSummary

        $summary.state | Should Be 'UNVERIFIED'
        $summary.principalLogonType | Should Be 'UNKNOWN'
        $summary.requiresInteractiveLogon | Should Be $null
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
        @($summary.tasks | Where-Object { $_.requiresInteractiveLogon -ne $null }).Count | Should Be 0
        $json | Should Not Match 'private status detail'
    }

    It 'reports only registered PAPER lifecycle and P4 model validation tasks' {
        $nextRun = [datetime]'2026-11-09T23:45:00'
        Mock Get-ScheduledTask {
            @(
                [pscustomobject]@{ TaskName = 'AIStockTrader-US-Paper-Lifecycle-Smoke-20261109'; State = 'Ready'; Principal = [pscustomobject]@{ LogonType = 'Interactive' }; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $true; StartWhenAvailable = $true } },
                [pscustomobject]@{ TaskName = 'AIStockTrader-P4-Model-Train-KR-005930-20261128'; State = 'Ready'; Principal = [pscustomobject]@{ LogonType = 'Interactive' }; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $true; StartWhenAvailable = $true } },
                [pscustomobject]@{ TaskName = 'AIStockTrader-P4-Model-Holdout-US-AAPL-20261205'; State = 'Ready'; Principal = [pscustomobject]@{ LogonType = 'Password' }; Settings = [pscustomobject]@{ Enabled = $true; WakeToRun = $false; StartWhenAvailable = $false } },
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
        $paperTask.requiresInteractiveLogon | Should Be $true
        $paperTask.startWhenAvailable | Should Be $true
        $trainTask.market | Should Be 'KR'
        $trainTask.symbol | Should Be '005930'
        $trainTask.wakeToRun | Should Be $true
        $holdoutTask.market | Should Be 'US'
        $holdoutTask.symbol | Should Be 'AAPL'
        $holdoutTask.startWhenAvailable | Should Be $false
        $holdoutTask.requiresInteractiveLogon | Should Be $false
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
    It 'reports missing PAPER preflight evidence without exposing extra local fields' {
        $summary = Get-AiTraderPaperPreflightSummary -Path (Join-Path $TestDrive 'missing-paper-preflight.json')

        $summary.state | Should Be 'MISSING'
        $summary.orderSent | Should Be $false
        $summary.cancelSent | Should Be $false
        ($summary | ConvertTo-Json -Compress) | Should Not Match 'account|secret|amount'
    }

    It 'allowlists a valid PAPER preflight summary and omits sensitive source properties' {
        $path = Join-Path $TestDrive 'paper-preflight.json'
        @{
            schema_version = 1; mode = 'PAPER_READ_ONLY_PREFLIGHT'; checked_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
            market = 'KR'; symbol = '069500'; currency = 'KRW'; result = 'PASSED'; stage = 'Complete'
            quote_freshness_check_passed = $true; orderable_funds_check_passed = $true
            order_sent = $false; cancel_sent = $false; accountNumber = '12345678'; appSecret = 'do-not-copy'
        } | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding utf8

        $summary = Get-AiTraderPaperPreflightSummary -Path $path
        $json = $summary | ConvertTo-Json -Compress

        $summary.state | Should Be 'AVAILABLE'
        $summary.result | Should Be 'PASSED'
        $summary.stage | Should Be 'Complete'
        $summary.market | Should Be 'KR'
        $summary.symbol | Should Be '069500'
        $summary.quoteFreshnessCheckPassed | Should Be $true
        $summary.orderableFundsCheckPassed | Should Be $true
        $summary.orderSent | Should Be $false
        $summary.cancelSent | Should Be $false
        $json | Should Not Match '12345678|do-not-copy|accountNumber|appSecret'
    }

    It 'rejects PAPER preflight evidence that claims an order was sent' {
        $path = Join-Path $TestDrive 'forged-paper-preflight.json'
        @{
            schema_version = 1; mode = 'PAPER_READ_ONLY_PREFLIGHT'; checked_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
            market = 'KR'; symbol = '069500'; currency = 'KRW'; result = 'PASSED'; stage = 'Complete'
            quote_freshness_check_passed = $true; orderable_funds_check_passed = $true
            order_sent = $true; cancel_sent = $false
        } | ConvertTo-Json | Set-Content -LiteralPath $path -Encoding utf8

        $summary = Get-AiTraderPaperPreflightSummary -Path $path

        $summary.state | Should Be 'INVALID'
        $summary.result | Should Be $null
        $summary.orderSent | Should Be $false
    }

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
            -ArchiveOutputPath $archivePath -PaperPreflightEvidencePath (Join-Path $TestDrive 'no-paper-preflight.json') `
            -ModelRetrainingReportDirectory $retrainingDirectory -DomesticSessionEvidencePath (Join-Path $TestDrive 'no-monitor-progress.jsonl') | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json
        $archivedReport = Get-Content -LiteralPath $archivePath -Raw | ConvertFrom-Json

        $report.reportSchemaVersion | Should Be 15
        $archivedReport.generatedAt | Should Be $report.generatedAt
        $archivedReport.tradingMode | Should Be $report.tradingMode
        $report.pythonConnected | Should Be $true
        $report.optionalDataStatus | Should Be 'INCOMPLETE'
        $report.marketDataQualitySummary.sessionCompleteness | Should Be 'UNVERIFIED'
        $report.marketDataQualitySummary.spreadCoverage | Should Be 'UNVERIFIED'
        $report.endpointStatus.'python.market_data_quality_KR'.state | Should Be 'SKIPPED_NO_SYMBOLS'
        $report.endpointStatus.'local.model_retraining'.state | Should Be 'NO_RUNS'
        $report.endpointStatus.'local.local_stack_task'.state | Should Be $report.localStackTask.state
        $report.endpointStatus.'local.local_stack_task'.principalLogonType | Should Be $report.localStackTask.principalLogonType
        $report.endpointStatus.'local.local_stack_task'.requiresInteractiveLogon | Should Be $report.localStackTask.requiresInteractiveLogon
        $report.endpointStatus.'local.session_monitor_schedule'.state | Should Be $report.sessionMonitorSchedule.state
        $report.endpointStatus.'local.session_monitor_schedule'.registeredCount | Should Be $report.sessionMonitorSchedule.registeredCount
        $report.endpointStatus.'local.session_monitor_schedule'.timeZoneId | Should Be $report.sessionMonitorSchedule.timeZoneId
        $report.endpointStatus.'local.validation_task_schedule'.state | Should Be $report.validationTaskSchedule.state
        $report.endpointStatus.'local.validation_task_schedule'.registeredCount | Should Be $report.validationTaskSchedule.registeredCount
        $report.endpointStatus.'local.validation_task_schedule'.timeZoneId | Should Be $report.validationTaskSchedule.timeZoneId
        $report.paperPreflight.state | Should Be 'MISSING'
        $report.endpointStatus.'local.paper_preflight'.state | Should Be 'MISSING'
    }

    It 'includes only allowlisted latest PAPER preflight fields in the operations report' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') { return @{ decisions = @{} } }
            if ($Uri -like '*api/v1/ai/models') { return @{ models = @() } }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            throw 'Unexpected report endpoint.'
        }
        $evidencePath = Join-Path $TestDrive 'paper-preflight-integration.json'
        @{
            schema_version = 1; mode = 'PAPER_READ_ONLY_PREFLIGHT'; checked_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
            market = 'KR'; symbol = '069500'; currency = 'KRW'; result = 'PASSED'; stage = 'Complete'
            quote_freshness_check_passed = $true; orderable_funds_check_passed = $true
            order_sent = $false; cancel_sent = $false; accountNumber = '98765432'; appSecret = 'must-not-leak'
        } | ConvertTo-Json | Set-Content -LiteralPath $evidencePath -Encoding utf8

        $outputPath = Join-Path $TestDrive 'paper-preflight-operations-report.json'
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory (Join-Path $TestDrive 'no-retraining-runs') `
            -DomesticSessionEvidencePath (Join-Path $TestDrive 'no-monitor-progress.jsonl') `
            -PaperPreflightEvidencePath $evidencePath | Out-Null
        $json = Get-Content -LiteralPath $outputPath -Raw
        $report = $json | ConvertFrom-Json

        $report.paperPreflight.state | Should Be 'AVAILABLE'
        $report.paperPreflight.result | Should Be 'PASSED'
        $report.paperPreflight.symbol | Should Be '069500'
        $report.endpointStatus.'local.paper_preflight'.state | Should Be 'AVAILABLE'
        $json | Should Not Match '98765432|must-not-leak|accountNumber|appSecret'
    }

    It 'summarizes domestic incomplete market bars and spread coverage' {
        Mock Invoke-RestMethod {
            if ($Uri -like '*api/v1/operations/summary') { return [pscustomobject]@{ tradingMode = 'paper' } }
            if ($Uri -like '*api/v1/health') { return @{ status = 'ok' } }
            if ($Uri -like '*api/v1/paper/session') { return @{ decisions = @{ kr = @{ market = 'KR'; symbol = '005930' } } } }
            if ($Uri -like '*api/v1/ai/models') { return @{ models = @() } }
            if ($Uri -like '*api/v1/market/sessions/*') { return @{ market = $Uri } }
            if ($Uri -like '*session-quality/KR*') {
                return @{ market = 'KR'; sessions = @(@{ full_session_complete = $false; observed_completed_bars = 302; spread_bars = 302; spread_observation_time_bars = 301 }) }
            }
            throw 'Unexpected report endpoint.'
        }

        $outputPath = Join-Path $TestDrive 'market-quality-operations-report.json'
        $retrainingDirectory = Join-Path $TestDrive 'no-retraining-runs'
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath `
            -ModelRetrainingReportDirectory $retrainingDirectory -DomesticSessionEvidencePath (Join-Path $TestDrive 'no-monitor-progress.jsonl') | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json

        $report.optionalDataStatus | Should Be 'INCOMPLETE'
        $report.endpointStatus.'python.market_data_quality_KR'.state | Should Be 'AVAILABLE'
        $report.marketDataQualitySummary.sessionCompleteness | Should Be 'INCOMPLETE'
        $report.marketDataQualitySummary.spreadCoverage | Should Be 'COMPLETE'
        $report.marketDataQualitySummary.spreadObservationTimeCoverage | Should Be 'INCOMPLETE'
        $report.marketDataQualitySummary.sessionsReported | Should Be 1
        $report.marketDataQualitySummary.incompleteSessions | Should Be 1
        $report.marketDataQualitySummary.sessionsMissingSpreads | Should Be 0
        $report.marketDataQualitySummary.sessionsMissingSpreadObservationTimes | Should Be 1
    }

    It 'marks timestamp coverage complete only when every observed bar has a spread observation time' {
        $endpointStatus = @{
            'python.market_data_quality_KR' = @{ state = 'AVAILABLE' }
        }
        $quality = @{
            KR = @{ sessions = @(@{ full_session_complete = $true; observed_completed_bars = 380; spread_bars = 380; spread_observation_time_bars = 380 }) }
        }

        $summary = Get-AiTraderMarketDataQualitySummary -EndpointStatus $endpointStatus -MarketDataQuality $quality -OperationalMarkets @('KR')

        $summary.sessionCompleteness | Should Be 'COMPLETE'
        $summary.spreadCoverage | Should Be 'COMPLETE'
        $summary.spreadObservationTimeCoverage | Should Be 'COMPLETE'
        $summary.sessionsMissingSpreadObservationTimes | Should Be 0
    }

    It 'fails closed when the session completeness field is not a JSON boolean' {
        $endpointStatus = @{
            'python.market_data_quality_KR' = @{ state = 'AVAILABLE' }
        }
        $quality = @{
            KR = @{ sessions = @(@{ full_session_complete = 'false'; observed_completed_bars = 10; spread_bars = 10 }) }
        }

        $summary = Get-AiTraderMarketDataQualitySummary -EndpointStatus $endpointStatus -MarketDataQuality $quality -OperationalMarkets @('KR')

        $summary.sessionCompleteness | Should Be 'INCOMPLETE'
        $summary.spreadCoverage | Should Be 'COMPLETE'
        $summary.spreadObservationTimeCoverage | Should Be 'INCOMPLETE'
        $summary.sessionsMissingSpreadObservationTimes | Should Be 1
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
            -ModelRetrainingReportDirectory $retrainingDirectory -DomesticSessionEvidencePath (Join-Path $TestDrive 'no-monitor-progress.jsonl') | Out-Null
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
            -ModelRetrainingReportDirectory $retrainingDirectory -DomesticSessionEvidencePath (Join-Path $TestDrive 'no-monitor-progress.jsonl') | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json

        $report.modelRetraining.latestRun.status | Should Be 'training_failed'
        $report.modelRetraining.latestRun.failureStage | Should Be 'candidate_training'
        $report.endpointStatus.'local.model_retraining'.errorType | Should Be 'ArgumentException'
        $json = Get-Content -LiteralPath $outputPath -Raw
        $json | Should Not Match 'private diagnostic'
    }
}
