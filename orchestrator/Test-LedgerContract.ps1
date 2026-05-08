#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-R3 (PM-CL-002): hardening gate that converts the
    dual-ledger contract at docs/design/ledger_contract.md from
    a markdown-only document into an enforced invariant.

    The contract:
      - phase_fix_ledger.json is committed at docs/design/phase_fix_ledger.json
        (PR-visible historical record).
      - state/regression_ledger.json is the LIVE RUNTIME state machine and
        is gitignored. No committed file outside a small allowlist may
        contain regression_ledger contents.
      - docs/design/ledger_contract.md exists and references both ledgers
        by name.
      - Build-WaggleProposalMatrix.ps1 tolerates absence of
        state/regression_ledger.json (clean clone, never run).

    A finding here means the dual-ledger contract has drifted. The
    fix is either to restore the documented invariant (move the file
    back, gitignore it, etc.) or to amend the allowlist below
    explicitly so the addition is reviewable.

    Authoritative source: docs/design/ledger_contract.md.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string]$Name, [bool]$Cond, [string]$Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    # ---- 1. Committed-side invariants ----------------------------------

    $committedFiles = @(& git ls-files 2>$null)
    Assert-True 'git ls-files works (we are inside a git checkout)' ($committedFiles.Count -gt 0)

    Assert-True 'phase_fix_ledger.json is committed at docs/design/' (
        $committedFiles -contains 'docs/design/phase_fix_ledger.json'
    )
    Assert-True 'phase_fix_ledger.md is committed alongside the JSON' (
        $committedFiles -contains 'docs/design/phase_fix_ledger.md'
    )
    Assert-True 'ledger_contract.md is committed at docs/design/' (
        $committedFiles -contains 'docs/design/ledger_contract.md'
    )
    Assert-True 'regression_ledger.schema.json is committed under schemas/' (
        $committedFiles -contains 'schemas/regression_ledger.schema.json'
    )

    # ---- 2. Runtime-ledger writer-audience invariant -------------------
    #
    # Only the documented allowlist may have 'regression_ledger' in its
    # path AND be committed. Anything else is a writer-audience drift:
    # someone committed runtime ledger output, or named a new committed
    # file with the runtime ledger's name. Either is wrong; either gets
    # caught here.
    # Phase 2B-R3 P4b (architect ARCH-002): two clearly-named arrays
    # rather than a sentinel-prefixed mixed array.
    $regressionAllowlistExact = @(
        'docs/design/ledger_contract.md',
        'docs/quality/regression_ledger.md',
        'orchestrator/Test-RegressionLedger.ps1',
        'orchestrator/Test-LedgerContract.ps1',
        'orchestrator/lib/RegressionLedger.ps1',
        'schemas/regression_ledger.schema.json'
    )
    # docs/runs/ are evidence files describing past phases that
    # surfaced the ledger. Allowed by directory prefix to avoid
    # editing this allowlist for every new phase run.
    $regressionAllowlistPrefixes = @(
        'docs/runs/'
    )

    # Phase 2B-R3 P4b (architect ARCH-005): explicit presence
    # assertion for the runtime-ledger validator the allowlist
    # references. Surfaces drift if the validator is removed.
    Assert-True 'Test-RegressionLedger.ps1 is committed alongside this contract gate' (
        $committedFiles -contains 'orchestrator/Test-RegressionLedger.ps1'
    )

    $unexpected = New-Object System.Collections.Generic.List[string]
    foreach ($f in $committedFiles) {
        if ($f -match 'regression_ledger') {
            if ($regressionAllowlistExact -contains $f) { continue }
            $matched = $false
            # Phase 2B-R3 P4b (reliability REL-003): the allowlist values
            # are committed lower-case; git ls-files paths are case-
            # sensitive on Linux. Use Ordinal (case-sensitive) so a
            # capitalised drift like Docs/Runs/.../Regression_Ledger.json
            # surfaces here rather than silently passing.
            foreach ($d in $regressionAllowlistPrefixes) { if ($f.StartsWith($d, [StringComparison]::Ordinal)) { $matched = $true; break } }
            if (-not $matched) { $unexpected.Add($f) | Out-Null }
        }
    }
    Assert-True 'no unexpected committed regression_ledger paths' ($unexpected.Count -eq 0) ("unexpected: " + ($unexpected -join ', '))

    # The CANONICAL runtime location MUST itself be uncommitted. If the
    # current checkout has state/regression_ledger.json on disk that is
    # fine (it gets created at runtime); what matters is that it is NOT
    # tracked by git. The check below is robust whether the file exists
    # locally or not.
    $stateLedgerCommitted = ($committedFiles -contains 'state/regression_ledger.json')
    Assert-True 'state/regression_ledger.json is NOT committed (runtime-only)' (-not $stateLedgerCommitted)

    # The same holds for any iterations/-rooted regression-ledger copy:
    # iterations/ is a runtime tree gitignored at the repo level. A
    # check here is defence-in-depth in case .git/info/exclude drifts.
    # Phase 2B-R3 P4b (architect ARCH-004): the wildcard is intentionally
    # broad (iterations/**/*regression_ledger*.json) to catch nested
    # leaks; the assertion title now matches that behaviour.
    $iterLedgerCommitted = @($committedFiles | Where-Object { $_ -like 'iterations/*regression_ledger*.json' })
    Assert-True 'no iterations/**/regression_ledger*.json committed (defence-in-depth vs runtime leak)' ($iterLedgerCommitted.Count -eq 0) ("found: " + ($iterLedgerCommitted -join ', '))

    # ---- 3. Contract doc references both ledgers ----------------------

    $contractPath = Join-Path $repoRoot 'docs/design/ledger_contract.md'
    Assert-True 'ledger_contract.md exists on disk' (Test-Path -LiteralPath $contractPath)
    if (Test-Path -LiteralPath $contractPath) {
        $contract = Get-Content -Raw -Path $contractPath -Encoding UTF8
        Assert-True 'ledger_contract.md references phase_fix_ledger' (
            $contract -match 'phase_fix_ledger'
        )
        Assert-True 'ledger_contract.md references regression_ledger' (
            $contract -match 'regression_ledger'
        )
        # Phase 2B-R3 P4d (iterate-2 architect ARCH-002): replace lossy
        # regex-window checks with anchored literal-substring matches on
        # phrases the contract author commits to keeping verbatim. Less
        # drift, less false-confidence; if the contract is reworded the
        # test fails loudly and the author updates these strings or
        # restores the wording. Anchors live as constants in this test
        # file so the test is the source of truth for the contract's
        # required literal phrases.
        $Script:RequiredContractPhrases = @(
            'Committed historical record',
            'live runtime state machine'
        )
        foreach ($phrase in $Script:RequiredContractPhrases) {
            Assert-True ("ledger_contract.md contains anchor phrase: '" + $phrase + "'") (
                $contract.IndexOf($phrase, [StringComparison]::OrdinalIgnoreCase) -ge 0
            )
        }

        # Phase 2B-R3 P10 (Codex ARCH-001 fix): the Codex dual-engine
        # simulation found the contract was materially stale vs the
        # actual schema and helper names. The Codex finding (ARCH-001
        # in iterations/.../codex/architect_codex_review.json) listed
        # two helpers and an entry-field set that did not exist. Lock
        # the corrected names + canonical schema fields here so a
        # future drift fails the gate loudly.
        $Script:RequiredContractRuntimeHelpers = @(
            'Get-WaggleRegressionLedger',
            'Save-WaggleRegressionLedger',
            'Add-WaggleRegressionEntry',
            'Update-WaggleRegressionEntry'
        )
        foreach ($helper in $Script:RequiredContractRuntimeHelpers) {
            Assert-True ("ledger_contract.md names runtime helper: '" + $helper + "'") (
                $contract.IndexOf($helper, [StringComparison]::Ordinal) -ge 0
            )
        }
        # Phase 2B-R3 P10b (GPT-5.5 Pro review): instead of hard-coding
        # which fields the contract doc must mention, parse the actual
        # schema and assert the contract names every truly-required
        # field. Drift in either direction (schema gains a required
        # field, doc forgets to list it) fails the gate. Optional
        # fields are NOT required to appear in the doc.
        $schemaPath = Join-Path $repoRoot 'schemas/regression_ledger.schema.json'
        Assert-True 'regression_ledger schema file is parseable' (Test-Path -LiteralPath $schemaPath)
        if (Test-Path -LiteralPath $schemaPath) {
            try {
                $schema = Get-Content -Raw -Path $schemaPath -Encoding UTF8 | ConvertFrom-Json
                $entrySchema = $schema.properties.regressions.items
                $schemaRequired = @($entrySchema.required)
                Assert-True ('schema declares >=5 required entry fields (got ' + $schemaRequired.Count + ')') ($schemaRequired.Count -ge 5)
                foreach ($field in $schemaRequired) {
                    Assert-True ("ledger_contract.md names schema-required entry field: '" + $field + "'") (
                        $contract.IndexOf($field, [StringComparison]::Ordinal) -ge 0
                    )
                }
                # Phase 2B-R3 P10d (Codex post-fix ARCH-003 fix): a
                # canonical-field floor that prevents a hypothetical
                # schema relaxation from being silently accepted by
                # the dynamic schema-required loop above. These four
                # fields ARE the load-bearing identity contract for a
                # regression entry and must remain required for as
                # long as the contract calls them out.
                $Script:CanonicalRequiredFields = @(
                    'severity',
                    'score',
                    'category',
                    'history'
                )
                foreach ($canonical in $Script:CanonicalRequiredFields) {
                    Assert-True ("schema canonical floor: '" + $canonical + "' is in required[]") (
                        $schemaRequired -contains $canonical
                    )
                }
            } catch {
                Assert-True ('regression_ledger schema parse: exception=' + $_.Exception.Message) $false
            }
        }
        # Names that MUST NOT appear (they were the stale ones).
        $Script:ForbiddenContractStaleNames = @(
            'Ensure-WaggleRegressionLedger',
            'Set-WaggleRegressionState'
        )
        foreach ($stale in $Script:ForbiddenContractStaleNames) {
            Assert-True ("ledger_contract.md no longer references stale name: '" + $stale + "'") (
                $contract.IndexOf($stale, [StringComparison]::Ordinal) -lt 0
            )
        }
        # Phase 2B-R3 P10d (Claude post-fix ARCH-001 fix): ledger_contract.md
        # MUST point at this gate file (Test-LedgerContract.ps1) as the
        # canonical source of the regression_ledger allowlist, instead of
        # repeating the list and creating dual-truth drift.
        Assert-True 'ledger_contract.md names Test-LedgerContract.ps1 as the canonical allowlist source' (
            $contract.IndexOf('Test-LedgerContract.ps1', [StringComparison]::Ordinal) -ge 0
        )
        Assert-True "ledger_contract.md says the allowlist 'lives in code' (anti-duplicate-truth)" (
            ($contract.IndexOf('lives in code', [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
            ($contract.IndexOf('canonical truth', [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
            ($contract.IndexOf('canonical source', [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
            ($contract.IndexOf('canonical allowlist', [StringComparison]::OrdinalIgnoreCase) -ge 0)
        )
    }

    # ---- 4. Build-WaggleProposalMatrix tolerates missing runtime ledger

    # Phase 2B-R3 (PM-CL-002): the contract requires the matrix builder
    # to populate `linked_ledger_tags` from the always-present
    # phase_fix_ledger.json AND tolerate `linked_regressions` being
    # empty when state/regression_ledger.json is absent. Run the
    # builder under a synthetic config whose stateDir points at an
    # empty temp directory and assert no throw.
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("waggle-ledger-contract-{0}" -f ([guid]::NewGuid().ToString('N')))
    [void](New-Item -ItemType Directory -Path $tmp -Force)
    try {
        $emptyState  = Join-Path $tmp 'state'
        $emptyIters  = Join-Path $tmp 'iterations'
        [void](New-Item -ItemType Directory -Path $emptyState -Force)
        [void](New-Item -ItemType Directory -Path $emptyIters -Force)
        # Plant a minimal iteration directory the builder will inspect.
        $itid = '2026-05-08_ledger_contract_smoke'
        $iterDir = Join-Path $emptyIters $itid
        [void](New-Item -ItemType Directory -Path (Join-Path $iterDir 'reviews') -Force)
        # Empty iteration tree on purpose: no review JSONs, no codex
        # findings, no external review imports. The matrix should still
        # build (with zero rows) and MUST NOT throw on missing
        # state/regression_ledger.json.
        $cfg = [pscustomobject]@{
            projectRoot   = $tmp
            iterationsDir = 'iterations'
            stateDir      = 'state'
        }
        $cfgPath = Join-Path $tmp 'orchestrator.config.json'
        $cfg | ConvertTo-Json | Set-Content -Path $cfgPath -Encoding UTF8

        $builderPath = Join-Path $repoRoot 'orchestrator/Build-WaggleProposalMatrix.ps1'
        # Phase 2B-R3 P4d (iterate-2 architect ARCH-001): dot-source
        # the builder in-process inside an isolated scriptblock so we
        # get proper exception semantics + no child-process spawn.
        # The CLI wrapper at the bottom of Build-WaggleProposalMatrix.ps1
        # only fires when invoked as a script with all params present
        # (see `if ($MyInvocation.InvocationName -ne '.' ...)` guard);
        # dot-sourcing here is safe and just defines the function.
        $builderOk   = $false
        $builderError = ''
        try {
            & {
                . $builderPath
                Build-WaggleProposalMatrix -ConfigPath $cfgPath -EpochId 'ledger-contract-smoke' -IterationId $itid | Out-Null
            }
            $builderOk = $true
        } catch {
            $builderOk = $false
            $builderError = $_.Exception.Message
        }
        $detail = ''
        if (-not $builderOk) {
            $detail = "exception: $builderError"
        }
        Assert-True 'Build-WaggleProposalMatrix tolerates missing state/regression_ledger.json' $builderOk $detail
        $synthDir = Join-Path $iterDir 'external_reviews/synthesis/ledger-contract-smoke'
        Assert-True 'matrix output directory exists after the run' (Test-Path -LiteralPath $synthDir)
        if (Test-Path -LiteralPath $synthDir) {
            Assert-True 'matrix proposal_matrix.json was emitted' (Test-Path -LiteralPath (Join-Path $synthDir 'proposal_matrix.json'))
        }
    }
    finally {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp
    }
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host ("Result: {0}/{1} tests passed" -f $Script:Pass, ($Script:Pass + $Script:Fail)) -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
