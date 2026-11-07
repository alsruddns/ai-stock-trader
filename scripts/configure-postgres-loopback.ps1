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
$binaryDirectory = Get-PostgresBinaryDirectoryFromServicePath -ServicePathName $service.PathName
$configPath = Join-Path $dataDirectory 'postgresql.conf'
$pgIsReadyPath = Join-Path $binaryDirectory 'pg_isready.exe'
if (-not (Test-Path -LiteralPath $pgIsReadyPath -PathType Leaf)) { throw 'pg_isready.exe was not found beside the PostgreSQL service binary.' }
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
    $readyDeadline = (Get-Date).AddSeconds(15)
    $ready = $false
    do {
        & $pgIsReadyPath -h localhost -p $Port -q 2>$null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $readyDeadline)
    if (-not $ready) { throw 'PostgreSQL listener exists but pg_isready did not confirm connection readiness.' }

    Write-Output "PostgreSQL is ready on loopback only at port $Port."
    if ($configuration.BackupPath) { Write-Output "Configuration backup: $($configuration.BackupPath)" }
} catch {
    if ($configuration -and $configuration.Changed -and $configuration.BackupPath) {
        try {
            Copy-Item -LiteralPath $configuration.BackupPath -Destination $configPath -Force
            if ($wasRunning) { Restart-Service -Name $ServiceName -Force } else { Start-Service -Name $ServiceName }
            $rollbackDeadline = (Get-Date).AddSeconds(45)
            do {
                Start-Sleep -Milliseconds 500
                $rollbackService = Get-Service -Name $ServiceName
                if ($rollbackService.Status -eq 'Running') { break }
            } while ((Get-Date) -lt $rollbackDeadline)
            if ($rollbackService.Status -ne 'Running') { throw 'PostgreSQL service did not recover after restoring the backup.' }
            if ((Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash -ne
                (Get-FileHash -LiteralPath $configuration.BackupPath -Algorithm SHA256).Hash) {
                throw 'The original PostgreSQL configuration backup was not restored exactly.'
            }
            $rollbackReadyDeadline = (Get-Date).AddSeconds(15)
            $rollbackReady = $false
            do {
                & $pgIsReadyPath -h localhost -p $Port -q 2>$null
                if ($LASTEXITCODE -eq 0) { $rollbackReady = $true; break }
                Start-Sleep -Milliseconds 500
            } while ((Get-Date) -lt $rollbackReadyDeadline)
            if (-not $rollbackReady) { throw 'PostgreSQL did not accept local connections after restoring the backup.' }
            throw 'The loopback change did not verify; the original PostgreSQL configuration was restored.'
        } catch {
            if ($_.Exception.Message -like 'The loopback change did not verify*') { throw }
            throw 'The loopback change failed and automatic rollback could not be verified. Restore the backup manually.'
        }
    }
    throw
}
