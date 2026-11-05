$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Missing .env. Copy .env.example and configure the paper account locally.'
}

foreach ($line in Get-Content -LiteralPath $envPath) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
    $separator = $trimmed.IndexOf('=')
    if ($separator -lt 1) { continue }
    $name = $trimmed.Substring(0, $separator).Trim()
    $value = $trimmed.Substring($separator + 1).Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}

if ($env:KIS_ACCOUNT_READ_ENABLED -ne 'true') {
    throw 'Set KIS_ACCOUNT_READ_ENABLED=true in .env before explicitly running paper account GET tests.'
}
if ($env:KIS_PAPER_ACCOUNT_READ_ENABLED -ne 'true') {
    throw 'Set KIS_PAPER_ACCOUNT_READ_ENABLED=true in .env before explicitly running paper account GET tests.'
}
if (-not $env:KIS_PAPER_APP_KEY -or -not $env:KIS_PAPER_APP_SECRET -or
    -not $env:KIS_PAPER_ACCOUNT_NUMBER) {
    throw 'Configure the paper KIS key, secret, and account number in .env. Values are not displayed.'
}

$env:RUN_KIS_PAPER_ACCOUNT_INTEGRATION_TESTS = 'true'
Push-Location (Join-Path $repoRoot 'backend/trading-api')
try {
    & .\mvnw.cmd '-Dtest=KisPaperAccountIntegrationTest' test
    if ($LASTEXITCODE -ne 0) {
        throw "KIS paper account integration test failed with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
