. (Join-Path $PSScriptRoot '..\lib\operations-report.ps1')

Describe 'Optional operations report endpoint diagnostics' {
    It 'marks a successful endpoint as available and retains its JSON data' {
        Mock Invoke-RestMethod { return @{ status = 'ok' } }

        $result = Invoke-AiTraderOptionalJsonRequest 'http://127.0.0.1:8000/api/v1/health'

        $result.state | Should Be 'AVAILABLE'
        $result.data.status | Should Be 'ok'
        $result.http_status | Should Be $null
        $result.error_type | Should Be $null
    }

    It 'records a safe failure class without retaining exception text' {
        Mock Invoke-RestMethod { throw [System.Net.WebException]::new('secret-bearing exception detail') }

        $result = Invoke-AiTraderOptionalJsonRequest 'http://127.0.0.1:8000/api/v1/health'
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
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath | Out-Null
        $report = Get-Content -LiteralPath $outputPath -Raw | ConvertFrom-Json

        $report.reportSchemaVersion | Should Be 3
        $report.pythonConnected | Should Be $true
        $report.optionalDataStatus | Should Be 'INCOMPLETE'
        $report.endpointStatus.'python.market_data_quality_KR'.state | Should Be 'SKIPPED_NO_SYMBOLS'
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
        & (Join-Path $PSScriptRoot '..\export-daily-report.ps1') `
            -ApiBaseUrl 'http://spring.test' -PythonApiBaseUrl 'http://python.test' -OutputPath $outputPath | Out-Null
        $json = Get-Content -LiteralPath $outputPath -Raw
        $report = $json | ConvertFrom-Json

        $report.optionalDataStatus | Should Be 'PARTIAL'
        $report.endpointStatus.'python.ai_models'.state | Should Be 'UNREACHABLE'
        $report.endpointStatus.'python.ai_models'.errorType | Should Be 'WebException'
        $json | Should Not Match 'secret-bearing exception detail'
    }
}
