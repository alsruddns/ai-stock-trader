function Get-KisTrackedCredentialFinding {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [int]$LineNumber,
        [Parameter(Mandatory)] [AllowEmptyString()] [string]$Line
    )

    $match = [regex]::Match($Line,
        '^\s*(?<name>KIS_(?:(?:PAPER|REAL)_)?(?:APP_KEY|APP_SECRET|ACCOUNT_NUMBER))\s*=\s*(?<value>.*?)\s*$')
    if (-not $match.Success) { return $null }

    $value = $match.Groups['value'].Value.Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2).Trim()
    }
    if (-not $value) { return $null }
    if ($value -match '^(?:<[^>]+>|(?:YOUR|REPLACE(?:_ME)?|CHANGE(?:_ME)?|EXAMPLE|TEST_ONLY_PLACEHOLDER|DUMMY|PLACEHOLDER|SAMPLE|FAKE)(?:[_-][A-Z0-9]+)*|X{3,})$') {
        return $null
    }

    return [pscustomobject]@{ Path = $Path; Line = $LineNumber; Name = $match.Groups['name'].Value }
}

function Get-KisTrackedCredentialFindings {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string]$RepositoryRoot)

    $root = (Resolve-Path -LiteralPath $RepositoryRoot -ErrorAction Stop).Path
    $tracked = @(& git -C $root ls-files 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'Could not list tracked repository files for the credential scan.' }

    $findings = [System.Collections.Generic.List[object]]::new()
    foreach ($path in $tracked) {
        if ($path -match '(^|/)\.env(?:_local)?$') {
            $findings.Add([pscustomobject]@{ Path = $path; Line = 0; Name = 'tracked environment file' })
            continue
        }

        $extension = [IO.Path]::GetExtension($path).ToLowerInvariant()
        if ($extension -notin @('.ps1', '.py', '.java', '.kt', '.js', '.jsx', '.ts', '.tsx', '.json', '.yaml',
                '.yml', '.properties', '.toml', '.xml', '.md', '.txt', '.csv', '.sql', '.sh', '.bat', '.cmd',
                '.gradle', '.config', '.conf', '.env', '.example', '.template')) {
            continue
        }

        $fullPath = Join-Path $root ($path -replace '/', [IO.Path]::DirectorySeparatorChar)
        try { $lines = [IO.File]::ReadAllLines($fullPath) } catch { continue }
        for ($index = 0; $index -lt $lines.Length; $index++) {
            $finding = Get-KisTrackedCredentialFinding -Path $path -LineNumber ($index + 1) -Line $lines[$index]
            if ($null -ne $finding) { $findings.Add($finding) }
        }
    }
    return @($findings)
}
