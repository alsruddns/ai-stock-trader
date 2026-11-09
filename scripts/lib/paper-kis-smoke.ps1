function Get-PaperKisSmokeMarkets {
    [CmdletBinding()]
    param([switch]$IncludeUS)

    if ($IncludeUS) { return @('KR', 'US') }
    return @('KR')
}
