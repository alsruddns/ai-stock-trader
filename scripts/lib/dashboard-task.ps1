function Test-DashboardOperationsReady {
    param([AllowNull()]$Response)

    if ($null -eq $Response -or
        $Response.springConnected -ne $true -or
        $Response.pythonConnected -ne $true -or
        $null -eq $Response.operations -or
        $null -eq $Response.marketAi) {
        return $false
    }

    $operations = $Response.operations
    $marketAi = $Response.marketAi
    return $operations.tradingMode -eq 'paper' -and
        $operations.liveTrading -eq 'disabled' -and
        $operations.paperAutomationEnabled -eq $false -and
        $operations.kisPaperOrdersEnabled -eq $false -and
        $operations.kisRealOrdersEnabled -eq $false -and
        $operations.kisRealAccountReadsEnabled -eq $false -and
        $operations.kisRealReconciliationEnabled -eq $false -and
        $marketAi.live_trading_enabled -eq $false -and
        $marketAi.paper_automation_enabled -eq $false
}

function Assert-DashboardOperationsReady {
    param([AllowNull()]$Response)

    if ($null -ne $Response -and $Response.springConnected -eq $true -and
        $null -ne $Response.operations) {
        $operations = $Response.operations
        if ($operations.tradingMode -ne 'paper' -or
            $operations.liveTrading -ne 'disabled' -or
            $operations.paperAutomationEnabled -ne $false -or
            $operations.kisPaperOrdersEnabled -ne $false -or
            $operations.kisRealOrdersEnabled -ne $false -or
            $operations.kisRealAccountReadsEnabled -ne $false -or
            $operations.kisRealReconciliationEnabled -ne $false) {
            throw 'Dashboard startup stopped because Spring did not report the required PAPER and order locks.'
        }
    }
    if ($null -ne $Response -and $Response.pythonConnected -eq $true -and
        $null -ne $Response.marketAi) {
        $marketAi = $Response.marketAi
        if ($marketAi.live_trading_enabled -ne $false -or $marketAi.paper_automation_enabled -ne $false) {
            throw 'Dashboard startup stopped because FastAPI did not report the required trading locks.'
        }
    }
}
