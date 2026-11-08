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

    [int]$ExpectedBars = 0,

    [string]$ModelId,

    [string]$BuyCommissionBps,

    [string]$SellCommissionBps,

    [string]$SellTaxBps,

    [string]$SlippageBps
)

$ErrorActionPreference = 'Stop'
$repository = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourceCsv = (Resolve-Path -LiteralPath $CsvFile).Path
$python = Join-Path $repository 'services\market-ai\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Project Python environment is missing. Create services/market-ai/.venv and install the project ops dependencies first.'
}

if ($ExpectedBars -le 0) {
    $ExpectedBars = if ($Market -eq 'US') { 390 } else { 380 }
}
$currency = if ($Market -eq 'US') { 'USD' } else { 'KRW' }
$costPrefix = "BACKTEST_${Market}_"
$costKeys = [ordered]@{
    buy_commission = "${costPrefix}BUY_COMMISSION_BPS"
    sell_commission = "${costPrefix}SELL_COMMISSION_BPS"
    sell_tax = "${costPrefix}SELL_TAX_BPS"
    slippage = "${costPrefix}SLIPPAGE_BPS"
}
$explicitCosts = [ordered]@{
    buy_commission = $BuyCommissionBps
    sell_commission = $SellCommissionBps
    sell_tax = $SellTaxBps
    slippage = $SlippageBps
}
$explicitCostCount = @($explicitCosts.Values | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count
if ($explicitCostCount -gt 0 -and $explicitCostCount -lt $explicitCosts.Count) {
    throw 'Pass all four explicit cost values together, or leave them all blank to use .env_local.'
}
$localEnvironment = @{}
$localEnvironmentPath = Join-Path $repository '.env_local'
if (Test-Path -LiteralPath $localEnvironmentPath) {
    foreach ($line in Get-Content -LiteralPath $localEnvironmentPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $separator = $trimmed.IndexOf('=')
        if ($separator -lt 1) { continue }
        $name = $trimmed.Substring(0, $separator).Trim()
        if ($costKeys.Values -notcontains $name) { continue }
        $value = $trimmed.Substring($separator + 1).Trim().Trim('"').Trim("'")
        if ($value) { $localEnvironment[$name] = $value }
    }
}
$configuredCostCount = @($costKeys.Values | Where-Object { $localEnvironment.ContainsKey($_) }).Count
if ($explicitCostCount -eq 0 -and $configuredCostCount -gt 0 -and $configuredCostCount -lt $costKeys.Count) {
    throw "Incomplete $Market backtest cost assumptions in .env_local. Configure all four directional commission, sell tax, and slippage values or leave all four blank for diagnostic parity only."
}
$costSource = if ($explicitCostCount -eq $explicitCosts.Count) { 'explicit_parameters' }
    elseif ($configuredCostCount -eq $costKeys.Count) { 'env_local' }
    else { 'diagnostic_defaults' }
$costValues = @{}
foreach ($costName in $costKeys.Keys) {
    $costValues[$costName] = if ($costSource -eq 'explicit_parameters') { $explicitCosts[$costName] }
    elseif ($costSource -eq 'env_local') { $localEnvironment[$costKeys[$costName]] }
    else {
        switch ($costName) {
            'buy_commission' { '5' }
            'sell_commission' { '5' }
            'sell_tax' { '0' }
            'slippage' { '5' }
        }
    }
    $parsedCost = [decimal]0
    if (-not [decimal]::TryParse([string]$costValues[$costName], [Globalization.NumberStyles]::Float,
            [cultureinfo]::InvariantCulture, [ref]$parsedCost) -or $parsedCost -lt 0 -or $parsedCost -gt 10000) {
        throw "The $Market ${costName} cost assumption must be a decimal basis-point value from zero through 10000."
    }
    $costValues[$costName] = $parsedCost.ToString([cultureinfo]::InvariantCulture)
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

$configPath = Join-Path $leanRepo 'Launcher\config.json'
$config = [System.IO.File]::ReadAllText($configPath)
$config = [regex]::Replace($config, '"algorithm-type-name"\s*:\s*"[^"]+"', '"algorithm-type-name": "KisCsvLeanStrategyAlgorithm"', 1)
$dataJson = ConvertTo-Json -InputObject $dataFolder -Compress
$config = [regex]::Replace($config, '"data-folder"\s*:\s*"[^"]+"', '"data-folder": ' + $dataJson, 1)
$parameters = @{
    market = $Market
    symbol = $Symbol.Trim().ToUpperInvariant()
    session = $Session
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
$match = [regex]::Match($summary[0].Line, 'KIS_LEAN_ORB_VWAP market=(?<market>\w+) symbol=(?<symbol>\S+) bars=(?<bars>\d+) signals=(?<signals>\d+) quant_signals=(?<quant_signals>\d+) trades=(?<trades>\d+) orders=(?<orders>\d+) ai_model_id=(?<model_id>\S*) ai_threshold=(?<threshold>\S*) net_liquidation=(?<equity>[-\d.]+) net_pnl=(?<pnl>[-\d.]+)')
if (-not $match.Success) { throw 'LEAN strategy result line has an unexpected format.' }
$leanBars = [int]::Parse($match.Groups['bars'].Value, [cultureinfo]::InvariantCulture)
$leanSignals = [int]::Parse($match.Groups['signals'].Value, [cultureinfo]::InvariantCulture)
$leanQuantSignals = [int]::Parse($match.Groups['quant_signals'].Value, [cultureinfo]::InvariantCulture)
$leanTrades = [int]::Parse($match.Groups['trades'].Value, [cultureinfo]::InvariantCulture)
$leanOrders = [int]::Parse($match.Groups['orders'].Value, [cultureinfo]::InvariantCulture)
$leanPnl = [decimal]::Parse($match.Groups['pnl'].Value, [cultureinfo]::InvariantCulture)
$pythonPnl = [decimal]::Parse([string]$prepared.net_pnl, [cultureinfo]::InvariantCulture)
$pnlDelta = [math]::Abs($leanPnl - $pythonPnl)
if ($leanBars -ne $prepared.bars -or $leanSignals -ne $prepared.signals -or $leanQuantSignals -ne $prepared.quant_signals -or $leanTrades -ne $prepared.trades -or $leanOrders -ne (2 * $leanTrades) -or $pnlDelta -gt [decimal]0.01 -or $match.Groups['model_id'].Value -ne [string]$prepared.model_id) {
    throw "LEAN/Python parity check failed: bars=$leanBars/$($prepared.bars), signals=$leanSignals/$($prepared.signals), quant_signals=$leanQuantSignals/$($prepared.quant_signals), trades=$leanTrades/$($prepared.trades), order_count=$leanOrders, net_delta=$pnlDelta. See .local/logs/lean-backtest.log."
}
[ordered]@{
    market = $Market
    symbol = $Symbol.Trim().ToUpperInvariant()
    session = $Session
    bars = $leanBars
    signals = $leanSignals
    quant_signals = $leanQuantSignals
    trades = $leanTrades
    orders = $leanOrders
    python_net_pnl = $pythonPnl.ToString([cultureinfo]::InvariantCulture)
    lean_net_pnl = $leanPnl.ToString([cultureinfo]::InvariantCulture)
    pnl_delta = $pnlDelta.ToString([cultureinfo]::InvariantCulture)
    cost_source = $costSource
    cost_assumptions_bps = $prepared.cost_assumptions_bps
    parity = 'passed'
} | ConvertTo-Json -Compress
