. (Join-Path $PSScriptRoot '../lib/tracked-credential-scan.ps1')

Describe 'Tracked KIS credential scan' {
    It 'ignores empty settings and explicit documentation placeholders' {
        (Get-KisTrackedCredentialFinding -Path '.env.example' -LineNumber 1 -Line 'KIS_APP_KEY=') | Should Be $null
        (Get-KisTrackedCredentialFinding -Path '.env.example' -LineNumber 2 -Line 'KIS_APP_SECRET=TEST_ONLY_PLACEHOLDER') | Should Be $null
        (Get-KisTrackedCredentialFinding -Path '.env.example' -LineNumber 3 -Line 'KIS_PAPER_ACCOUNT_NUMBER=YOUR_ACCOUNT_NUMBER') | Should Be $null
    }

    It 'flags a non-placeholder KIS key value without returning the value' {
        $line = 'KIS_REAL_APP_SECRET=' + ('A' * 40)
        $finding = Get-KisTrackedCredentialFinding -Path 'settings.env' -LineNumber 7 -Line $line
        $finding.Path | Should Be 'settings.env'
        $finding.Line | Should Be 7
        $finding.Name | Should Be 'KIS_REAL_APP_SECRET'
        ($finding.PSObject.Properties.Name -contains 'Value') | Should Be $false
    }

    It 'flags a committed local environment filename regardless of its contents' {
        $root = Join-Path $env:TEMP ('kis-credential-scan-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        try {
            & git -C $root init -q
            if ($LASTEXITCODE -ne 0) { throw 'Could not initialize temporary test repository.' }
            Set-Content -LiteralPath (Join-Path $root '.env_local') -Value 'local value is deliberately not read by this test'
            & git -C $root add -f .env_local
            if ($LASTEXITCODE -ne 0) { throw 'Could not stage temporary environment file fixture.' }
            $findings = @(Get-KisTrackedCredentialFindings -RepositoryRoot $root)
            $findings.Count | Should Be 1
            $findings[0].Name | Should Be 'tracked environment file'
        } finally {
            Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    It 'detects credentials in tracked text files while suppressing values' {
        $root = Join-Path $env:TEMP ('kis-credential-scan-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        try {
            & git -C $root init -q
            if ($LASTEXITCODE -ne 0) { throw 'Could not initialize temporary test repository.' }
            $line = 'KIS_PAPER_APP_KEY=' + ('B' * 40)
            Set-Content -LiteralPath (Join-Path $root 'settings.ps1') -Value $line
            & git -C $root add settings.ps1
            if ($LASTEXITCODE -ne 0) { throw 'Could not stage temporary test fixture.' }
            $findings = @(Get-KisTrackedCredentialFindings -RepositoryRoot $root)
            $findings.Count | Should Be 1
            $findings[0].Path | Should Be 'settings.ps1'
            $findings[0].Name | Should Be 'KIS_PAPER_APP_KEY'
            ($findings[0].PSObject.Properties.Name -contains 'Value') | Should Be $false
        } finally {
            Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    It 'passes for tracked documentation templates and does not scan ignored local values' {
        $root = Join-Path $env:TEMP ('kis-credential-scan-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        try {
            & git -C $root init -q
            if ($LASTEXITCODE -ne 0) { throw 'Could not initialize temporary test repository.' }
            Set-Content -LiteralPath (Join-Path $root '.env.example') -Value @('KIS_APP_KEY=', 'KIS_APP_SECRET=TEST_ONLY_PLACEHOLDER')
            Set-Content -LiteralPath (Join-Path $root '.gitignore') -Value '.env_local'
            Set-Content -LiteralPath (Join-Path $root '.env_local') -Value ('KIS_APP_SECRET=' + ('C' * 40))
            & git -C $root add .env.example .gitignore
            if ($LASTEXITCODE -ne 0) { throw 'Could not stage temporary test templates.' }
            $findings = @(Get-KisTrackedCredentialFindings -RepositoryRoot $root)
            $findings.Count | Should Be 0
        } finally {
            Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
