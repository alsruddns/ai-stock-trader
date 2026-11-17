function Archive-LocalStackServiceLogs {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$LogDirectory,
        [datetime]$Timestamp = (Get-Date)
    )

    $knownLogs = @(
        'spring-boot.stdout.log',
        'spring-boot.stderr.log',
        'market-ai.stdout.log',
        'market-ai.stderr.log'
    )
    $existingLogs = @($knownLogs | ForEach-Object {
        $path = Join-Path $LogDirectory $_
        if (Test-Path -LiteralPath $path -PathType Leaf) { Get-Item -LiteralPath $path }
    })
    if ($existingLogs.Count -eq 0) { return @() }

    $archiveDirectory = Join-Path $LogDirectory 'archive'
    New-Item -ItemType Directory -Path $archiveDirectory -Force | Out-Null
    $stamp = $Timestamp.ToString('yyyyMMdd-HHmmss-fff', [Globalization.CultureInfo]::InvariantCulture)
    $archivedPaths = @()
    foreach ($source in $existingLogs) {
        $destination = Join-Path $archiveDirectory ("{0}.{1}{2}" -f
            [IO.Path]::GetFileNameWithoutExtension($source.Name), $stamp, $source.Extension)
        Copy-Item -LiteralPath $source.FullName -Destination $destination -ErrorAction Stop
        $archivedPaths += $destination
    }
    return $archivedPaths
}
