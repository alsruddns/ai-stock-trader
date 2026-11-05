$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$dashboardPath = Join-Path $repoRoot "dashboard"
Push-Location $dashboardPath
try {
    if (-not (Test-Path (Join-Path $dashboardPath "node_modules"))) {
        npm ci
    }
    npm run dev
}
finally {
    Pop-Location
}
