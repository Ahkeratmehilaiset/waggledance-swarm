#requires -Version 5.1
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'lib/ConfigValidator.ps1')

$script:tests = 0; $script:passes = 0; $script:fails = @()
function Run([string]$name, [scriptblock]$body) {
    $script:tests++
    try { & $body; $script:passes++; Write-Host "PASS  $name" -ForegroundColor Green }
    catch { Write-Host "FAIL  $name : $($_.Exception.Message)" -ForegroundColor Red; $script:fails += $name }
}

$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("waggle-cfg-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

try {
    function New-MinimalCfg {
        return [pscustomobject]@{
            projectRoot   = $tmpDir
            transcriptDir = 'transcripts'
            iterationsDir = 'iterations'
            stateDir      = 'state'
            reportFile    = 'raportti.md'
        }
    }

    Run 'Minimal valid config passes' {
        $r = Test-WaggleConfig -Config (New-MinimalCfg)
        if (-not $r.valid) { throw ($r.errors -join '; ') }
    }
    Run 'Missing projectRoot fails' {
        $c = New-MinimalCfg; $c.PSObject.Properties.Remove('projectRoot')
        $r = Test-WaggleConfig -Config $c
        if ($r.valid) { throw 'should have failed' }
    }
    Run 'Non-existent projectRoot fails' {
        $c = New-MinimalCfg; $c.projectRoot = 'C:\nope\definitely\not'
        $r = Test-WaggleConfig -Config $c
        if ($r.valid) { throw 'should have failed' }
    }
    Run 'Negative pollIntervalSeconds fails' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName pollIntervalSeconds -NotePropertyValue (-5)
        if ((Test-WaggleConfig -Config $c).valid) { throw 'should have failed' }
    }
    Run 'Invalid regex fails' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName interactivePromptPatterns -NotePropertyValue @('[unterm')
        if ((Test-WaggleConfig -Config $c).valid) { throw 'should have failed' }
    }
    Run 'Bad executionMode enum fails' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName executionMode -NotePropertyValue 'turbo'
        if ((Test-WaggleConfig -Config $c).valid) { throw 'should have failed' }
    }
    Run 'Bad outputFormat enum fails' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName outputFormat -NotePropertyValue 'xml'
        if ((Test-WaggleConfig -Config $c).valid) { throw 'should have failed' }
    }
    Run 'Bad permissionMode enum fails' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName permissionMode -NotePropertyValue 'yolo'
        if ((Test-WaggleConfig -Config $c).valid) { throw 'should have failed' }
    }
    Run 'dangerouslySkipPermissions=true emits warning' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName dangerouslySkipPermissions -NotePropertyValue $true
        $r = Test-WaggleConfig -Config $c
        if (-not $r.valid) { throw 'unexpected error' }
        if ($r.warnings.Count -lt 1) { throw 'expected warning' }
    }
    Run 'allowBash=true emits warning' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName allowBash -NotePropertyValue $true
        $r = Test-WaggleConfig -Config $c
        if ($r.warnings.Count -lt 1) { throw 'expected warning' }
    }
    Run 'tailLineCount too small fails' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName tailLineCount -NotePropertyValue 0
        if ((Test-WaggleConfig -Config $c).valid) { throw 'should have failed' }
    }
    Run 'maxTurns out of range fails' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName maxTurns -NotePropertyValue 99999
        if ((Test-WaggleConfig -Config $c).valid) { throw 'should have failed' }
    }
    Run 'envDenylist invalid regex fails' {
        $c = New-MinimalCfg
        $c | Add-Member -NotePropertyName envDenylist -NotePropertyValue @('[unterm')
        if ((Test-WaggleConfig -Config $c).valid) { throw 'should have failed' }
    }
    Run 'Assert-WaggleConfig throws on invalid' {
        $c = New-MinimalCfg; $c.projectRoot = 'X:\nope'
        $threw = $false
        try { Assert-WaggleConfig -Config $c | Out-Null } catch { $threw = $true }
        if (-not $threw) { throw 'should have thrown' }
    }
}
finally {
    Remove-Item -Path $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("Result: {0}/{1} tests passed" -f $script:passes, $script:tests) -ForegroundColor Cyan
if ($script:fails.Count -gt 0) { exit 1 }
exit 0
