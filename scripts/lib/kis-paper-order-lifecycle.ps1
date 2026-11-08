function Get-KisPaperOrderLifecycleResult {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [object]$Intent)

    $filledQuantity = 0
    $hasValidFillQuantity = $null -ne $Intent.filledQuantity -and
        [int]::TryParse([string]$Intent.filledQuantity, [ref]$filledQuantity) -and
        $filledQuantity -ge 0
    $fillLabel = if ($hasValidFillQuantity) { [string]$filledQuantity } else { 'unknown' }
    $orderQuantity = 0
    $hasValidOrderQuantity = $null -ne $Intent.quantity -and
        [int]::TryParse([string]$Intent.quantity, [ref]$orderQuantity) -and
        $orderQuantity -gt 0

    if ($Intent.status -eq 'CANCELLED' -and $hasValidFillQuantity -and $filledQuantity -eq 0) {
        return [pscustomobject]@{
            exitCode = 0
            filledQuantity = $fillLabel
            message = 'PAPER order was cancelled with zero fills.'
        }
    }

    if ($Intent.status -eq 'CANCELLED' -and $hasValidFillQuantity -and $filledQuantity -gt 0 -and
        $hasValidOrderQuantity -and $filledQuantity -lt $orderQuantity) {
        return [pscustomobject]@{
            exitCode = 2
            filledQuantity = $fillLabel
            message = "PAPER order was cancelled after a partial fill of $filledQuantity share(s). Review and close any remaining simulated position."
        }
    }

    if ($Intent.status -eq 'CANCELLED' -and $hasValidFillQuantity -and $filledQuantity -gt 0 -and
        $hasValidOrderQuantity -and $filledQuantity -ge $orderQuantity) {
        return [pscustomobject]@{
            exitCode = 2
            filledQuantity = $fillLabel
            message = "All $filledQuantity requested PAPER share(s) filled before cancellation. Review and close the simulated position."
        }
    }

    if ($Intent.status -eq 'FILLED' -or ($hasValidFillQuantity -and $filledQuantity -gt 0)) {
        return [pscustomobject]@{
            exitCode = 2
            filledQuantity = $fillLabel
            message = "A simulated fill occurred ($fillLabel share(s)); review and close the PAPER position if needed."
        }
    }

    if ($Intent.status -eq 'REJECTED' -and $hasValidFillQuantity -and $filledQuantity -eq 0) {
        return [pscustomobject]@{
            exitCode = 1
            filledQuantity = $fillLabel
            message = 'KIS PAPER rejected the order with zero fills; no accepted order remains.'
        }
    }

    if ($Intent.status -eq 'CANCELLED' -and (-not $hasValidFillQuantity -or -not $hasValidOrderQuantity)) {
        return [pscustomobject]@{
            exitCode = 2
            filledQuantity = $fillLabel
            message = 'Broker cancellation result is missing a valid fill or order quantity. Inspect the PAPER position manually.'
        }
    }

    return [pscustomobject]@{
        exitCode = 2
        filledQuantity = $fillLabel
        message = 'Broker state is not a confirmed zero-fill cancellation. Do not resubmit; reconcile this key manually.'
    }
}
