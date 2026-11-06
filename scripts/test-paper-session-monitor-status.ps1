$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'paper-session-monitor-status.ps1')

function New-Status($State, $Paper, $Real, $LastError = $null, $OrdersEnabled = $false) {
    [pscustomobject]@{
        state = $State
        enabled = $true
        orders_enabled = $OrdersEnabled
        automation_state = 'DISABLED'
        last_error = $LastError
        stream_connections = [pscustomobject]@{ paper = $Paper; real = $Real }
    }
}

# A selected PAPER feed remains valid while the unrelated REAL feed reconnects.
$selectedPaperReady = New-Status 'RECONNECTING' 'RUNNING' 'RECONNECTING' 'KIS_STREAM_UNAVAILABLE'
Assert-SafeRunningStatus $selectedPaperReady 'paper' $false

# The selected feed itself reconnecting must fail, even if the other feed is running.
$selectedPaperReconnecting = New-Status 'RECONNECTING' 'RECONNECTING' 'RUNNING' 'KIS_STREAM_UNAVAILABLE'
$failedAsExpected = $false
try { Assert-SafeRunningStatus $selectedPaperReconnecting 'paper' $false } catch { $failedAsExpected = $true }
if (-not $failedAsExpected) { throw 'A reconnecting selected feed was incorrectly accepted.' }

# Unexpected runtime errors and enabled orders remain hard failures.
$runtimeError = New-Status 'RECONNECTING' 'RUNNING' 'RECONNECTING' 'PAPER_BAR_HISTORY_RESTORE_FAILED'
$failedAsExpected = $false
try { Assert-SafeRunningStatus $runtimeError 'paper' $false } catch { $failedAsExpected = $true }
if (-not $failedAsExpected) { throw 'A non-feed runtime error was incorrectly ignored.' }
$ordersEnabled = New-Status 'RECONNECTING' 'RUNNING' 'RECONNECTING' 'KIS_STREAM_UNAVAILABLE' $true
$failedAsExpected = $false
try { Assert-SafeRunningStatus $ordersEnabled 'paper' $false } catch { $failedAsExpected = $true }
if (-not $failedAsExpected) { throw 'Enabled orders were incorrectly accepted.' }

# Unscoped or both-feed samples still require a healthy aggregate state.
$failedAsExpected = $false
try { Assert-SafeRunningStatus $selectedPaperReady '' $false } catch { $failedAsExpected = $true }
if (-not $failedAsExpected) { throw 'An unscoped degraded feed set was incorrectly accepted.' }
$failedAsExpected = $false
try { Assert-SafeRunningStatus $selectedPaperReady '' $true } catch { $failedAsExpected = $true }
if (-not $failedAsExpected) { throw 'A degraded both-feed sample was incorrectly accepted.' }

Write-Output 'Paper session status tests passed: selected-feed isolation, reconnect failure, unexpected runtime error, order lock, and aggregate readiness.'
