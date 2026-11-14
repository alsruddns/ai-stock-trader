function New-LeanBacktestRangeSummary {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [ValidateSet('KR', 'US')] [string]$Market,
        [Parameter(Mandatory)] [string]$Symbol,
        [Parameter(Mandatory)] [object[]]$SessionResults
    )

    if ($SessionResults.Count -lt 2) {
        throw 'A LEAN range replay requires at least two completed single-session results.'
    }

    $normalizedSymbol = $Symbol.Trim().ToUpperInvariant()
    $seenSessions = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $validated = [System.Collections.Generic.List[object]]::new()
    $expectedCosts = $null
    $expectedCostSource = $null
    $expectedModelId = $null
    $expectedModelArtifact = $null
    $netPnlSum = [decimal]0

    foreach ($result in $SessionResults) {
        if ($result.market -ne $Market -or $result.symbol -ne $normalizedSymbol) {
            throw 'LEAN range results must all match the requested market and symbol.'
        }
        if ($result.parity -ne 'passed') {
            throw "LEAN/Python parity did not pass for $($result.symbol) $($result.session)."
        }
        $sessionDate = [datetime]::MinValue
        if (-not [datetime]::TryParseExact([string]$result.session, 'yyyy-MM-dd',
                [cultureinfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$sessionDate)) {
            throw 'Every LEAN range result must have a valid exchange session date.'
        }
        if (-not $seenSessions.Add($result.session)) {
            throw "LEAN range contains a duplicate session: $($result.session)."
        }

        $costs = ConvertTo-Json -InputObject $result.cost_assumptions_bps -Compress -Depth 10
        if ($null -eq $expectedCosts) { $expectedCosts = $costs }
        elseif ($costs -ne $expectedCosts) {
            throw 'LEAN range results must use identical cost assumptions for every session.'
        }
        if ($null -eq $expectedCostSource) { $expectedCostSource = [string]$result.cost_source }
        elseif ([string]$result.cost_source -ne $expectedCostSource) {
            throw 'LEAN range results must use the same cost source for every session.'
        }
        $modelId = [string]$result.model_id
        $modelArtifact = [string]$result.model_artifact_sha256
        if ($null -eq $expectedModelId) {
            $expectedModelId = $modelId
            $expectedModelArtifact = $modelArtifact
        } elseif ($modelId -ne $expectedModelId -or $modelArtifact -ne $expectedModelArtifact) {
            throw 'LEAN range results must use the same model artifact for every session.'
        }

        $pnl = [decimal]0
        if (-not [decimal]::TryParse([string]$result.python_net_pnl,
                [Globalization.NumberStyles]::Float, [cultureinfo]::InvariantCulture, [ref]$pnl)) {
            throw "LEAN range has an invalid Python net P&L for $($result.session)."
        }
        foreach ($hashName in @('dataset_sha256', 'staged_dataset_sha256')) {
            if ([string]$result.$hashName -notmatch '^[a-fA-F0-9]{64}$') {
                throw "LEAN range has no valid $hashName provenance for $($result.session)."
            }
        }
        if (-not [bool]$result.rights_reviewed) {
            throw "LEAN range data rights have not been reviewed for $($result.session)."
        }
        $netPnlSum += $pnl
        $validated.Add([pscustomobject]@{
            session = $result.session
            replay_session = [string]$result.replay_session
            bars = [int]$result.bars
            signals = [int]$result.signals
            trades = [int]$result.trades
            orders = [int]$result.orders
            model_id = $modelId
            model_artifact_sha256 = $modelArtifact
            dataset_sha256 = [string]$result.dataset_sha256
            staged_dataset_sha256 = [string]$result.staged_dataset_sha256
            rights_reviewed = [bool]$result.rights_reviewed
            cost_source = [string]$result.cost_source
            python_net_pnl = [string]$result.python_net_pnl
            lean_net_pnl = [string]$result.lean_net_pnl
            pnl_delta = [string]$result.pnl_delta
            parity = [string]$result.parity
        })
    }

    $orderedSessions = @($validated | Sort-Object { [datetime]::ParseExact($_.session, 'yyyy-MM-dd', [cultureinfo]::InvariantCulture) })
    return [ordered]@{
        market = $Market
        symbol = $normalizedSymbol
        replay_type = 'independent_single_session_replays'
        session_count = $orderedSessions.Count
        sessions = $orderedSessions
        model_id = $expectedModelId
        model_artifact_sha256 = $expectedModelArtifact
        cost_source = $expectedCostSource
        cost_assumptions_bps = ($expectedCosts | ConvertFrom-Json)
        independent_session_net_pnl_sum = $netPnlSum.ToString([cultureinfo]::InvariantCulture)
        aggregation_note = 'Descriptive sum of independent daily replays; not a shared-capital portfolio or compounded performance.'
        parity = 'passed'
        executable = $false
    }
}
