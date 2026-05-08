#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (ARCH-011) tests for
    orchestrator/Build-WaggleCockpitData.ps1 and the cockpit HTML.

    Cases:
      - Build-WaggleCockpitData with synthetic queue + no imports
        produces a well-formed JSON with status='pending' bundles
      - Build-WaggleCockpitData with one imported response shows
        that bundle as 'imported'
      - Build-WaggleCockpitData with disabled provider shows
        'disabled' status
      - Build-WaggleCockpitData attaches regression-ledger summary
        when state/regression_ledger.json exists
      - Build-WaggleCockpitData attaches proposal-matrix summary
        when proposal_matrix.json exists in the synth dir
      - cockpit_data.json validates against
        schemas/cockpit_data.schema.json (shape sanity)
      - review_cockpit.html parses + has the expected element IDs
      - review_cockpit.html only references whitelisted external
        origins (gemini.google.com / grok.com / chatgpt.com /
        claude.ai). No API endpoints, no analytics.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'Build-WaggleCockpitData.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$tmp = Join-Path $env:TEMP ("waggle-test-cd-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

function New-FakeProject {
    param([string] $Name, [bool] $DisableGemini = $false)
    $root = Join-Path $tmp $Name
    [void](New-Item -ItemType Directory -Path $root -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'state') -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $root 'iterations') -Force)
    $cfg = @{
        projectRoot = $root; iterationsDir = 'iterations'
        external_review = @{
            queue_dir_relative = 'external_reviews/queue'
            imported_dir_relative = 'external_reviews/imported'
            synthesis_dir_relative = 'external_reviews/synthesis'
            providers = @{
                gemini = @{ enabled = (-not $DisableGemini); timeout_sec = 600; expected_model_in_ui = 'Gemini Pro Advanced' }
                grok   = @{ enabled = $true; timeout_sec = 900; expected_model_in_ui = 'Grok Expert mode' }
            }
        }
    }
    $cfgPath = Join-Path $root 'orchestrator.config.json'
    Set-Content -Path $cfgPath -Value ($cfg | ConvertTo-Json -Depth 10) -Encoding UTF8
    return [pscustomobject]@{ root = $root; cfg = $cfgPath }
}

function New-FakeQueueBundle {
    param([string] $Root, [string] $IterationId, [string] $EpochId, [string] $Provider, [string] $Role, [string] $Sha)
    $bundleDir = Join-Path (Join-Path (Join-Path (Join-Path $Root 'iterations') $IterationId) ('external_reviews/queue/' + $EpochId)) ($Provider + '_' + $Role)
    [void](New-Item -ItemType Directory -Path $bundleDir -Force)
    [void](New-Item -ItemType Directory -Path (Join-Path $bundleDir 'attachments') -Force)
    Set-Content -Path (Join-Path $bundleDir 'prompt.md') -Value '# prompt' -Encoding UTF8
    Set-Content -Path (Join-Path $bundleDir 'expected_response_path.txt') -Value (Join-Path $bundleDir 'response.md') -Encoding UTF8
    Set-Content -Path (Join-Path $bundleDir 'metadata.json') -Value (@{
        provider = $Provider; role = $Role
        provider_profile = @{ enabled = $true; expected_model_in_ui = ('Provider ' + $Provider); timeout_sec = 600 }
    } | ConvertTo-Json -Depth 5) -Encoding UTF8
    1..3 | ForEach-Object {
        Set-Content -Path (Join-Path (Join-Path $bundleDir 'attachments') ('att' + $_ + '.txt')) -Value 'x' -Encoding UTF8
    }
    # Also seed the epoch_evidence.json so cockpit can read evidence_sha256.
    $evDir = Join-Path (Join-Path (Join-Path $Root 'iterations') $IterationId) ('external_reviews/epoch_' + $EpochId + '/evidence')
    [void](New-Item -ItemType Directory -Path $evDir -Force)
    Set-Content -Path (Join-Path $evDir 'epoch_evidence.json') -Value (@{ evidence_sha256 = $Sha } | ConvertTo-Json) -Encoding UTF8
}

function New-FakeImport {
    param([string] $Root, [string] $IterationId, [string] $EpochId, [string] $Provider, [string] $Role)
    $imp = Join-Path (Join-Path (Join-Path $Root 'iterations') $IterationId) 'external_reviews/imported'
    [void](New-Item -ItemType Directory -Path $imp -Force)
    $importId = ((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH-mm-ssZ') + '_' + $Provider + '_' + $Role + '_xxxxxxxx')
    Set-Content -Path (Join-Path $imp ($importId + '.metadata.json')) -Value (@{
        import_id = $importId; ok = $true; provider = $Provider; role = $Role
        epoch_id = $EpochId; target_iteration_id = $IterationId
        applied_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json -Depth 5) -Encoding UTF8
}

function New-FakeRegLedger {
    param([string] $Root)
    Set-Content -Path (Join-Path $Root 'state/regression_ledger.json') -Value (@{
        regressions = @(
            @{ id = 'REG-1'; severity = 'critical'; status = 'open' },
            @{ id = 'REG-2'; severity = 'high'; status = 'open' },
            @{ id = 'REG-3'; severity = 'medium'; status = 'verified' }
        )
    } | ConvertTo-Json -Depth 5) -Encoding UTF8
}

function New-FakeProposalMatrix {
    param([string] $Root, [string] $IterationId, [string] $EpochId)
    $synth = Join-Path (Join-Path (Join-Path $Root 'iterations') $IterationId) ('external_reviews/synthesis/' + $EpochId)
    [void](New-Item -ItemType Directory -Path $synth -Force)
    Set-Content -Path (Join-Path $synth 'proposal_matrix.json') -Value (@{
        sources_summary = @{ total_proposals = 7; claude_internal_count = 4; codex_count = 1; external_count = 2 }
    } | ConvertTo-Json -Depth 5) -Encoding UTF8
}

# ---- Test 1: pending bundles -------------------------------------------

$proj = New-FakeProject -Name 'cd1'
$iid = '2026-05-07_e1'
$ev = ('a' * 64)
New-FakeQueueBundle -Root $proj.root -IterationId $iid -EpochId 'e1' -Provider 'gemini' -Role 'architect' -Sha $ev
New-FakeQueueBundle -Root $proj.root -IterationId $iid -EpochId 'e1' -Provider 'grok'   -Role 'reliability' -Sha $ev
$out = Join-Path $proj.root 'state/cockpit_data.json'
$r = Build-WaggleCockpitData -ConfigPath $proj.cfg -EpochId 'e1' -IterationId $iid -OutputPath $out
Assert-True 'pending: ok' ($r.ok -eq $true)
Assert-True 'pending: file exists' (Test-Path -LiteralPath $out)
$d = Get-Content -Raw -Path $out -Encoding UTF8 | ConvertFrom-Json
Assert-True 'pending: format_version=1.0' ($d.format_version -eq '1.0')
Assert-True 'pending: bundle count=2' (@($d.bundles).Count -eq 2)
Assert-True 'pending: bundles all status=pending' (@($d.bundles | Where-Object { $_.status -eq 'pending' }).Count -eq 2)
Assert-True 'pending: evidence_sha256 captured' ($d.evidence_sha256 -eq $ev)
# Phase 2B-R1 fix: prompt_text must be a plain string (not a
# PSObject wrapping with PSPath/PSDrive/value sub-fields).
foreach ($pb in @($d.bundles)) {
    Assert-True ("pending: bundle " + $pb.provider + "/" + $pb.role + " prompt_text is string") ($pb.prompt_text -is [string])
}
# Phase 2B-R1 fix: provider/role must split on the LAST underscore
# so claude_web_architect parses as (claude_web, architect). The
# fixture above uses gemini/architect + grok/reliability so we
# can assert the expected pair shape directly.
Assert-True 'pending: gemini/architect bundle present' (@($d.bundles | Where-Object { $_.provider -eq 'gemini' -and $_.role -eq 'architect' }).Count -eq 1)
Assert-True 'pending: grok/reliability bundle present' (@($d.bundles | Where-Object { $_.provider -eq 'grok' -and $_.role -eq 'reliability' }).Count -eq 1)

# ---- Test 1b: claude_web_architect (3-segment name; legacy opt-in) ----
# Phase 2B-R1: bundle name has provider with an underscore inside
# (claude_web). The parser must split on the LAST underscore so
# the role is the trailing single segment.
$proj1b = New-FakeProject -Name 'cd1b'
$iid1b = '2026-05-07_e1b'
$ev1b = ('c' * 64)
New-FakeQueueBundle -Root $proj1b.root -IterationId $iid1b -EpochId 'e1b' -Provider 'claude_web' -Role 'architect' -Sha $ev1b
$out1b = Join-Path $proj1b.root 'state/cockpit_data.json'
$r1b = Build-WaggleCockpitData -ConfigPath $proj1b.cfg -EpochId 'e1b' -IterationId $iid1b -OutputPath $out1b
$d1b = Get-Content -Raw -Path $out1b -Encoding UTF8 | ConvertFrom-Json
$cwBundle = @($d1b.bundles)[0]
Assert-True 'cw3seg: provider=claude_web (split on LAST underscore)' ($cwBundle.provider -eq 'claude_web')
Assert-True 'cw3seg: role=architect'                                   ($cwBundle.role -eq 'architect')

# ---- Test 2: one imported -----------------------------------------------

$proj2 = New-FakeProject -Name 'cd2'
$iid2 = '2026-05-07_e2'
$ev2 = ('b' * 64)
New-FakeQueueBundle -Root $proj2.root -IterationId $iid2 -EpochId 'e2' -Provider 'gemini' -Role 'architect' -Sha $ev2
New-FakeQueueBundle -Root $proj2.root -IterationId $iid2 -EpochId 'e2' -Provider 'grok'   -Role 'reliability' -Sha $ev2
New-FakeImport -Root $proj2.root -IterationId $iid2 -EpochId 'e2' -Provider 'gemini' -Role 'architect'
$r = Build-WaggleCockpitData -ConfigPath $proj2.cfg -EpochId 'e2' -IterationId $iid2
$d = Get-Content -Raw -Path (Join-Path $proj2.root 'state/cockpit_data.json') -Encoding UTF8 | ConvertFrom-Json
$gemBundle = @($d.bundles | Where-Object { $_.provider -eq 'gemini' })[0]
$grokBundle = @($d.bundles | Where-Object { $_.provider -eq 'grok' })[0]
Assert-True 'one-imported: gemini status=imported' ($gemBundle.status -eq 'imported')
Assert-True 'one-imported: gemini import_id populated' ([string]$gemBundle.import_id -ne '')
Assert-True 'one-imported: grok status=pending'    ($grokBundle.status -eq 'pending')

# ---- Test 3: disabled provider -----------------------------------------

$proj3 = New-FakeProject -Name 'cd3' -DisableGemini $true
$iid3 = '2026-05-07_e3'
$ev3 = ('c' * 64)
New-FakeQueueBundle -Root $proj3.root -IterationId $iid3 -EpochId 'e3' -Provider 'gemini' -Role 'architect' -Sha $ev3
New-FakeQueueBundle -Root $proj3.root -IterationId $iid3 -EpochId 'e3' -Provider 'grok'   -Role 'reliability' -Sha $ev3
$r = Build-WaggleCockpitData -ConfigPath $proj3.cfg -EpochId 'e3' -IterationId $iid3
$d = Get-Content -Raw -Path (Join-Path $proj3.root 'state/cockpit_data.json') -Encoding UTF8 | ConvertFrom-Json
$gemBundle = @($d.bundles | Where-Object { $_.provider -eq 'gemini' })[0]
Assert-True 'disabled: gemini status=disabled' ($gemBundle.status -eq 'disabled')

# ---- Test 4: regression-ledger + proposal-matrix summaries -------------

$proj4 = New-FakeProject -Name 'cd4'
$iid4 = '2026-05-07_e4'
$ev4 = ('d' * 64)
New-FakeQueueBundle -Root $proj4.root -IterationId $iid4 -EpochId 'e4' -Provider 'gemini' -Role 'architect' -Sha $ev4
New-FakeRegLedger -Root $proj4.root
New-FakeProposalMatrix -Root $proj4.root -IterationId $iid4 -EpochId 'e4'
$r = Build-WaggleCockpitData -ConfigPath $proj4.cfg -EpochId 'e4' -IterationId $iid4
$d = Get-Content -Raw -Path (Join-Path $proj4.root 'state/cockpit_data.json') -Encoding UTF8 | ConvertFrom-Json
Assert-True 'reg-summary: open_critical=1' ($d.regression_ledger.open_critical -eq 1)
Assert-True 'reg-summary: open_high=1'     ($d.regression_ledger.open_high -eq 1)
Assert-True 'reg-summary: open_medium=0'   ($d.regression_ledger.open_medium -eq 0)
Assert-True 'pm-summary: total=7'          ($d.proposal_matrix.total -eq 7)
Assert-True 'pm-summary: internal=4'       ($d.proposal_matrix.claude_internal_count -eq 4)

# ---- Test 5: cockpit HTML sanity --------------------------------------

$repoRoot = Split-Path -Parent $PSScriptRoot
# Phase 2B-R2 (ARCH-005): the cockpit HTML moved to
# orchestrator/cockpit/. The old repo-root path is the legacy
# fallback honoured by Open-WaggleCockpit.ps1 only.
$cockpitFile = Join-Path $repoRoot 'orchestrator/cockpit/review_cockpit.html'
$legacyCockpitFile = Join-Path $repoRoot 'review_cockpit.html'
Assert-True 'html: cockpit file exists at orchestrator/cockpit/' (Test-Path -LiteralPath $cockpitFile)
Assert-True 'html: legacy repo-root cockpit removed' (-not (Test-Path -LiteralPath $legacyCockpitFile))
Assert-True 'html: orchestrator/cockpit/README.md exists' (Test-Path -LiteralPath (Join-Path $repoRoot 'orchestrator/cockpit/README.md'))
$html = Get-Content -Raw -Path $cockpitFile -Encoding UTF8
Assert-True 'html: has element id="cards"'     ($html -match 'id="cards"')
Assert-True 'html: has element id="summary"'   ($html -match 'id="summary"')
Assert-True 'html: has element id="meta"'      ($html -match 'id="meta"')
Assert-True 'html: fetches state/cockpit_data.json' ($html -match "fetch\(COCKPIT_DATA_URL" -or $html -match "fetch\('\.\./\.\./state/cockpit_data.json'" -or $html -match "fetch\('state/cockpit_data.json'")
# Phase 2B-R3: cockpit is at orchestrator/cockpit/ now (ARCH-005,
# Phase 2BR2). The fetch URL must be a relative path that, resolved
# from the cockpit HTML's location, lands on the canonical
# state/cockpit_data.json at repo root. Verify the resolved URL by
# rebuilding the path the way the browser would.
$cockpitDirAbs = Split-Path -Parent $cockpitFile
$urlMatch = [regex]::Match($html, "var\s+COCKPIT_DATA_URL\s*=\s*'([^']+)'")
Assert-True 'html: COCKPIT_DATA_URL constant declared' $urlMatch.Success
if ($urlMatch.Success) {
    $relUrl = $urlMatch.Groups[1].Value
    $resolved = [System.IO.Path]::GetFullPath((Join-Path $cockpitDirAbs ($relUrl -replace '/', [System.IO.Path]::DirectorySeparatorChar)))
    $expected = [System.IO.Path]::GetFullPath((Join-Path $repoRoot 'state/cockpit_data.json'))
    Assert-True ('html: COCKPIT_DATA_URL resolves to repo-root state/cockpit_data.json (got ' + $relUrl + ')') ($resolved -ieq $expected)
}
Assert-True 'html: uses navigator.clipboard.writeText' ($html -match 'navigator\.clipboard\.writeText')
# Phase 2B-R3: when cockpit is served over http:// the file:// link
# for "Open attachments folder" is silently blocked by browsers.
# A clipboard fallback button must exist so the operator can paste
# the path into Explorer / terminal manually.
Assert-True 'html: copy-folder button exists' ($html -match 'data-act="copy-folder"')
Assert-True 'html: copy-folder writes attachments_dir to clipboard' ($html -match "writeText\(b\.attachments_dir\)")

# Phase 2B-R3 P10 (Codex ARCH-003 fix): canonical provider origin
# allowlist is in the PROVIDER_URLS constant. Header comment and
# footer text must agree with it.
$canonicalHosts = @('gemini.google.com','grok.com','chatgpt.com','claude.ai')
# NOTE: $host is a read-only PS automatic variable; use $hostName.
foreach ($hostName in $canonicalHosts) {
    Assert-True ("html: provider host '" + $hostName + "' present (allowlist consistency)") (
        $html -match [regex]::Escape($hostName)
    )
}
# Stale alternative URL from a removed header entry must be gone.
Assert-True 'html: stale x.com/i/grok header entry removed' (
    $html -notmatch 'x\.com/i/grok'
)
Assert-True 'html: 5s polling interval'         ($html -match 'setInterval\(loadAndRender,\s*5000\)')

# Whitelist check: the only external https:// origins the cockpit
# explicitly references. We extract every https:// URL and assert
# the host is in the whitelist.
$urlMatches = [regex]::Matches($html, 'https://([A-Za-z0-9.\-]+)/?')
$allowedHosts = @('gemini.google.com','grok.com','chatgpt.com','claude.ai','x.com','json-schema.org','waggledance.local')
$bad = New-Object System.Collections.Generic.List[string]
foreach ($m in $urlMatches) {
    $hostName = $m.Groups[1].Value.ToLowerInvariant()
    if ($allowedHosts -notcontains $hostName) { $bad.Add($hostName) | Out-Null }
}
Assert-True 'html: only whitelisted https hosts referenced' ($bad.Count -eq 0) ('bad hosts: ' + ($bad -join ', '))
Assert-True 'html: no api endpoint reference (api., /v1/)' ($html -notmatch '://api\.|/v1/')

# ---- Cleanup -----------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
