$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envFile = Join-Path $repoRoot '.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    throw 'Repository .env file is missing. Copy .env.example to .env and configure local PostgreSQL first.'
}

$originalEnvironment = @{}
foreach ($line in Get-Content -LiteralPath $envFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
        continue
    }

    $parts = $trimmed.Split('=', 2)
    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if (-not $originalEnvironment.ContainsKey($name)) {
        $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
}

$originalRunFlag = [Environment]::GetEnvironmentVariable('RUN_LOCAL_POSTGRES_INTEGRATION_TESTS', 'Process')
$env:RUN_LOCAL_POSTGRES_INTEGRATION_TESTS = 'true'
$apiDirectory = Join-Path $repoRoot 'backend\trading-api'
Push-Location $apiDirectory
try {
    & .\mvnw.cmd -Dtest=KisOrderIntentPostgresIntegrationTest test
    if ($LASTEXITCODE -ne 0) {
        throw "PostgreSQL integration tests failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
    foreach ($name in $originalEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
    }
    [Environment]::SetEnvironmentVariable('RUN_LOCAL_POSTGRES_INTEGRATION_TESTS', $originalRunFlag, 'Process')
}
