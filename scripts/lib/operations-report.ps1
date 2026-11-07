function Invoke-AiTraderOptionalJsonRequest {
    param([Parameter(Mandatory)][string]$Url)

    try {
        $data = Invoke-RestMethod -Uri $Url -TimeoutSec 5
        return [pscustomobject]@{
            data = $data
            state = 'AVAILABLE'
            http_status = $null
            error_type = $null
        }
    }
    catch {
        $httpStatus = $null
        try {
            if ($null -ne $_.Exception.Response -and $null -ne $_.Exception.Response.StatusCode) {
                $parsedStatus = [int]$_.Exception.Response.StatusCode
                if ($parsedStatus -gt 0) { $httpStatus = $parsedStatus }
            }
        } catch { }
        $state = if ($null -ne $httpStatus) { 'HTTP_ERROR' } else { 'UNREACHABLE' }
        return [pscustomobject]@{
            data = $null
            state = $state
            http_status = $httpStatus
            error_type = $_.Exception.GetType().Name
        }
    }
}

function Set-AiTraderAtomicJsonFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)]$Value,
        [ValidateRange(1, 100)][int]$Depth = 12
    )

    $directory = Split-Path -Parent $Path
    if ([string]::IsNullOrWhiteSpace($directory) -or -not (Test-Path -LiteralPath $directory -PathType Container)) {
        throw 'The operations report output directory must already exist.'
    }
    $temporaryPath = Join-Path $directory ('.{0}.{1}.tmp' -f [IO.Path]::GetFileName($Path), [guid]::NewGuid().ToString('N'))
    $backupPath = "$temporaryPath.bak"
    try {
        $json = $Value | ConvertTo-Json -Depth $Depth
        [IO.File]::WriteAllText($temporaryPath, $json, [Text.Encoding]::UTF8)
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [IO.File]::Replace($temporaryPath, $Path, $backupPath)
        } else {
            [IO.File]::Move($temporaryPath, $Path)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
        if (Test-Path -LiteralPath $backupPath -PathType Leaf) {
            Remove-Item -LiteralPath $backupPath -Force
        }
    }
}
