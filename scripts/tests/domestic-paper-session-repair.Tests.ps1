$ErrorActionPreference = 'Stop'
$monitorPath = Join-Path $PSScriptRoot '..\test-paper-websocket-monitor.ps1'
$tokens = $null
$parseErrors = $null
$monitorAst = [System.Management.Automation.Language.Parser]::ParseFile($monitorPath, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw 'The domestic PAPER monitor script has PowerShell syntax errors.' }
$repairFunction = $monitorAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Try-RepairDomesticPaperSessionBars'
}, $true)
if (-not $repairFunction) { throw 'The domestic PAPER REST repair function was not found.' }
. ([ScriptBlock]::Create($repairFunction.Extent.Text))

function New-TestPaperQualityRow([string]$Symbol, [int]$WebsocketBars, [int]$RestBars = 0) {
    $observed = $WebsocketBars + $RestBars
    $spreadBars = $WebsocketBars
    $sourceCounts = [ordered]@{ KIS_PAPER_WEBSOCKET = $WebsocketBars }
    if ($RestBars -gt 0) { $sourceCounts.KIS_REST_MINUTE_SESSION = $RestBars }
    [pscustomobject]@{
        market = 'KR'; symbol = $Symbol; session_date = $script:testSessionDate
        status = if ($observed -eq 380) { 'COMPLETE' } else { 'INCOMPLETE' }
        full_session_complete = ($observed -eq 380); expected_bars = 380; completed_minutes = $observed
        observed_bars = $observed; observed_completed_bars = $observed
        missing_bars = 380 - $observed; off_grid_bars = 0; duplicate_bars = 0
        spread_bars = $spreadBars; spread_coverage_percent = [Math]::Round(100 * $spreadBars / $observed, 2)
        spread_observation_time_bars = $spreadBars
        spread_observation_time_coverage_percent = [Math]::Round(100 * $spreadBars / $observed, 2)
        source_counts = [pscustomobject]$sourceCounts
    }
}

Describe 'Domestic PAPER REST minute-session recovery' {
    BeforeEach {
        $zone = [TimeZoneInfo]::FindSystemTimeZoneById('Korea Standard Time')
        $script:testSessionDate = [TimeZoneInfo]::ConvertTime([DateTimeOffset]::Now, $zone).ToString('yyyy-MM-dd')
        $script:Feed = 'paper'
        $script:repairCalls = @()
        Mock Invoke-RestMethod {
            param($Uri, $TimeoutSec)
            if ($Uri -like '*/api/v1/operations/summary') {
                return [pscustomobject]@{
                    tradingMode = 'paper'; liveTrading = 'disabled'; kisPaperOrdersEnabled = $false
                    paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
                }
            }
            $script:repairCalls += $Uri
            $symbol = [regex]::Match($Uri, '/KR/(\d{6})/bars').Groups[1].Value
            $opened = [DateTimeOffset]::Parse("$($script:testSessionDate)T09:00:00+09:00")
            $bars = @(for ($index = 0; $index -lt 380; $index++) {
                [pscustomobject]@{
                    timestamp = $opened.AddMinutes($index).ToString('o')
                    spread_bps = $null; spread_observed_at = $null
                }
            })
            return [pscustomobject]@{
                market = 'KR'; symbol = $symbol; session_date = $script:testSessionDate
                source = 'KIS_REST_MINUTE_SESSION'; spread_bps_available = $false; bars = $bars
            }
        }
    }

    It 'repairs only incomplete symbols and recognizes a previously repaired symbol on retry' {
        $quality = [pscustomobject]@{
            sessions = @(
                (New-TestPaperQualityRow '005930' 377),
                (New-TestPaperQualityRow '000660' 377 3)
            )
        }
        $session = [pscustomobject]@{ market = 'KR'; session_date = $script:testSessionDate }

        $result = Try-RepairDomesticPaperSessionBars $null $session 'http://127.0.0.1:18000' `
            'http://127.0.0.1:18080' @('005930', '000660') 380 $quality

        $result.attempted | Should Be $true
        $result.reason | Should Be 'restored_and_revalidated'
        $script:repairCalls.Count | Should Be 1
        $script:repairCalls[0] | Should Match '/KR/005930/bars'
        @($result.repaired_symbols | Where-Object { $_.already_restored -eq $true }).Count | Should Be 1
    }

    It 'does not fetch KIS REST data unless every PAPER and REAL order gate is locked' {
        Mock Invoke-RestMethod {
            param($Uri, $TimeoutSec)
            if ($Uri -like '*/api/v1/operations/summary') {
                return [pscustomobject]@{
                    tradingMode = 'paper'; liveTrading = 'disabled'; kisPaperOrdersEnabled = $true
                    paperAutomationEnabled = $false; kisRealOrdersEnabled = $false
                }
            }
            $script:repairCalls += $Uri
        }
        $quality = [pscustomobject]@{ sessions = @((New-TestPaperQualityRow '005930' 377)) }
        $session = [pscustomobject]@{ market = 'KR'; session_date = $script:testSessionDate }

        $result = Try-RepairDomesticPaperSessionBars $null $session 'http://127.0.0.1:18000' `
            'http://127.0.0.1:18080' @('005930') 380 $quality

        $result.attempted | Should Be $false
        $result.reason | Should Be 'orders_or_automation_not_locked'
        $script:repairCalls.Count | Should Be 0
    }
}
