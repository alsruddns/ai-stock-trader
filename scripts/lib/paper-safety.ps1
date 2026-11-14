function Read-AiTraderLocalSafetySettings {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $envFile = Join-Path $RepositoryRoot '.env_local'
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        throw 'The ignored .env_local file is required.'
    }

    $settings = @{}
    $safetyKeys = @('TRADING_MODE', 'LIVE_TRADING_ENABLED', 'KIS_PAPER_ORDERS_ENABLED',
        'KIS_REAL_ORDERS_ENABLED', 'REAL_TRADING_APPROVAL_CONFIRMED', 'PAPER_AUTOTRADING_ENABLED')
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^\s*([^#=]+)=(.*)$') {
            $key = $matches[1].Trim()
            if ($safetyKeys -contains $key) {
                $settings[$key] = $matches[2].Trim().Trim('"').Trim("'")
            }
        }
    }
    return $settings
}

function Assert-AiTraderPaperSafetyLocks {
    param([Parameter(Mandatory = $true)][hashtable]$Settings)

    foreach ($required in @('TRADING_MODE', 'LIVE_TRADING_ENABLED', 'KIS_PAPER_ORDERS_ENABLED',
            'KIS_REAL_ORDERS_ENABLED', 'REAL_TRADING_APPROVAL_CONFIRMED', 'PAPER_AUTOTRADING_ENABLED')) {
        if (-not $Settings.ContainsKey($required)) {
            throw "Required local safety setting '$required' is missing."
        }
    }
    if ($Settings.TRADING_MODE -ine 'paper' -or $Settings.LIVE_TRADING_ENABLED -ine 'false' -or
        $Settings.KIS_PAPER_ORDERS_ENABLED -ine 'false' -or $Settings.KIS_REAL_ORDERS_ENABLED -ine 'false' -or
        $Settings.REAL_TRADING_APPROVAL_CONFIRMED -ine 'false' -or
        $Settings.PAPER_AUTOTRADING_ENABLED -ine 'false') {
        throw 'Scheduled model work requires PAPER mode with all order and automation switches disabled.'
    }
}
