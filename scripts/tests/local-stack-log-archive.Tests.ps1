$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../lib/local-stack-log-archive.ps1')

Describe 'Local stack service log archive' {
    It 'preserves each previous API log before the supervisor starts a new run' {
        $logDirectory = Join-Path $TestDrive 'logs'
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $expected = @(
            'spring-boot.stdout.log',
            'spring-boot.stderr.log',
            'market-ai.stdout.log',
            'market-ai.stderr.log'
        )
        foreach ($name in $expected) {
            Set-Content -LiteralPath (Join-Path $logDirectory $name) -Value "previous:$name"
        }
        Set-Content -LiteralPath (Join-Path $logDirectory 'local-stack-task.log') -Value 'keep supervisor history separate'
        $timestamp = [datetime]::ParseExact('2026-11-17T10:00:00.123', 'yyyy-MM-ddTHH:mm:ss.fff',
            [Globalization.CultureInfo]::InvariantCulture)

        $archived = @(Archive-LocalStackServiceLogs -LogDirectory $logDirectory -Timestamp $timestamp)

        $archived.Count | Should Be 4
        foreach ($name in $expected) {
            $archiveName = [IO.Path]::GetFileNameWithoutExtension($name) + '.20261117-100000-123.log'
            $archivePath = Join-Path (Join-Path $logDirectory 'archive') $archiveName
            (Get-Content -LiteralPath $archivePath -Raw).Trim() | Should Be "previous:$name"
            (Test-Path -LiteralPath (Join-Path $logDirectory $name)) | Should Be $true
        }
        (Test-Path -LiteralPath (Join-Path (Join-Path $logDirectory 'archive') 'local-stack-task.20261117-100000-123.log')) |
            Should Be $false
    }

    It 'does nothing when there are no previous service logs' {
        $logDirectory = Join-Path $TestDrive 'empty-logs'
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

        @(Archive-LocalStackServiceLogs -LogDirectory $logDirectory).Count | Should Be 0
        (Test-Path -LiteralPath (Join-Path $logDirectory 'archive')) | Should Be $false
    }
}
