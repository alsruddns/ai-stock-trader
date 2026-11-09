function Get-PaperKisSmokeMarkets {
    [CmdletBinding()]
    param()

    # Broker-backed PAPER integration checks stay domestic until overseas
    # operational testing is explicitly approved in a later phase.
    return @('KR')
}
