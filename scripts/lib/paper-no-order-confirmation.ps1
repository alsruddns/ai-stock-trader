function Assert-PaperNoOrderConfirmationLocks {
    param([Parameter(Mandatory)]$OperationsSummary)

    if ($OperationsSummary.tradingMode -ne 'paper' -or
        $OperationsSummary.liveTrading -ne 'disabled' -or
        $OperationsSummary.kisPaperOrdersEnabled -ne $false -or
        $OperationsSummary.paperAutomationEnabled -ne $false -or
        $OperationsSummary.kisRealOrdersEnabled -ne $false -or
        $OperationsSummary.kisRealAccountReadsEnabled -ne $false -or
        $OperationsSummary.kisRealReconciliationEnabled -ne $false) {
        throw 'Trading safety locks do not match the required read-only PAPER state.'
    }
}

function Get-PaperNoOrderConfirmationCandidate {
    param(
        [Parameter(Mandatory)]$OperationsSummary,
        [Parameter(Mandatory)][ValidatePattern('^\d{4}-\d{2}-\d{2}$')][string]$SessionDate,
        [Parameter(Mandatory)][ValidatePattern('^\d{6}$')][string]$Symbol,
        [Parameter(Mandatory)][ValidatePattern('^\d{2}:\d{2}$')][string]$OrderMinute
    )

    $kst = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
    $target = @($OperationsSummary.recentPaperKisOrders | Where-Object {
        if ($_.market -ne 'KR' -or $_.symbol -ne $Symbol -or $_.side -ne 'BUY' -or $_.quantity -ne 1) {
            return $false
        }
        try {
            $createdValue = $_.createdAt
            if ($createdValue -is [DateTimeOffset]) {
                $created = $createdValue
            } elseif ($createdValue -is [DateTime]) {
                $createdDate = [DateTime]$createdValue
                if ($createdDate.Kind -eq [DateTimeKind]::Unspecified) {
                    # Spring serializes timestamps as UTC. PowerShell JSON cmdlets may
                    # deserialize them as DateTime, so preserve UTC instead of stringifying
                    # and accidentally treating the value as local time.
                    $createdDate = [DateTime]::SpecifyKind($createdDate, [DateTimeKind]::Utc)
                }
                $created = [DateTimeOffset]::new($createdDate)
            } else {
                $created = [DateTimeOffset]::Parse(
                    [string]$createdValue,
                    [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::AssumeUniversal)
            }
            $local = [TimeZoneInfo]::ConvertTime($created, $kst)
            $local.ToString('yyyy-MM-dd') -eq $SessionDate -and $local.ToString('HH:mm') -eq $OrderMinute
        } catch { $false }
    })

    if ($target.Count -ne 1) {
        throw 'Expected exactly one matching PAPER order intent for the incident window.'
    }
    $intent = $target[0]
    if ($intent.status -eq 'NO_ORDER_CONFIRMED') {
        return [pscustomobject]@{ alreadyConfirmed = $true; idempotencyKey = $null }
    }
    if ($intent.status -ne 'UNKNOWN' -or $intent.filledQuantity -ne 0 -or
        $intent.brokerOrderReferencePresent -ne $false -or
        [string]$intent.idempotencyKey -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') {
        throw 'The incident intent is no longer an eligible unlinked, unfilled UNKNOWN PAPER order.'
    }
    return [pscustomobject]@{ alreadyConfirmed = $false; idempotencyKey = [string]$intent.idempotencyKey }
}
