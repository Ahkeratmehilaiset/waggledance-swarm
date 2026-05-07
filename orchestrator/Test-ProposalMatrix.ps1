#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (ARCH-013) tests for
    orchestrator/Build-WaggleProposalMatrix.ps1.

    Cases:
      - Empty: no proposals anywhere -> well-formed empty matrix
      - Internal-only (3 roles, 1 iter, with suggested_next_actions)
      - Internal + Codex (synthetic codex_findings.json)
      - Internal + Codex + external (synthetic Phase 2B import)
      - Schema-required fields populated on every row
      - linked_ledger_tags populates from a text match against the
        committed phase_fix_ledger.json tags
      - linked_regressions populates when a regression-ledger entry
        links to a review finding ID
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'Build-WaggleProposalMatrix.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$tmp = Join-Path $env:TEMP ("waggle-test-pmx-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

function New-FakeProject {
    param([string] $Name, [bool] $WithFixLedger = $true)
    $root = Join-Path $tmp $Name
    [void](New-Item -ItemType Directory -Path $root -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'state') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'iterations') -Force)
    if ($WithFixLedger) {
        [void](New-Item -ItemType Directory -Path (Join-Path $root 'docs/design') -Force)
        $fl = @{ rows = @(@{ tag = 'ARCH-001' }, @{ tag = 'REL-001' }, @{ tag = 'SEC-001' }) }
        Set-Content -Path (Join-Path $root 'docs/design/phase_fix_ledger.json') -Value ($fl | ConvertTo-Json -Depth 5) -Encoding UTF8
    }
    Set-Content -Path (Join-Path $root 'raportti.md') -Value '# raportti' -Encoding UTF8
    $cfg = @{
        projectRoot = $root; iterationsDir = 'iterations'; stateDir = 'state'
        reportFile = 'raportti.md'
        external_review = @{
            queue_dir_relative = 'external_reviews/queue'
            imported_dir_relative = 'external_reviews/imported'
            synthesis_dir_relative = 'external_reviews/synthesis'
        }
    }
    $cfgPath = Join-Path $root 'orchestrator.config.json'
    Set-Content -Path $cfgPath -Value ($cfg | ConvertTo-Json -Depth 10) -Encoding UTF8
    return [pscustomobject]@{ root = $root; cfg = $cfgPath }
}

function Add-FakeIteration {
    param([string] $Root, [string] $Id, [bool] $WithProposals = $true)
    $iterDir = Join-Path (Join-Path $Root 'iterations') $Id
    [void](New-Item -ItemType Directory -Path $iterDir -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $iterDir 'reviews') -Force)
    foreach ($role in 'architect','security','reliability') {
        $obj = [ordered]@{
            role = $role
            target_iteration_id = $Id
            source_package_path = "iterations/$Id/llm_input_package.md"
            summary = "synthetic $role review"
            verdict = 'pass_with_notes'
            findings = @(
                @{ id = ($role.Substring(0,3).ToUpper() + '-001'); severity = 'low'; title = "$role finding"; where = 'foo'; evidence = 'e'; why_it_matters = 'w'; recommended_action = 'r' }
            )
            metrics = @{ files_reviewed = 1; lines_reviewed = 1; review_duration_seconds = 1 }
            completed = $true
        }
        if ($WithProposals) {
            $obj.suggested_next_actions = @(
                @{ id = 'PROP-001'; title = "${role}: refactor module boundary"; rationale = 'r'; approach = "touch module foo and add test test_$role.py"; estimated_effort = 'medium'; risks = 'rk'; expected_payoff = 'p' },
                @{ id = 'PROP-002'; title = "${role}: add coverage for ARCH-001 path"; rationale = 'tag mention via text'; approach = 'expand tests/'; estimated_effort = 'small'; risks = 'rk'; expected_payoff = 'p' }
            )
        }
        Set-Content -Path (Join-Path (Join-Path $iterDir 'reviews') ($role + '.json')) -Value (([pscustomobject]$obj) | ConvertTo-Json -Depth 10) -Encoding UTF8
    }
    return $iterDir
}

function Write-Synthetic-Codex {
    param([string] $Root, [string] $CarrierIid, [string] $EpochId)
    $codexDir = Join-Path (Join-Path (Join-Path $Root 'iterations') $CarrierIid) 'codex'
    [void](New-Item -ItemType Directory -Path $codexDir -Force)
    $obj = [ordered]@{
        format_version = '1.0'
        scout_self_id = @{ tool = 'codex_cli'; version = $null; model = $null; worktree_root = 'C:\\Python\\project2-codex-scout'; ran_at_utc = (Get-Date).ToUniversalTime().ToString('o') }
        scope = @{ epoch_id = $EpochId; target_iteration_ids = @($CarrierIid); branch_at_scan = 'phase2br'; commit_at_scan = ('a' * 40) }
        findings = @(
            @{ id = 'CDEX-001'; severity = 'medium'; category = 'reliability'; title = 'lock release ordering'; where = 'lib/Lockfile.ps1:120'; evidence = 'partial release on early return'; why_it_matters = 'cleanup paths skip release'; recommended_action = 'add finally block' }
        )
        proposals = @(
            @{ id = 'CDEX-PROP-001'; title = 'add retry/backoff to ClaudeRunner'; rationale = 'cold-start flakes'; approach = 'wrap process start in retry loop'; estimated_effort = 'small'; risks = 'rk'; expected_payoff = 'fewer transient failures' }
        )
        completed = $true
    }
    Set-Content -Path (Join-Path $codexDir 'findings.json') -Value (([pscustomobject]$obj) | ConvertTo-Json -Depth 10) -Encoding UTF8
}

function Write-Synthetic-ExternalImport {
    param([string] $Root, [string] $CarrierIid, [string] $EpochId, [string] $Provider, [string] $Role)
    $imp = Join-Path (Join-Path (Join-Path $Root 'iterations') $CarrierIid) 'external_reviews/imported'
    [void](New-Item -ItemType Directory -Path $imp -Force)
    $importId = ((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH-mm-ssZ') + '_' + $Provider + '_' + $Role + '_' + ([guid]::NewGuid().ToString('N').Substring(0,8)))
    $obj = [ordered]@{
        reviewer_self_id = @{ claimed_model_name = "External $Provider"; claimed_version = $null; training_cutoff = $null; self_assessed_strengths_for_this_review = @('s'); self_assessed_limitations_for_this_review = @('l'); estimated_context_window_kb = $null; uses_extended_thinking_or_reasoning_mode = $false }
        provider = $Provider; role = $Role
        target_iteration_id = $CarrierIid; epoch_id = $EpochId
        source_evidence_sha256 = ('e' * 64)
        reviewer_summary = 'syn'
        verdict = 'pass'
        findings = @()
        suggested_next_actions = @(
            @{ id = 'P1'; title = "$Provider/$Role proposal"; rationale = 'r'; approach = 'a'; estimated_effort = 'small'; risks = 'rk'; expected_payoff = 'p' }
        )
        confidence = 'medium'; limitations = 'l'; completed = $true
    }
    $jsonOut = Join-Path $imp ($importId + '.json')
    Set-Content -Path $jsonOut -Value (([pscustomobject]$obj) | ConvertTo-Json -Depth 10) -Encoding UTF8
    $meta = [ordered]@{
        import_id = $importId; ok = $true; provider = $Provider; role = $Role
        epoch_id = $EpochId; target_iteration_id = $CarrierIid
        json_path = $jsonOut
        md_path = (Join-Path $imp ($importId + '.md'))
        sha_verified = $true
    }
    Set-Content -Path (Join-Path $imp ($importId + '.metadata.json')) -Value (([pscustomobject]$meta) | ConvertTo-Json -Depth 10) -Encoding UTF8
    Set-Content -Path (Join-Path $imp ($importId + '.md')) -Value '# stub' -Encoding UTF8
}

function Write-Synthetic-RegressionLedger {
    param([string] $Root)
    $obj = @{ regressions = @( @{ id = 'REG-2026-05-07-001'; status = 'open'; severity = 'medium'; score = 50; linked_findings = @('ARC-001') } ) }
    Set-Content -Path (Join-Path $Root 'state/regression_ledger.json') -Value (([pscustomobject]$obj) | ConvertTo-Json -Depth 6) -Encoding UTF8
}

# ---- Test 1: empty -------------------------------------------------------

$proj = New-FakeProject -Name 'pmx-empty'
$iid = '2026-05-07_e1'
[void](Add-FakeIteration -Root $proj.root -Id $iid -WithProposals $false)
$r = Build-WaggleProposalMatrix -ConfigPath $proj.cfg -EpochId 'e1' -IterationId $iid -IncludeCodex $false -IncludeExternal $false
Assert-True 'empty: ok=true' ($r.ok -eq $true)
Assert-True 'empty: row_count=0' ($r.row_count -eq 0)
Assert-True 'empty: json exists' (Test-Path -LiteralPath $r.json_path)
Assert-True 'empty: md exists'   (Test-Path -LiteralPath $r.md_path)
$mtxJson = Get-Content -Raw -Path $r.json_path -Encoding UTF8 | ConvertFrom-Json
Assert-True 'empty: format_version=1.0' ($mtxJson.format_version -eq '1.0')
Assert-True 'empty: total_proposals=0'  ($mtxJson.sources_summary.total_proposals -eq 0)
$mtxMd = Get-Content -Raw -Path $r.md_path -Encoding UTF8
Assert-True 'empty: md mentions no-proposal note' ($mtxMd -match '_No proposals')

# ---- Test 2: internal only -----------------------------------------------

$proj2 = New-FakeProject -Name 'pmx-internal'
$iid2 = '2026-05-07_e2'
[void](Add-FakeIteration -Root $proj2.root -Id $iid2 -WithProposals $true)
$r = Build-WaggleProposalMatrix -ConfigPath $proj2.cfg -EpochId 'e2' -IterationId $iid2 -IncludeCodex $false -IncludeExternal $false
Assert-True 'internal: ok=true'                   ($r.ok -eq $true)
Assert-True 'internal: 6 rows (3 roles x 2)'      ($r.row_count -eq 6)
Assert-True 'internal: 6 internal in summary'     ($r.sources_summary.claude_internal_count -eq 6)
Assert-True 'internal: 0 codex'                   ($r.sources_summary.codex_count -eq 0)
Assert-True 'internal: 0 external'                ($r.sources_summary.external_count -eq 0)
$mtxJson = Get-Content -Raw -Path $r.json_path -Encoding UTF8 | ConvertFrom-Json
Assert-True 'internal: every row has id'          (@($mtxJson.rows | Where-Object { $_.id }).Count -eq 6)
Assert-True 'internal: every row has source_kind=claude_internal' (@($mtxJson.rows | Where-Object { $_.source_kind -eq 'claude_internal' }).Count -eq 6)
Assert-True 'internal: every row has source_provider=claude_code' (@($mtxJson.rows | Where-Object { $_.source_provider -eq 'claude_code' }).Count -eq 6)
Assert-True 'internal: every row has matrix_status=candidate'      (@($mtxJson.rows | Where-Object { $_.matrix_status -eq 'candidate' }).Count -eq 6)
Assert-True 'internal: at least one row has category=test_coverage' (@($mtxJson.rows | Where-Object { $_.category -eq 'test_coverage' }).Count -ge 1)
Assert-True 'internal: ARCH-001 ledger tag linked from text match'  (@($mtxJson.rows | Where-Object { $_.linked_ledger_tags -contains 'ARCH-001' }).Count -ge 1)

# ---- Test 3: internal + codex --------------------------------------------

$proj3 = New-FakeProject -Name 'pmx-codex'
$iid3 = '2026-05-07_e3'
[void](Add-FakeIteration -Root $proj3.root -Id $iid3 -WithProposals $true)
Write-Synthetic-Codex -Root $proj3.root -CarrierIid $iid3 -EpochId 'e3'
$r = Build-WaggleProposalMatrix -ConfigPath $proj3.cfg -EpochId 'e3' -IterationId $iid3 -IncludeCodex $true -IncludeExternal $false
Assert-True 'codex: codex_count=1' ($r.sources_summary.codex_count -eq 1)
Assert-True 'codex: row_count=7'   ($r.row_count -eq 7)
$mtxJson = Get-Content -Raw -Path $r.json_path -Encoding UTF8 | ConvertFrom-Json
Assert-True 'codex: PM-CDEX prefix present' (@($mtxJson.rows | Where-Object { $_.id -match '^PM-CDEX-' }).Count -eq 1)
Assert-True 'codex: codex row has source_role=scout' (@($mtxJson.rows | Where-Object { $_.source_kind -eq 'codex' -and $_.source_role -eq 'scout' }).Count -eq 1)

# ---- Test 4: internal + codex + external ---------------------------------

$proj4 = New-FakeProject -Name 'pmx-all'
$iid4 = '2026-05-07_e4'
[void](Add-FakeIteration -Root $proj4.root -Id $iid4 -WithProposals $true)
Write-Synthetic-Codex -Root $proj4.root -CarrierIid $iid4 -EpochId 'e4'
Write-Synthetic-ExternalImport -Root $proj4.root -CarrierIid $iid4 -EpochId 'e4' -Provider 'gemini' -Role 'architect'
Write-Synthetic-ExternalImport -Root $proj4.root -CarrierIid $iid4 -EpochId 'e4' -Provider 'grok' -Role 'reliability'
$r = Build-WaggleProposalMatrix -ConfigPath $proj4.cfg -EpochId 'e4' -IterationId $iid4 -IncludeCodex $true -IncludeExternal $true
Assert-True 'all: external_count=2' ($r.sources_summary.external_count -eq 2)
Assert-True 'all: codex_count=1'    ($r.sources_summary.codex_count -eq 1)
Assert-True 'all: internal_count=6' ($r.sources_summary.claude_internal_count -eq 6)
Assert-True 'all: row_count=9'      ($r.row_count -eq 9)
$mtxMd = Get-Content -Raw -Path $r.md_path -Encoding UTF8
Assert-True 'all: md groups by category'    ($mtxMd -match '## architecture\b|## reliability\b|## security\b|## test_coverage\b')

# ---- Test 5: linked_regressions populates from finding-id match ----------

$proj5 = New-FakeProject -Name 'pmx-reg'
$iid5 = '2026-05-07_e5'
[void](Add-FakeIteration -Root $proj5.root -Id $iid5 -WithProposals $true)
Write-Synthetic-RegressionLedger -Root $proj5.root
$r = Build-WaggleProposalMatrix -ConfigPath $proj5.cfg -EpochId 'e5' -IterationId $iid5 -IncludeCodex $false -IncludeExternal $false
$mtxJson = Get-Content -Raw -Path $r.json_path -Encoding UTF8 | ConvertFrom-Json
# The architect proposals' findings_in_source includes "ARC-001"
# (synthesized in Add-FakeIteration). The regression ledger fixture
# has linked_findings = @('ARC-001'). Therefore at least one matrix
# row should have linked_regressions = @('REG-2026-05-07-001').
Assert-True 'reg: linked_regressions populates from finding match' (@($mtxJson.rows | Where-Object { $_.linked_regressions -contains 'REG-2026-05-07-001' }).Count -ge 1)

# ---- Cleanup -------------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
