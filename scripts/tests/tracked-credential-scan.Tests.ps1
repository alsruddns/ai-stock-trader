. (Join-Path $PSScriptRoot '../lib/tracked-credential-scan.ps1')
$kisPrefix = 'KIS_'
$appKeyName = $kisPrefix + 'APP_KEY'
$appSecretName = $kisPrefix + 'APP_SECRET'
$paperAppKeyName = $kisPrefix + 'PAPER_APP_KEY'
$realAppSecretName = $kisPrefix + 'REAL_APP_SECRET'
$paperAccountNumberName = $kisPrefix + 'PAPER_ACCOUNT_NUMBER'

Describe 'Tracked KIS credential scan' {
    It 'ignores empty settings and explicit documentation placeholders' {
        (Get-KisTrackedCredentialFinding -Path '.env.example' -LineNumber 1 -Line ($appKeyName + '=')) | Should Be $null
        (Get-KisTrackedCredentialFinding -Path '.env.example' -LineNumber 2 -Line ($appSecretName + '=TEST_ONLY_PLACEHOLDER')) | Should Be $null
        (Get-KisTrackedCredentialFinding -Path '.env.example' -LineNumber 3 -Line ($paperAccountNumberName + '=YOUR_ACCOUNT_NUMBER')) | Should Be $null
    }

    It 'flags a non-placeholder KIS key value without returning the value' {
        $line = ($realAppSecretName + '=') + ('A' * 40)
        $finding = Get-KisTrackedCredentialFinding -Path 'settings.env' -LineNumber 7 -Line $line
        $finding.Path | Should Be 'settings.env'
        $finding.Line | Should Be 7
        $finding.Name | Should Be $realAppSecretName
        ($finding.PSObject.Properties.Name -contains 'Value') | Should Be $false
    }

    It 'detects secret assignments in JSON and YAML settings without returning values' {
        $json = Get-KisTrackedCredentialFinding -Path 'settings.json' -LineNumber 2 -Line ('"' + $realAppSecretName + '": "' + ('D' * 40) + '",')
        $yaml = Get-KisTrackedCredentialFinding -Path 'settings.yml' -LineNumber 3 -Line ($paperAccountNumberName + ': "' + ('1' * 10) + '"')
        $json.Name | Should Be $realAppSecretName
        $yaml.Name | Should Be $paperAccountNumberName
        ($json.PSObject.Properties.Name -contains 'Value') | Should Be $false
        ($yaml.PSObject.Properties.Name -contains 'Value') | Should Be $false
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
            $line = ($paperAppKeyName + '=') + ('B' * 40)
            Set-Content -LiteralPath (Join-Path $root 'settings.ps1') -Value $line
            & git -C $root add settings.ps1
            if ($LASTEXITCODE -ne 0) { throw 'Could not stage temporary test fixture.' }
            $findings = @(Get-KisTrackedCredentialFindings -RepositoryRoot $root)
            $findings.Count | Should Be 1
            $findings[0].Path | Should Be 'settings.ps1'
            $findings[0].Name | Should Be $paperAppKeyName
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
            Set-Content -LiteralPath (Join-Path $root '.env.example') -Value @(($appKeyName + '='), ($appSecretName + '=TEST_ONLY_PLACEHOLDER'))
            Set-Content -LiteralPath (Join-Path $root '.gitignore') -Value '.env_local'
            Set-Content -LiteralPath (Join-Path $root '.env_local') -Value (($appSecretName + '=') + ('C' * 40))
            & git -C $root add .env.example .gitignore
            if ($LASTEXITCODE -ne 0) { throw 'Could not stage temporary test templates.' }
            $findings = @(Get-KisTrackedCredentialFindings -RepositoryRoot $root)
            $findings.Count | Should Be 0
        } finally {
            Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
