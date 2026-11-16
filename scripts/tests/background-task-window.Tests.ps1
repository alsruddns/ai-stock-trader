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
            'scripts/register-model-holdout-evaluation-task.ps1'
        )

        foreach ($relativePath in $registrationScripts) {
            $source = Get-Content -LiteralPath (Join-Path $repositoryRoot $relativePath) -Raw
            $source | Should Match '(?s)-WindowStyle Hidden.*-ExecutionPolicy Bypass'
        }
    }
}
