$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../lib/local-stack-activation.ps1')

Describe 'Local stack API listener watchdog' {
    It 'accepts the Spring and FastAPI listeners only on loopback' {
        $listeners = @(
            [pscustomobject]@{ LocalPort = 18000; LocalAddress = '127.0.0.1'; OwningProcess = 1001 },
            [pscustomobject]@{ LocalPort = 18080; LocalAddress = '127.0.0.1'; OwningProcess = 1002 }
        )
        (Test-LocalStackApiListenersReady -Listeners $listeners) | Should Be $true
    }

    It 'rejects a missing API listener' {
        $listeners = @(
            [pscustomobject]@{ LocalPort = 18080; LocalAddress = '127.0.0.1'; OwningProcess = 1002 }
        )
        (Test-LocalStackApiListenersReady -Listeners $listeners) | Should Be $false
    }

    It 'rejects a listener exposed outside loopback' {
        $listeners = @(
            [pscustomobject]@{ LocalPort = 18000; LocalAddress = '0.0.0.0'; OwningProcess = 1001 },
            [pscustomobject]@{ LocalPort = 18080; LocalAddress = '127.0.0.1'; OwningProcess = 1002 }
        )
        (Test-LocalStackApiListenersReady -Listeners $listeners) | Should Be $false
    }

    It 'rejects ambiguous listeners for the same API port' {
        $listeners = @(
            [pscustomobject]@{ LocalPort = 18000; LocalAddress = '127.0.0.1'; OwningProcess = 1001 },
            [pscustomobject]@{ LocalPort = 18000; LocalAddress = '127.0.0.1'; OwningProcess = 1003 },
            [pscustomobject]@{ LocalPort = 18080; LocalAddress = '127.0.0.1'; OwningProcess = 1002 }
        )
        (Test-LocalStackApiListenersReady -Listeners $listeners) | Should Be $false
    }
}
