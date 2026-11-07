function Assert-LocalPostgresBinding {
    [CmdletBinding()]
    param(
        [ValidateRange(1, 65535)]
        [int]$Port = 5432,
        [string[]]$ListenerAddresses
    )

    if ($PSBoundParameters.ContainsKey('ListenerAddresses')) {
        $addresses = @($ListenerAddresses | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    } else {
        try {
            $addresses = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction Stop |
                Select-Object -ExpandProperty LocalAddress -Unique)
        } catch {
            throw "Cannot verify PostgreSQL listener on port $Port. Start PostgreSQL and confirm its local-only binding before starting the application."
        }
    }

    if ($addresses.Count -eq 0) {
        throw "No PostgreSQL listener was found on port $Port. Start PostgreSQL before starting the application."
    }

    $nonLoopback = @()
    foreach ($address in $addresses) {
        try {
            $ipAddress = [System.Net.IPAddress]::Parse($address)
        } catch {
            $nonLoopback += $address
            continue
        }
        if (-not [System.Net.IPAddress]::IsLoopback($ipAddress)) {
            $nonLoopback += $address
        }
    }

    if ($nonLoopback.Count -gt 0) {
        $exposedAddresses = $nonLoopback -join ', '
        throw "PostgreSQL port $Port listens on non-loopback address(es): $exposedAddresses. Set listen_addresses = 'localhost' in postgresql.conf and restart the PostgreSQL service."
    }
}
