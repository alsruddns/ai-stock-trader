$ErrorActionPreference = 'Stop'
foreach ($path in @((Join-Path $PSScriptRoot 'configure-postgres-loopback.ps1'), (Join-Path $PSScriptRoot 'lib\postgres-config.ps1'))) {
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count -gt 0) { throw "PowerShell syntax validation failed for $path." }
}
. (Join-Path $PSScriptRoot 'lib\postgres-config.ps1')

$servicePath = '"C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe" runservice -N "postgresql-x64-16" -D "C:\Program Files\PostgreSQL\16\data" -w'
$discoveredDataPath = Get-PostgresDataDirectoryFromServicePath -ServicePathName $servicePath
if ($discoveredDataPath -ne 'C:\Program Files\PostgreSQL\16\data') { throw 'Quoted PostgreSQL service data paths must be discovered exactly.' }
$binaryPath = Get-PostgresBinaryDirectoryFromServicePath -ServicePathName $servicePath
if ($binaryPath -ne 'C:\Program Files\PostgreSQL\16\bin') { throw 'Quoted PostgreSQL binary paths must be discovered exactly.' }
$plainPath = Get-PostgresDataDirectoryFromServicePath -ServicePathName 'pg_ctl.exe runservice -D C:\PostgreSQL\data -w'
if ($plainPath -ne 'C:\PostgreSQL\data') { throw 'Unquoted PostgreSQL service data paths must be discovered exactly.' }
$plainBinaryPath = Get-PostgresBinaryDirectoryFromServicePath -ServicePathName 'C:\PostgreSQL\bin\pg_ctl.exe runservice -D C:\PostgreSQL\data -w'
if ($plainBinaryPath -ne 'C:\PostgreSQL\bin') { throw 'Unquoted PostgreSQL binary paths must be discovered exactly.' }
$missingDataPathRejected = $false
try { Get-PostgresDataDirectoryFromServicePath -ServicePathName 'pg_ctl.exe runservice -N postgres' | Out-Null }
catch { $missingDataPathRejected = $_.Exception.Message -like 'Could not locate the PostgreSQL data directory*' }
if (-not $missingDataPathRejected) { throw 'A service definition without a data directory must be rejected.' }
$missingBinaryPathRejected = $false
try { Get-PostgresBinaryDirectoryFromServicePath -ServicePathName 'postgres.exe runservice' | Out-Null }
catch { $missingBinaryPathRejected = $_.Exception.Message -like 'Could not locate pg_ctl.exe*' }
if (-not $missingBinaryPathRejected) { throw 'A service definition without pg_ctl.exe must be rejected.' }

$root = Join-Path ([System.IO.Path]::GetTempPath()) ('ai-stock-trader-postgres-config-' + [guid]::NewGuid().ToString('N'))
$config = Join-Path $root 'postgresql.conf'
$backups = Join-Path $root 'backups'
New-Item -ItemType Directory -Path $root -Force | Out-Null
try {
    $original = "# local config`r`nlisten_addresses = '*'  # existing comment`r`nport = 5432`r`n"
    Set-Content -LiteralPath $config -Value $original -Encoding UTF8 -NoNewline
    $result = Set-PostgresListenAddressesLoopback -ConfigPath $config -BackupDirectory $backups
    if (-not $result.Changed -or -not $result.BackupPath) { throw 'Expected a changed configuration and a backup.' }
    $updated = Get-Content -LiteralPath $config -Raw
    if ($updated -notmatch "(?m)^listen_addresses = 'localhost'  # existing comment\r?$") { throw 'The active setting was not safely rewritten.' }
    if ($updated -notmatch "(?m)^port = 5432\r?$") { throw 'Unrelated PostgreSQL settings changed.' }
    if ((Get-Content -LiteralPath $result.BackupPath -Raw) -notmatch "(?m)^listen_addresses = '\*'  # existing comment\r?$") { throw 'The original configuration backup is incorrect.' }

    $second = Set-PostgresListenAddressesLoopback -ConfigPath $config -BackupDirectory $backups
    if ($second.Changed -or $second.BackupPath) { throw 'An already-local configuration should be a no-op.' }

    Set-Content -LiteralPath $config -Value "listen_addresses='*'`nlisten_addresses='127.0.0.1'`n" -Encoding UTF8 -NoNewline
    try {
        Set-PostgresListenAddressesLoopback -ConfigPath $config -BackupDirectory $backups | Out-Null
        throw 'Conflicting directives were not rejected.'
    } catch {
        if ($_.Exception.Message -notlike 'Expected exactly one active listen_addresses setting*') { throw }
    }

    'PostgreSQL config tests passed: rewrite, backup, no-op, and ambiguous setting rejection.'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
