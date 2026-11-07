[CmdletBinding(SupportsShouldProcess)]
param([string]$TaskName = 'AIStockTrader-Dashboard')

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runnerPath = Join-Path $PSScriptRoot 'run-dashboard-task.ps1'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powerShellPath)) { $powerShellPath = Join-Path $PSHOME 'pwsh.exe' }
if (-not (Test-Path -LiteralPath $powerShellPath)) { throw 'No PowerShell executable was found for the dashboard task.' }

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    $matchesThisRepository = @($existing.Actions | Where-Object {
        [string]$_.Arguments -match [regex]::Escape($runnerPath)
    }).Count -gt 0
    if (-not $matchesThisRepository) { throw "Scheduled task '$TaskName' already exists with a different action; it was not replaced." }
}

$action = New-ScheduledTaskAction -Execute $powerShellPath `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`"" `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 5 -MultipleInstances IgnoreNew

if ($PSCmdlet.ShouldProcess("Current Windows user '$identity'", "Register local read-only dashboard task '$TaskName'")) {
    if ($existing -and $existing.State -eq 'Running') { Stop-ScheduledTask -TaskName $TaskName }
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description 'Starts the loopback-only read-only AI Stock Trader dashboard after Spring and FastAPI report PAPER-locked state.' -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Registered '$TaskName' to start the loopback-only dashboard at sign-in."
}
