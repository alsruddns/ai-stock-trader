[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [string]$ServiceName = 'postgresql-x64-16',
    [ValidateRange(1, 65535)]
    [int]$Port = 5432
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\postgres-config.ps1')

$principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell window.'
}

$service = Get-CimInstance Win32_Service -Filter "Name='$ServiceName'"
if (-not $service) { throw "PostgreSQL service '$ServiceName' was not found." }
$dataDirectory = Get-PostgresDataDirectoryFromServicePath -ServicePathName $service.PathName
$configPath = Join-Path $dataDirectory 'postgresql.conf'
$backupDirectory = Join-Path $env:ProgramData 'AIStockTrader\backups'
$wasRunning = (Get-Service -Name $ServiceName).Status -eq 'Running'
$configuration = $null

if (-not $PSCmdlet.ShouldProcess("$ServiceName on port $Port", "Back up config, bind PostgreSQL to localhost, and restart service")) {
    return
}

try {
    $configuration = Set-PostgresListenAddressesLoopback -ConfigPath $configPath -BackupDirectory $backupDirectory
    if ($configuration.Changed) {
        if ($wasRunning) { Restart-Service -Name $ServiceName -Force } else { Start-Service -Name $ServiceName }
    }

    $deadline = (Get-Date).AddSeconds(45)
    do {
        Start-Sleep -Milliseconds 500
        $currentService = Get-Service -Name $ServiceName
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty LocalAddress -Unique)
        if ($currentService.Status -eq 'Running' -and $listeners.Count -gt 0) { break }
    } while ((Get-Date) -lt $deadline)

    if ($currentService.Status -ne 'Running' -or $listeners.Count -eq 0) {
        throw 'PostgreSQL did not become ready after the configuration change.'
    }
    $nonLoopback = @($listeners | Where-Object {
        try { -not [System.Net.IPAddress]::IsLoopback([System.Net.IPAddress]::Parse($_)) }
        catch { $true }
    })
    if ($nonLoopback.Count -gt 0) {
        throw "PostgreSQL is still listening outside loopback: $($nonLoopback -join ', ')."
    }

    Write-Output "PostgreSQL is listening on loopback only at port $Port."
    if ($configuration.BackupPath) { Write-Output "Configuration backup: $($configuration.BackupPath)" }
} catch {
    if ($configuration -and $configuration.Changed -and $configuration.BackupPath) {
        try {
            Copy-Item -LiteralPath $configuration.BackupPath -Destination $configPath -Force
            if ($wasRunning) { Restart-Service -Name $ServiceName -Force } else { Start-Service -Name $ServiceName }
            throw 'The loopback change did not verify; the original PostgreSQL configuration was restored.'
        } catch {
            if ($_.Exception.Message -like 'The loopback change did not verify*') { throw }
            throw 'The loopback change failed and automatic rollback could not be verified. Restore the backup manually.'
        }
    }
    throw
}
