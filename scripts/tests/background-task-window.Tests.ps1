Describe 'AI Stock Trader scheduled PowerShell tasks' {
    It 'creates startup, monitor, report, refresh, and model tasks with hidden windows' {
        $repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
        $registrationScripts = @(
            'scripts/install-local-stack-task.ps1',
            'scripts/install-dashboard-task.ps1',
            'scripts/install-daily-report-task.ps1',
            'scripts/register-post-session-refresh-task.ps1',
            'scripts/register-market-data-session-monitor.ps1',
            'scripts/register-market-data-session-horizon.ps1',
            'scripts/register-model-candidate-retraining-task.ps1',
            'scripts/register-model-holdout-evaluation-task.ps1',
            'scripts/register-dashboard-post-session-task.ps1'
        )

        foreach ($relativePath in $registrationScripts) {
            $source = Get-Content -LiteralPath (Join-Path $repositoryRoot $relativePath) -Raw
            $source | Should Match '(?s)-WindowStyle Hidden.*-ExecutionPolicy Bypass'
        }
    }

    It 'only rebuilds the dashboard after a closed domestic session and verifies the new route without orders' {
        $repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
        $source = Get-Content -LiteralPath (Join-Path $repositoryRoot 'scripts/rebuild-dashboard-after-close.ps1') -Raw
        $source | Should Match 'Assert-ClosedDomesticSession'
        $source | Should Match 'Assert-TradingLocks'
        $source | Should Match '\.next-rollback-'
        $source | Should Match '/api/paper-orders/manual-reconcile'
        $source | Should Match '\[int\]\$probe.StatusCode -ne 400'
    }
}
