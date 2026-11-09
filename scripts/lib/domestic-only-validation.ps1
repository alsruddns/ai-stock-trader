function Assert-DomesticOnlyValidation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateSet('KR', 'US')]
        [string]$Market,
        [Parameter(Mandatory)]
        [string]$Operation
    )

    if ($Market -ne 'KR') {
        throw "Current $Operation scope is domestic KR only. US operational validation is disabled; keep overseas development to mocked or fixed-fixture unit tests."
    }
}
