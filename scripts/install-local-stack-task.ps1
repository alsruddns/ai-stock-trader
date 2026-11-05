[CmdletBinding(SupportsShouldProcess)]
param([string]$TaskName = 'AIStockTrader-LocalStack')

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$runnerPath = Join-Path $PSScriptRoot 'run-local-stack-task.ps1'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$powerShellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
if (-not (Test-Path -LiteralPath $powerShellPath)) { $powerShellPath = Join-Path $PSHOME 'pwsh.exe' }
if (-not (Test-Path -LiteralPath $powerShellPath)) { throw 'No PowerShell executable was found for the local stack task.' }

$action = New-ScheduledTaskAction -Execute $powerShellPath `
    -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$runnerPath`"" `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 5 -MultipleInstances IgnoreNew

if ($PSCmdlet.ShouldProcess("Current Windows user '$identity'", "Register local Spring Boot + Python startup task '$TaskName'")) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings `
        -Description 'Starts the local AI Stock Trader Spring Boot and Python APIs with real KIS order switches forced off.' -Force | Out-Null
    Write-Output "Registered '$TaskName' to start the local APIs at sign-in. PostgreSQL must start before the task."
}
