#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (ARCH-012) tests for
    orchestrator/Import-WaggleCodexFindings.ps1.

    Cases:
      - well-formed synthetic codex_findings.json -> import succeeds
      - wrong epoch_id -> .invalid record (no schema-valid output)
      - schema-invalid (missing required field) -> .invalid record
      - synthetic credential in evidence text -> redacted before
        storage (tested by reading the stored JSON)
      - missing optional field (claimed_version null) -> still
        imports if required fields present
      - two consecutive imports -> both kept; the stable
        findings.json points at the most recent
      - parsable but completed=false -> .invalid
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'Import-WaggleCodexFindings.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$tmp = Join-Path $env:TEMP ("waggle-test-cdx-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

function New-FakeProject {
    param([string] $Name)
    $root = Join-Path $tmp $Name
    [void](New-Item -ItemType Directory -Path $root -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'iterations') -Force)
    $cfg = @{ projectRoot = $root; iterationsDir = 'iterations' }
    $cfgPath = Join-Path $root 'orchestrator.config.json'
    Set-Content -Path $cfgPath -Value ($cfg | ConvertTo-Json -Depth 5) -Encoding UTF8
    return [pscustomobject]@{ root = $root; cfg = $cfgPath }
}

function New-FakeIteration { param([string] $Root, [string] $Id)
    $iter = Join-Path (Join-Path $Root 'iterations') $Id
    [void](New-Item -ItemType Directory -Path $iter -Force)
}

function New-CodexFindings {
    param([string] $EpochId, [string] $IterationId, [string] $LeakedToken = '')
    $obj = [ordered]@{
        format_version = '1.0'
        scout_self_id = [ordered]@{
            tool = 'codex_cli'
            version = '0.1.x'
            model = 'gpt-codex-mini'
            worktree_root = 'C:\\Python\\project2-codex-scout'
            ran_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        }
        scope = [ordered]@{
            epoch_id = $EpochId
            target_iteration_ids = @($IterationId)
            branch_at_scan = 'phase2br'
            commit_at_scan = ('a' * 40)
        }
        findings = @(
            [ordered]@{
                id = 'CDEX-001'
                severity = 'medium'
                category = 'reliability'
                title = 'lock release ordering on early return'
                where = 'lib/Lockfile.ps1:120'
                evidence = 'partial release on early return path' + $(if ($LeakedToken) { ' (token: ' + $LeakedToken + ')' } else { '' })
                why_it_matters = 'subsequent iterations see stale lock'
                recommended_action = 'wrap release in finally'
            }
        )
        proposals = @(
            [ordered]@{
                id = 'CDEX-PROP-001'
                title = 'add retry/backoff to ClaudeRunner'
                rationale = 'cold-start flakes'
                approach = 'wrap process start in retry loop'
                estimated_effort = 'small'
                risks = 'transient false positives'
                expected_payoff = 'fewer flaky CI runs'
            }
        )
        completed = $true
    }
    return $obj
}

# ---- Test 1: valid import -----------------------------------------------

$proj = New-FakeProject -Name 'cdx1'
$iid = '2026-05-07_e1'
New-FakeIteration -Root $proj.root -Id $iid
$findingsObj = New-CodexFindings -EpochId 'e1' -IterationId $iid
$findingsPath = Join-Path $proj.root 'codex_findings.json'
Set-Content -Path $findingsPath -Value (([pscustomobject]$findingsObj) | ConvertTo-Json -Depth 10) -Encoding UTF8
$r = Import-WaggleCodexFindings -ConfigPath $proj.cfg -EpochId 'e1' -IterationId $iid -FindingsFile $findingsPath
Assert-True 'valid: ok=true' ($r.ok -eq $true)
Assert-True 'valid: 1 finding'  ($r.finding_count -eq 1)
Assert-True 'valid: 1 proposal' ($r.proposal_count -eq 1)
Assert-True 'valid: json_path exists' (Test-Path -LiteralPath $r.json_path)
Assert-True 'valid: md_path exists'   (Test-Path -LiteralPath $r.md_path)
Assert-True 'valid: stable findings.json copy exists' (Test-Path -LiteralPath (Join-Path (Join-Path (Join-Path $proj.root 'iterations') $iid) 'codex/findings.json'))

# ---- Test 2: wrong epoch_id rejected ------------------------------------

$proj2 = New-FakeProject -Name 'cdx2'
$iid2 = '2026-05-07_e2'
New-FakeIteration -Root $proj2.root -Id $iid2
$obj2 = New-CodexFindings -EpochId 'WRONG' -IterationId $iid2
$path2 = Join-Path $proj2.root 'cf.json'
Set-Content -Path $path2 -Value (([pscustomobject]$obj2) | ConvertTo-Json -Depth 10) -Encoding UTF8
$r2 = Import-WaggleCodexFindings -ConfigPath $proj2.cfg -EpochId 'e2' -IterationId $iid2 -FindingsFile $path2
Assert-True 'epoch-mismatch: ok=false'                ($r2.ok -eq $false)
Assert-True 'epoch-mismatch: reason=epoch_id_mismatch' ($r2.reason -eq 'epoch_id_mismatch')

# ---- Test 3: schema-invalid (missing finding fields) --------------------

$proj3 = New-FakeProject -Name 'cdx3'
$iid3 = '2026-05-07_e3'
New-FakeIteration -Root $proj3.root -Id $iid3
$obj3 = New-CodexFindings -EpochId 'e3' -IterationId $iid3
$obj3.findings = @([ordered]@{ id = 'CDEX-002'; severity = 'low'; category = 'bug'; title = 'tiny' })
$path3 = Join-Path $proj3.root 'cf.json'
Set-Content -Path $path3 -Value (([pscustomobject]$obj3) | ConvertTo-Json -Depth 10) -Encoding UTF8
$r3 = Import-WaggleCodexFindings -ConfigPath $proj3.cfg -EpochId 'e3' -IterationId $iid3 -FindingsFile $path3
Assert-True 'schema-invalid: ok=false'              ($r3.ok -eq $false)
Assert-True 'schema-invalid: reason=schema_invalid' ($r3.reason -eq 'schema_invalid')

# ---- Test 4: synthetic credential redacted before storage ---------------

$proj4 = New-FakeProject -Name 'cdx4'
$iid4 = '2026-05-07_e4'
New-FakeIteration -Root $proj4.root -Id $iid4
# Runtime concat -- no contiguous token literal in source
$ghpFake = ('ghp' + '_' + ('A' * 40))
$obj4 = New-CodexFindings -EpochId 'e4' -IterationId $iid4 -LeakedToken $ghpFake
$path4 = Join-Path $proj4.root 'cf.json'
Set-Content -Path $path4 -Value (([pscustomobject]$obj4) | ConvertTo-Json -Depth 10) -Encoding UTF8
$beforeBody = Get-Content -Raw -Path $path4 -Encoding UTF8
Assert-True 'redact-fixture: pre-import body contains raw ghp_ token' ($beforeBody -match 'ghp_[A-Za-z0-9]{36,}')
$r4 = Import-WaggleCodexFindings -ConfigPath $proj4.cfg -EpochId 'e4' -IterationId $iid4 -FindingsFile $path4
Assert-True 'redact: ok=true' ($r4.ok -eq $true)
$storedJson = Get-Content -Raw -Path $r4.json_path -Encoding UTF8
Assert-True 'redact: stored json has no raw ghp_ token' ($storedJson -notmatch 'ghp_[A-Za-z0-9]{36,}')
Assert-True 'redact: stored json contains REDACTED marker' ($storedJson -match 'REDACTED')
$storedMd = Get-Content -Raw -Path $r4.md_path -Encoding UTF8
Assert-True 'redact: stored md has no raw ghp_ token' ($storedMd -notmatch 'ghp_[A-Za-z0-9]{36,}')

# ---- Test 5: optional fields null OK ------------------------------------

$proj5 = New-FakeProject -Name 'cdx5'
$iid5 = '2026-05-07_e5'
New-FakeIteration -Root $proj5.root -Id $iid5
$obj5 = New-CodexFindings -EpochId 'e5' -IterationId $iid5
$obj5.scout_self_id.version = $null
$obj5.scout_self_id.model = $null
$obj5.scout_self_id.worktree_root = $null
$obj5.scope.branch_at_scan = $null
$obj5.scope.commit_at_scan = $null
$path5 = Join-Path $proj5.root 'cf.json'
Set-Content -Path $path5 -Value (([pscustomobject]$obj5) | ConvertTo-Json -Depth 10) -Encoding UTF8
$r5 = Import-WaggleCodexFindings -ConfigPath $proj5.cfg -EpochId 'e5' -IterationId $iid5 -FindingsFile $path5
Assert-True 'optional-null: ok=true' ($r5.ok -eq $true)

# ---- Test 6: completed=false rejected -----------------------------------

$proj6 = New-FakeProject -Name 'cdx6'
$iid6 = '2026-05-07_e6'
New-FakeIteration -Root $proj6.root -Id $iid6
$obj6 = New-CodexFindings -EpochId 'e6' -IterationId $iid6
$obj6.completed = $false
$path6 = Join-Path $proj6.root 'cf.json'
Set-Content -Path $path6 -Value (([pscustomobject]$obj6) | ConvertTo-Json -Depth 10) -Encoding UTF8
$r6 = Import-WaggleCodexFindings -ConfigPath $proj6.cfg -EpochId 'e6' -IterationId $iid6 -FindingsFile $path6
Assert-True 'completed-false: ok=false' ($r6.ok -eq $false)
Assert-True 'completed-false: reason=schema_invalid' ($r6.reason -eq 'schema_invalid')

# ---- Test 7: two consecutive imports both kept --------------------------

$proj7 = New-FakeProject -Name 'cdx7'
$iid7 = '2026-05-07_e7'
New-FakeIteration -Root $proj7.root -Id $iid7
$obj7a = New-CodexFindings -EpochId 'e7' -IterationId $iid7
$path7a = Join-Path $proj7.root 'cf_a.json'
Set-Content -Path $path7a -Value (([pscustomobject]$obj7a) | ConvertTo-Json -Depth 10) -Encoding UTF8
$r7a = Import-WaggleCodexFindings -ConfigPath $proj7.cfg -EpochId 'e7' -IterationId $iid7 -FindingsFile $path7a
Assert-True 'twice-1: ok' ($r7a.ok -eq $true)
Start-Sleep -Seconds 1
$obj7b = New-CodexFindings -EpochId 'e7' -IterationId $iid7
$obj7b.findings[0].title = 'second import title'
$path7b = Join-Path $proj7.root 'cf_b.json'
Set-Content -Path $path7b -Value (([pscustomobject]$obj7b) | ConvertTo-Json -Depth 10) -Encoding UTF8
$r7b = Import-WaggleCodexFindings -ConfigPath $proj7.cfg -EpochId 'e7' -IterationId $iid7 -FindingsFile $path7b
Assert-True 'twice-2: ok' ($r7b.ok -eq $true)
Assert-True 'twice: distinct import_ids' ($r7a.import_id -ne $r7b.import_id)
$stable = Get-Content -Raw -Path (Join-Path (Join-Path (Join-Path $proj7.root 'iterations') $iid7) 'codex/findings.json') -Encoding UTF8
Assert-True 'twice: stable findings.json points at most-recent (title match)' ($stable -match 'second import title')

# ---- Cleanup ------------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
