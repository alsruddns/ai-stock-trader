param(
    [Parameter(Mandatory = $true)]
    [string]$CsvFile,

    [Parameter(Mandatory = $true)]
    [ValidateSet('KR', 'US')]
    [string]$Market,

    [Parameter(Mandatory = $true)]
    [string]$Symbol,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$Session,

    [datetime]$ReplayDate,

    [int]$ExpectedBars = 0,

    [string]$ModelId,

    [string]$BuyCommissionBps,

    [string]$SellCommissionBps,

    [string]$SellTaxBps,

    [string]$SlippageBps
)

$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'lib\domestic-only-validation.ps1')
Assert-DomesticOnlyValidation -Market $Market -Operation 'LEAN historical backtest validation'
$sessionDate = [datetime]::ParseExact($Session, 'yyyy-MM-dd', [cultureinfo]::InvariantCulture)
if ($sessionDate.Date -gt [DateTime]::UtcNow.Date) {
    throw 'LEAN replay source session cannot be in the future.'
}
$latestLeanDate = [DateTime]::UtcNow.Date.AddDays(-1)
if ($PSBoundParameters.ContainsKey('ReplayDate')) {
    $replayDate = $ReplayDate.Date
    if ($replayDate -gt $latestLeanDate) {
        throw 'Requested LEAN replay date must be earlier than the current UTC date.'
    }
    if ($replayDate.DayOfWeek -eq [DayOfWeek]::Saturday -or $replayDate.DayOfWeek -eq [DayOfWeek]::Sunday) {
        throw 'Requested LEAN replay date must be a weekday.'
    }
} else {
    $replayDate = $sessionDate.Date
    if ($replayDate -gt $latestLeanDate) { $replayDate = $latestLeanDate }
    while ($replayDate.DayOfWeek -eq [DayOfWeek]::Saturday -or $replayDate.DayOfWeek -eq [DayOfWeek]::Sunday) {
        $replayDate = $replayDate.AddDays(-1)
    }
}
$sourceCsv = (Resolve-Path -LiteralPath $CsvFile).Path
$python = Join-Path $repository 'services\market-ai\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Create services/market-ai/.venv and install the project ops dependencies first.'
}

if ($ExpectedBars -le 0) {
    $ExpectedBars = if ($Market -eq 'US') { 390 } else { 380 }
}
$currency = if ($Market -eq 'US') { 'USD' } else { 'KRW' }
$costEnvironmentPath = Join-Path $repository '.env_local'
$costSettingsScript = Join-Path $PSScriptRoot 'lib/lean-cost-inputs.ps1'
. $costSettingsScript
$costSettings = Get-LeanBacktestCostInputs -Market $Market -EnvironmentFile $costEnvironmentPath `
    -BuyCommissionBps $BuyCommissionBps -SellCommissionBps $SellCommissionBps `
    -SellTaxBps $SellTaxBps -SlippageBps $SlippageBps
$costSource = $costSettings.Source
$costValues = @{
    buy_commission = $costSettings.BuyCommissionBps
    sell_commission = $costSettings.SellCommissionBps
    sell_tax = $costSettings.SellTaxBps
    slippage = $costSettings.SlippageBps
}
$leanRoot = Join-Path $repository '.local\lean-toolchain'
$dotnetRoot = Join-Path $leanRoot 'dotnet'
$dotnet = Join-Path $dotnetRoot 'dotnet.exe'
$leanRepo = Join-Path $leanRoot 'Lean'
$buildLogDirectory = Join-Path $repository '.local\logs'
New-Item -ItemType Directory -Path $leanRoot, $buildLogDirectory -Force | Out-Null

if (-not (Test-Path -LiteralPath $dotnet)) {
    $installer = Join-Path $leanRoot 'dotnet-install.ps1'
    Invoke-WebRequest -Uri 'https://dot.net/v1/dotnet-install.ps1' -OutFile $installer
    & $installer -Channel 10.0 -InstallDir $dotnetRoot
    if ($LASTEXITCODE -ne 0) { throw '.NET 10 SDK installation failed.' }
}

if (-not (Test-Path -LiteralPath (Join-Path $leanRepo 'QuantConnect.Lean.sln'))) {
    if (Test-Path -LiteralPath $leanRepo) {
        throw "LEAN source folder exists but is incomplete: $leanRepo"
    }
    git clone --depth 1 https://github.com/QuantConnect/Lean.git $leanRepo
    if ($LASTEXITCODE -ne 0) { throw 'Cloning the official QuantConnect LEAN source failed.' }
}

$algorithmSource = Join-Path $PSScriptRoot '..\backtesting\lean\KisCsvReader\KisCsvLeanAlgorithm.cs'
$algorithmDestination = Join-Path $leanRepo 'Algorithm.CSharp\KisCsvLeanAlgorithm.cs'
Copy-Item -LiteralPath $algorithmSource -Destination $algorithmDestination -Force
$legacyAlgorithm = Join-Path $leanRepo 'Algorithm.CSharp\KisCsvLeanSmokeAlgorithm.cs'
if (Test-Path -LiteralPath $legacyAlgorithm) {
    Remove-Item -LiteralPath $legacyAlgorithm -Force
}
$dataFolder = Join-Path $leanRepo 'Data'
$prepareArguments = @((Join-Path $repository 'scripts\prepare-lean-data.py'), $sourceCsv,
    '--market', $Market, '--symbol', $Symbol, '--data-folder', $dataFolder,
    '--replay-date', $replayDate.ToString('yyyy-MM-dd', [cultureinfo]::InvariantCulture),
    '--buy-commission-bps', $costValues.buy_commission,
    '--sell-commission-bps', $costValues.sell_commission,
    '--sell-tax-bps', $costValues.sell_tax,
    '--slippage-bps', $costValues.slippage)
if (-not [string]::IsNullOrWhiteSpace($ModelId)) {
    $prepareArguments += @('--model-id', $ModelId)
}
$preparedJson = & $python @prepareArguments | Out-String
if ($LASTEXITCODE -ne 0) { throw 'KIS data provenance or complete-session validation failed.' }
$prepared = $preparedJson | ConvertFrom-Json
if ($prepared.session -ne $Session -or $prepared.replay_session -ne $replayDate.ToString('yyyy-MM-dd', [cultureinfo]::InvariantCulture)) {
    throw 'Prepared source or LEAN replay session does not match the requested dates.'
}

$configPath = Join-Path $leanRepo 'Launcher\config.json'
$config = [System.IO.File]::ReadAllText($configPath)
$config = [regex]::Replace($config, '"algorithm-type-name"\s*:\s*"[^"]+"', '"algorithm-type-name": "KisCsvLeanStrategyAlgorithm"', 1)
$dataJson = ConvertTo-Json -InputObject $dataFolder -Compress
$config = [regex]::Replace($config, '"data-folder"\s*:\s*"[^"]+"', '"data-folder": ' + $dataJson, 1)
$parameters = @{
    market = $Market
    symbol = $Symbol.Trim().ToUpperInvariant()
    session = [string]$prepared.replay_session
    currency = $currency
    'expected-bars' = [string]$ExpectedBars
    'buy-commission-bps' = $costValues.buy_commission
    'sell-commission-bps' = $costValues.sell_commission
    'sell-tax-bps' = $costValues.sell_tax
    'slippage-bps' = $costValues.slippage
}
if (-not [string]::IsNullOrWhiteSpace($ModelId)) {
    $invalidModelPreparation = ($prepared.model_id -ne $ModelId) -or
        [string]::IsNullOrWhiteSpace([string]$prepared.ai_score_sha256) -or
        ([int]$prepared.ai_score_count -ne ($ExpectedBars - 20))
    if ($invalidModelPreparation) {
        throw 'Prepared AI model scores do not match the requested model or complete-session range.'
    }
    $parameters['ai-model-id'] = [string]$prepared.model_id
    $parameters['ai-threshold'] = [string]::Format([cultureinfo]::InvariantCulture, '{0}', [double]$prepared.model_threshold)
    $parameters['ai-score-sha256'] = [string]$prepared.ai_score_sha256
    $parameters['ai-score-count'] = [string]$prepared.ai_score_count
}
$parametersJson = $parameters | ConvertTo-Json -Compress
$parameterPattern = '"parameters"\s*:\s*\{[\s\S]*?\}'
if (-not [regex]::IsMatch($config, $parameterPattern)) {
    throw 'LEAN launcher config has no parameters object.'
}
$config = [regex]::Replace($config, $parameterPattern, '"parameters": ' + $parametersJson, 1)
[System.IO.File]::WriteAllText($configPath, $config, [System.Text.UTF8Encoding]::new($false))

$env:DOTNET_ROOT = $dotnetRoot
$env:PATH = "$dotnetRoot;$env:PATH"
$env:DOTNET_CLI_HOME = Join-Path $leanRoot 'cli-home'
$env:NUGET_PACKAGES = Join-Path $leanRoot 'nuget'
$buildLog = Join-Path $buildLogDirectory 'lean-build.log'
$launcherDirectory = Join-Path $leanRepo 'Launcher\bin\Release'
$launcherAssembly = Join-Path $launcherDirectory 'QuantConnect.Lean.Launcher.dll'
if (-not (Test-Path -LiteralPath $launcherAssembly)) {
    $buildTarget = Join-Path $leanRepo 'QuantConnect.Lean.sln'
    & $dotnet build $buildTarget --configuration Release --nologo --verbosity quiet *> $buildLog
} else {
    $buildTarget = Join-Path $leanRepo 'Algorithm.CSharp\QuantConnect.Algorithm.CSharp.csproj'
    & $dotnet build $buildTarget --configuration Release --no-restore --nologo --verbosity quiet *> $buildLog
}
if ($LASTEXITCODE -ne 0) {
    Get-Content -LiteralPath $buildLog -Tail 60
    throw 'Official LEAN source build failed; see .local/logs/lean-build.log.'
}

$algorithmAssembly = Join-Path $leanRepo 'Algorithm.CSharp\bin\Release\QuantConnect.Algorithm.CSharp.dll'
Copy-Item -LiteralPath $algorithmAssembly -Destination (Join-Path $launcherDirectory 'QuantConnect.Algorithm.CSharp.dll') -Force
Copy-Item -LiteralPath (Join-Path $leanRepo 'Launcher\config.json') -Destination (Join-Path $launcherDirectory 'config.json') -Force
$engineLog = Join-Path $buildLogDirectory 'lean-backtest.log'
Push-Location $launcherDirectory
try {
    & $dotnet (Join-Path $launcherDirectory 'QuantConnect.Lean.Launcher.dll') *> $engineLog
    $engineExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($engineExitCode -ne 0) {
    Get-Content -LiteralPath $engineLog -Tail 60
    throw 'LEAN engine process failed; see .local/logs/lean-backtest.log.'
}
$summary = Select-String -LiteralPath $engineLog -Pattern 'KIS_LEAN_ORB_VWAP'
if ($summary.Count -ne 1 -or (Select-String -LiteralPath $engineLog -Pattern 'Runtime Error:|Algorithm initialization exception').Count -gt 0) {
    Get-Content -LiteralPath $engineLog -Tail 60
    throw 'LEAN did not complete a clean strategy replay; see .local/logs/lean-backtest.log.'
}
$match = [regex]::Match($summary[0].Line, 'KIS_LEAN_ORB_VWAP market=(?<market>\w+) symbol=(?<symbol>\S+) bars=(?<bars>\d+) signals=(?<signals>\d+) quant_signals=(?<quant_signals>\d+) trades=(?<trades>\d+) orders=(?<orders>\d+) filled_orders=(?<filled_orders>\d+) failed_orders=(?<failed_orders>\d+) ai_model_id=(?<model_id>\S*) ai_threshold=(?<threshold>\S*) net_liquidation=(?<equity>[-\d.]+) net_pnl=(?<pnl>[-\d.]+)')
if (-not $match.Success) { throw 'LEAN strategy result line has an unexpected format.' }
$leanBars = [int]::Parse($match.Groups['bars'].Value, [cultureinfo]::InvariantCulture)
$leanSignals = [int]::Parse($match.Groups['signals'].Value, [cultureinfo]::InvariantCulture)
$leanQuantSignals = [int]::Parse($match.Groups['quant_signals'].Value, [cultureinfo]::InvariantCulture)
$leanTrades = [int]::Parse($match.Groups['trades'].Value, [cultureinfo]::InvariantCulture)
$leanOrders = [int]::Parse($match.Groups['orders'].Value, [cultureinfo]::InvariantCulture)
$leanFilledOrders = [int]::Parse($match.Groups['filled_orders'].Value, [cultureinfo]::InvariantCulture)
$leanFailedOrders = [int]::Parse($match.Groups['failed_orders'].Value, [cultureinfo]::InvariantCulture)
$leanPnl = [decimal]::Parse($match.Groups['pnl'].Value, [cultureinfo]::InvariantCulture)
$pythonPnl = [decimal]::Parse([string]$prepared.net_pnl, [cultureinfo]::InvariantCulture)
$pnlDelta = [math]::Abs($leanPnl - $pythonPnl)
if ($leanBars -ne $prepared.bars -or $leanSignals -ne $prepared.signals -or $leanQuantSignals -ne $prepared.quant_signals -or $leanTrades -ne $prepared.trades -or $leanOrders -ne (2 * $leanTrades) -or $leanFilledOrders -ne $leanOrders -or $leanFailedOrders -ne 0 -or $pnlDelta -gt [decimal]0.01 -or $match.Groups['model_id'].Value -ne [string]$prepared.model_id) {
    throw "LEAN/Python parity check failed: bars=$leanBars/$($prepared.bars), signals=$leanSignals/$($prepared.signals), quant_signals=$leanQuantSignals/$($prepared.quant_signals), trades=$leanTrades/$($prepared.trades), orders=$leanOrders, filled=$leanFilledOrders, failed=$leanFailedOrders, net_delta=$pnlDelta. See .local/logs/lean-backtest.log."
}
[ordered]@{
    market = $Market
    symbol = $Symbol.Trim().ToUpperInvariant()
    session = $Session
    replay_session = [string]$prepared.replay_session
    bars = $leanBars
    signals = $leanSignals
    quant_signals = $leanQuantSignals
    trades = $leanTrades
    orders = $leanOrders
    filled_orders = $leanFilledOrders
    failed_orders = $leanFailedOrders
    python_net_pnl = $pythonPnl.ToString([cultureinfo]::InvariantCulture)
    lean_net_pnl = $leanPnl.ToString([cultureinfo]::InvariantCulture)
    pnl_delta = $pnlDelta.ToString([cultureinfo]::InvariantCulture)
    cost_source = $costSource
    cost_assumptions_bps = $prepared.cost_assumptions_bps
    parity = 'passed'
} | ConvertTo-Json -Compress
