function Get-PostgresDataDirectoryFromServicePath {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$ServicePathName)

    $match = [regex]::Match($ServicePathName, '-D\s+(?:"(?<quoted>[^"]+)"|(?<plain>\S+))')
    if (-not $match.Success) {
        throw 'Could not locate the PostgreSQL data directory from the Windows service definition.'
    }
    if ($match.Groups['quoted'].Success) { return $match.Groups['quoted'].Value }
    return $match.Groups['plain'].Value
}

function Get-PostgresBinaryDirectoryFromServicePath {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$ServicePathName)

    $match = [regex]::Match($ServicePathName, '^\s*(?:"(?<quoted>[^"]*pg_ctl\.exe)"|(?<plain>\S*pg_ctl\.exe))(?:\s|$)', 'IgnoreCase')
    if (-not $match.Success) {
        throw 'Could not locate pg_ctl.exe from the Windows service definition.'
    }
    $executablePath = if ($match.Groups['quoted'].Success) { $match.Groups['quoted'].Value } else { $match.Groups['plain'].Value }
    return Split-Path -Parent $executablePath
}

function Set-PostgresListenAddressesLoopback {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$ConfigPath,
        [Parameter(Mandatory)]
        [string]$BackupDirectory
    )

    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw 'PostgreSQL configuration file was not found.'
    }

    $originalBytes = [System.IO.File]::ReadAllBytes($ConfigPath)
    $hasUtf8Bom = $originalBytes.Length -ge 3 -and
        $originalBytes[0] -eq 0xEF -and $originalBytes[1] -eq 0xBB -and $originalBytes[2] -eq 0xBF
    $originalText = [System.Text.Encoding]::UTF8.GetString($originalBytes)
    if ($hasUtf8Bom -and $originalText.Length -gt 0 -and $originalText[0] -eq [char]0xFEFF) {
        $originalText = $originalText.Substring(1)
    }

    $pattern = "(?m)^(?<prefix>\s*listen_addresses\s*=\s*)'[^']*'(?<suffix>[^\n]*)$"
    $directives = [regex]::Matches($originalText, $pattern)
    if ($directives.Count -ne 1) {
        throw 'Expected exactly one active listen_addresses setting; configuration was not changed.'
    }

    $currentValue = $directives[0].Value -replace "^.*?=\s*'([^']*)'.*$", '$1'
    if ($currentValue -ceq 'localhost') {
        return [pscustomobject]@{ Changed = $false; BackupPath = $null }
    }

    New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
    $backupPath = Join-Path $BackupDirectory ('postgresql.conf.before-loopback-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N') + '.bak')
    Copy-Item -LiteralPath $ConfigPath -Destination $backupPath

    $replacement = $directives[0].Groups['prefix'].Value + "'localhost'" + $directives[0].Groups['suffix'].Value
    $updatedText = [regex]::Replace($originalText, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($match) $replacement }, 1)
    $encoding = New-Object System.Text.UTF8Encoding($hasUtf8Bom)
    $updatedBytes = $encoding.GetBytes($updatedText)
    $temporaryPath = $ConfigPath + '.loopback-' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [System.IO.File]::WriteAllBytes($temporaryPath, $updatedBytes)
        Move-Item -LiteralPath $temporaryPath -Destination $ConfigPath -Force
    } catch {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        Copy-Item -LiteralPath $backupPath -Destination $ConfigPath -Force
        throw 'Could not safely update PostgreSQL configuration; the original file was restored.'
    }

    return [pscustomobject]@{ Changed = $true; BackupPath = $backupPath }
}
