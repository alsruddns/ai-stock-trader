. (Join-Path $PSScriptRoot '../lib/retired-kis-analysis-scan.ps1')

Describe 'Retired KIS domestic analysis API scan' {
    It 'passes when runtime sources contain no retired calls' {
        $root = Join-Path $TestDrive 'clean-repository'
        New-Item -ItemType Directory -Path (Join-Path $root 'backend/trading-api/src/main/java') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $root 'services/market-ai/app') -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $root 'backend/trading-api/src/main/java/Quotes.java') -Value 'quoteClient.readCurrentPrice(symbol);'
        Set-Content -LiteralPath (Join-Path $root 'services/market-ai/app/quotes.py') -Value 'return await client.get_quote(symbol)'

        $findings = @(Get-RetiredKisDomesticAnalysisFindings -RepositoryRoot $root)

        $findings.Count | Should Be 0
    }

    It 'detects every retired KIS function in Spring and FastAPI runtime source' {
        $root = Join-Path $TestDrive 'retired-repository'
        $javaRoot = Join-Path $root 'backend/trading-api/src/main/java/com/example'
        $pythonRoot = Join-Path $root 'services/market-ai/app'
        New-Item -ItemType Directory -Path $javaRoot -Force | Out-Null
        New-Item -ItemType Directory -Path $pythonRoot -Force | Out-Null
        $functions = @(
            'finance_balance_sheet', 'finance_income_statement', 'finance_financial_ratio',
            'finance_profit_ratio', 'finance_other_major_ratios', 'finance_stability_ratio', 'finance_growth_ratio'
        )
        Set-Content -LiteralPath (Join-Path $javaRoot 'Deprecated.java') -Value ($functions[0..2] | ForEach-Object { "client.$_();" })
        $pythonLines = @($functions[3..5] | ForEach-Object { "client.$_()" }) + "from kis import $($functions[6]) as growth"
        Set-Content -LiteralPath (Join-Path $pythonRoot 'deprecated.py') -Value $pythonLines

        $findings = @(Get-RetiredKisDomesticAnalysisFindings -RepositoryRoot $root)

        $findings.Count | Should Be 7
        @($findings | Select-Object -ExpandProperty Function | Sort-Object -Unique).Count | Should Be 7
        ($findings | Where-Object { $_.Path -like 'backend/trading-api/*' }).Count | Should Be 3
        ($findings | Where-Object { $_.Path -like 'services/market-ai/app/*' }).Count | Should Be 4
    }

    It 'ignores tests and documentation outside runtime source roots' {
        $root = Join-Path $TestDrive 'non-runtime-repository'
        New-Item -ItemType Directory -Path (Join-Path $root 'backend/trading-api/src/test/java') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $root 'services/market-ai/tests') -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $root 'backend/trading-api/src/test/java/Example.java') -Value 'client.finance_balance_sheet();'
        Set-Content -LiteralPath (Join-Path $root 'services/market-ai/tests/example.py') -Value 'client.finance_growth_ratio()'

        $findings = @(Get-RetiredKisDomesticAnalysisFindings -RepositoryRoot $root)

        $findings.Count | Should Be 0
    }

    It 'detects direct REST endpoint paths even when helper names are not used' {
        $root = Join-Path $TestDrive 'raw-endpoint-repository'
        $javaRoot = Join-Path $root 'backend/trading-api/src/main/java/com/example'
        $pythonRoot = Join-Path $root 'services/market-ai/app'
        New-Item -ItemType Directory -Path $javaRoot -Force | Out-Null
        New-Item -ItemType Directory -Path $pythonRoot -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $javaRoot 'RawClient.java') -Value 'String path = "/uapi/domestic-stock/v1/finance/balance-sheet";'
        Set-Content -LiteralPath (Join-Path $pythonRoot 'raw_client.py') -Value "path = '/uapi/domestic-stock/v1/finance/growth-ratio?x=1'"

        $findings = @(Get-RetiredKisDomesticAnalysisFindings -RepositoryRoot $root)

        $findings.Count | Should Be 2
        @($findings | Where-Object { $_.Function -like 'endpoint:*' }).Count | Should Be 2
    }

    It 'detects a retired endpoint suffix stored separately from its URL prefix' {
        $root = Join-Path $TestDrive 'dynamic-endpoint-repository'
        $pythonRoot = Join-Path $root 'services/market-ai/app'
        New-Item -ItemType Directory -Path $pythonRoot -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $pythonRoot 'raw_client.py') -Value @(
            'FINANCE_API_PREFIX = "/uapi/domestic-stock/v1/finance/"',
            'endpoint = "balance-sheet"',
            'url = FINANCE_API_PREFIX + endpoint'
        )

        $findings = @(Get-RetiredKisDomesticAnalysisFindings -RepositoryRoot $root)

        $findings.Count | Should Be 1
        $findings[0].Function | Should Be 'endpoint-fragment:balance-sheet'
    }
}
