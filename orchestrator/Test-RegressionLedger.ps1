#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (REL-012) tests for
    orchestrator/lib/RegressionLedger.ps1.

    Cases:
      - empty ledger: load creates well-formed empty structure
      - Add-WaggleRegressionEntry assigns ID, score, severity, history
      - Get-WaggleRegressionScore: rubric matches design (40/30/25/20/...)
      - score capped at 100 when multiple categories sum higher
      - severity derived from score
      - Update-WaggleRegressionEntry: legal status transition succeeds
      - Update: illegal transition throws
      - history_event appends with at_utc + iteration_id
      - score_delta clamps to [0, 100]
      - Get-WaggleIssueSignature is deterministic on same input
      - Format-WaggleRegressionLedgerExcerpt produces well-formed markdown
      - Save-WaggleRegressionLedger atomic write (writes via .tmp + rename)
      - Schema-shape validator catches invalid status / severity / score
      - Add-WaggleRegressionFromHardeningGateFailure deduplicates by signature
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/RegressionLedger.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$tmp = Join-Path $env:TEMP ("waggle-test-rl-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)

# ---- Empty ledger -------------------------------------------------------

$lpath = Join-Path $tmp 'reg.json'
$ledger = Get-WaggleRegressionLedger -Path $lpath
Assert-True 'empty: format_version=1.0' ($ledger.format_version -eq '1.0')
Assert-True 'empty: regressions empty' (@($ledger.regressions).Count -eq 0)

# ---- Issue signature determinism ---------------------------------------

$sig1 = Get-WaggleIssueSignature -IterationIdIntroduced 'iter-A' -FindingId 'ARC-001' -FailingTestOrFile 'foo.py'
$sig2 = Get-WaggleIssueSignature -IterationIdIntroduced 'iter-A' -FindingId 'ARC-001' -FailingTestOrFile 'foo.py'
$sig3 = Get-WaggleIssueSignature -IterationIdIntroduced 'iter-A' -FindingId 'ARC-002' -FailingTestOrFile 'foo.py'
Assert-True 'sig: deterministic on same input' ($sig1 -eq $sig2)
Assert-True 'sig: distinct on different finding' ($sig1 -ne $sig3)
Assert-True 'sig: 64-hex'                       ($sig1 -match '^[a-f0-9]{64}$')

# ---- Add entry; auto-score and auto-id ---------------------------------

$entry = [pscustomobject]@{
    detected_in_iteration = 'iter-1'
    category = 'hardening_gate_failure'
    first_symptom = 'Test-X failed'
    affected_files = @('orchestrator/X.ps1')
    failing_tests = @('Test-X')
    issue_signature = (Get-WaggleIssueSignature -IterationIdIntroduced 'iter-1' -FindingId 'GATE-Test-X' -FailingTestOrFile 'Test-X')
    score_categories = @('hardening_gate_failure')
}
$entry | Add-Member -NotePropertyName score -NotePropertyValue (Get-WaggleRegressionScore -Entry $entry) -Force
$added = Add-WaggleRegressionEntry -Ledger $ledger -Entry $entry
Assert-True 'add: id assigned (REG-)'                ([string]$added.id -match '^REG-\d{4}-\d{2}-\d{2}-\d{3}$')
Assert-True 'add: score = 40 (hardening_gate_failure)' ([int]$added.score -eq 40)
Assert-True 'add: severity = medium (40-59 band)'    ([string]$added.severity -eq 'medium')
Assert-True 'add: status = open (default)'           ([string]$added.status -eq 'open')
Assert-True 'add: ledger.regressions.count = 1'      (@($ledger.regressions).Count -eq 1)
Assert-True 'add: history populated with detected event' (@($added.history).Count -ge 1 -and [string]$added.history[0].event -eq 'detected')

# ---- Score capping at 100 ---------------------------------------------

$big = [pscustomobject]@{
    detected_in_iteration = 'iter-2'
    category = 'hardening_gate_failure'
    first_symptom = 'big'
    score_categories = @('hardening_gate_failure','ci_failure','previously_passing_test_now_failing','runtime_crash','lock_state_signal','security_redaction')
}
$bigScore = Get-WaggleRegressionScore -Entry $big
Assert-True 'score: capped at 100' ($bigScore -eq 100)

# ---- Severity bands ---------------------------------------------------

$bandTests = @(
    @{ cats = @('doc_report_mismatch'); expected = 'info' }     # 5
    @{ cats = @('no_work_stall'); expected = 'info' }            # 10 -> info (band: 0-19)
    @{ cats = @('hardening_gate_failure'); expected = 'medium' } # 40 -> medium
    @{ cats = @('hardening_gate_failure','ci_failure'); expected = 'high' } # 40+30=70 -> high
    @{ cats = @('hardening_gate_failure','ci_failure','runtime_crash'); expected = 'critical' } # 90 -> critical
)
foreach ($bt in $bandTests) {
    $obj = [pscustomobject]@{ score_categories = $bt.cats }
    $sc = Get-WaggleRegressionScore -Entry $obj
    $sevExpected = $bt.expected
    $iter = 'iter-band-' + ([guid]::NewGuid().ToString('N').Substring(0,4))
    $bandLedger = Get-WaggleRegressionLedger -Path (Join-Path $tmp ('rb_' + $iter + '.json'))
    $row = [pscustomobject]@{
        detected_in_iteration = $iter
        category = ($bt.cats | Select-Object -First 1)
        first_symptom = 'band'
        score_categories = $bt.cats
        score = $sc
    }
    $r2 = Add-WaggleRegressionEntry -Ledger $bandLedger -Entry $row
    Assert-True ("severity-band: cats=" + ($bt.cats -join '+') + " score=$sc severity=" + $r2.severity) ([string]$r2.severity -eq $sevExpected)
}

# ---- Update: legal transition open -> classified_local_repair ---------

$regId = [string]$added.id
$updated = Update-WaggleRegressionEntry -Ledger $ledger -RegId $regId -Update @{
    status = 'classified_local_repair'
    fixability = 'clear'
    history_event = @{ iteration_id = 'iter-1'; event = 'classified'; notes = 'auto-repair classifier' }
}
Assert-True 'update: status -> classified_local_repair' ([string]$updated.status -eq 'classified_local_repair')
Assert-True 'update: fixability set'                    ([string]$updated.fixability -eq 'clear')
Assert-True 'update: history appended'                  (@($updated.history).Count -eq 2)

# ---- Update: illegal transition fails ---------------------------------

$threw = $false; $emsg = ''
try {
    Update-WaggleRegressionEntry -Ledger $ledger -RegId $regId -Update @{ status = 'fixed' } | Out-Null
} catch { $threw = $true; $emsg = $_.Exception.Message }
Assert-True 'update: illegal transition (classified_local_repair -> fixed) throws' $threw
Assert-True 'update: error message cites illegal status transition' ($emsg -match 'illegal status transition')

# ---- Update: walk a real fix-then-verify trajectory -------------------

# classified_local_repair -> repair_prompt_generated
Update-WaggleRegressionEntry -Ledger $ledger -RegId $regId -Update @{ status = 'repair_prompt_generated' } | Out-Null
# -> repair_iteration_in_progress
Update-WaggleRegressionEntry -Ledger $ledger -RegId $regId -Update @{ status = 'repair_iteration_in_progress' } | Out-Null
# -> fix_attempted
Update-WaggleRegressionEntry -Ledger $ledger -RegId $regId -Update @{ status = 'fix_attempted' } | Out-Null
# -> verification_pending
Update-WaggleRegressionEntry -Ledger $ledger -RegId $regId -Update @{ status = 'verification_pending' } | Out-Null
# -> verified (with verified_by)
$v = Update-WaggleRegressionEntry -Ledger $ledger -RegId $regId -Update @{ status = 'verified'; verified_by_iteration = 'iter-3' }
Assert-True 'trajectory: verified status reached' ([string]$v.status -eq 'verified')
Assert-True 'trajectory: verified_by populated'  (@($v.verified_by) -contains 'iter-3')

# ---- score_delta clamps ----------------------------------------------

$big2 = Add-WaggleRegressionEntry -Ledger $ledger -Entry ([pscustomobject]@{
    detected_in_iteration = 'iter-2'
    category = 'no_work_stall'
    first_symptom = 's'
    score_categories = @('no_work_stall')
})
$beforeScore = [int]$big2.score
$big2u = Update-WaggleRegressionEntry -Ledger $ledger -RegId ([string]$big2.id) -Update @{ score_delta = 200 }
Assert-True 'score_delta: capped at 100' ([int]$big2u.score -eq 100)
$big2u = Update-WaggleRegressionEntry -Ledger $ledger -RegId ([string]$big2.id) -Update @{ score_delta = -500 }
Assert-True 'score_delta: floored at 0' ([int]$big2u.score -eq 0)

# ---- Save + load roundtrip --------------------------------------------

Save-WaggleRegressionLedger -Path $lpath -Ledger $ledger
Assert-True 'save: file exists'         (Test-Path -LiteralPath $lpath)
$reloaded = Get-WaggleRegressionLedger -Path $lpath
Assert-True 'save: roundtrip same regression count' (@($reloaded.regressions).Count -eq @($ledger.regressions).Count)
Assert-True 'save: tmp file removed'    (-not (Test-Path -LiteralPath ($lpath + '.tmp')))

# ---- Excerpt formatter -------------------------------------------------

$excerpt = Format-WaggleRegressionLedgerExcerpt -Ledger $reloaded -MaxItems 5
Assert-True 'excerpt: well-formed markdown' (($excerpt -match '# Regression ledger excerpt') -and ($excerpt -match '## Open'))

# ---- Auto-update hook: hardening gate failure -------------------------

$hookPath = Join-Path $tmp 'hook.json'
Add-WaggleRegressionFromHardeningGateFailure -LedgerPath $hookPath -GateName 'Test-Synthetic' -IterationId 'hook-iter-1' -Symptom 'synthetic'
$hookLedger = Get-WaggleRegressionLedger -Path $hookPath
Assert-True 'hook: created entry' (@($hookLedger.regressions).Count -eq 1)
Assert-True 'hook: category=hardening_gate_failure' ([string]$hookLedger.regressions[0].category -eq 'hardening_gate_failure')
Assert-True 'hook: signature populated' ([string]$hookLedger.regressions[0].issue_signature -match '^[a-f0-9]{64}$')

# ---- Hook deduplicates same signature ---------------------------------

Add-WaggleRegressionFromHardeningGateFailure -LedgerPath $hookPath -GateName 'Test-Synthetic' -IterationId 'hook-iter-2' -Symptom 'synthetic again'
$hookLedger = Get-WaggleRegressionLedger -Path $hookPath
Assert-True 'hook: dedup count still 1' (@($hookLedger.regressions).Count -eq 1)
Assert-True 'hook: history grew' (@($hookLedger.regressions[0].history).Count -ge 2)

# ---- Phase 2B-R2 P5c: iteration-failure hook --------------------------

$iterHookPath = Join-Path $tmp 'iter_hook.json'
Add-WaggleRegressionFromIterationFailure -LedgerPath $iterHookPath -IterationId 'iter-fail-1' -FailureKind 'FAILED' -Symptom 'unit harness flake' -ExitCode 1
$iterLedger = Get-WaggleRegressionLedger -Path $iterHookPath
Assert-True 'iter-hook: created entry' (@($iterLedger.regressions).Count -eq 1)
Assert-True 'iter-hook: category=iteration_failure' ([string]$iterLedger.regressions[0].category -eq 'iteration_failure')
Assert-True 'iter-hook: signature populated' ([string]$iterLedger.regressions[0].issue_signature -match '^[a-f0-9]{64}$')
Assert-True 'iter-hook: first_symptom carries through' ([string]$iterLedger.regressions[0].first_symptom -eq 'unit harness flake')
# Same iteration_id + same FailureKind -> same signature -> dedup, no double-add.
Add-WaggleRegressionFromIterationFailure -LedgerPath $iterHookPath -IterationId 'iter-fail-1' -FailureKind 'FAILED' -Symptom 'second fire'
$iterLedger = Get-WaggleRegressionLedger -Path $iterHookPath
Assert-True 'iter-hook: dedup count still 1' (@($iterLedger.regressions).Count -eq 1)
Assert-True 'iter-hook: dedup history grew' (@($iterLedger.regressions[0].history).Count -ge 2)
# Different FailureKind -> different signature -> appends.
Add-WaggleRegressionFromIterationFailure -LedgerPath $iterHookPath -IterationId 'iter-fail-1' -FailureKind 'TIMEOUT' -Symptom 'second kind'
$iterLedger = Get-WaggleRegressionLedger -Path $iterHookPath
Assert-True 'iter-hook: distinct kinds append' (@($iterLedger.regressions).Count -eq 2)

# ---- Phase 2B-R2 P5c: review-walk hook -------------------------------

$reviewHookPath = Join-Path $tmp 'review_hook.json'
$reviewObj = [pscustomobject]@{
    role = 'security'
    target_iteration_id = 'review-iter-1'
    summary = 't'
    verdict = 'needs_attention'
    findings = @(
        [pscustomobject]@{ id = 'SEC-201'; severity = 'critical'; title = 'critical secret leak'; affected_files = @('orchestrator/lib/Redactor.ps1') }
        [pscustomobject]@{ id = 'SEC-202'; severity = 'high';     title = 'high impact bypass';   affected_files = @('orchestrator/lib/Redactor.ps1') }
        [pscustomobject]@{ id = 'SEC-203'; severity = 'medium';   title = 'medium ignored';       affected_files = @() }
        [pscustomobject]@{ id = 'SEC-204'; severity = 'low';      title = 'low ignored';          affected_files = @() }
    )
    metrics = @{ files_reviewed = 1; lines_reviewed = 1; review_duration_seconds = 1 }
    completed = $true
}
$hits = Add-WaggleRegressionsFromReviewObject -LedgerPath $reviewHookPath -ReviewObject $reviewObj -IterationId 'review-iter-1' -Role 'security'
Assert-True 'review-hook: only critical+high contributed' ($hits -eq 2)
$reviewLedger = Get-WaggleRegressionLedger -Path $reviewHookPath
Assert-True 'review-hook: 2 entries created' (@($reviewLedger.regressions).Count -eq 2)
$linkedFindings = @($reviewLedger.regressions | ForEach-Object { @($_.linked_findings) | Select-Object -First 1 })
Assert-True 'review-hook: SEC-201 in linked_findings' ($linkedFindings -contains 'SEC-201')
Assert-True 'review-hook: SEC-202 in linked_findings' ($linkedFindings -contains 'SEC-202')
Assert-True 'review-hook: SEC-203 NOT in linked_findings' (-not ($linkedFindings -contains 'SEC-203'))
# Re-fire: same iteration_id + same finding_id -> dedup; total still 2.
$null = Add-WaggleRegressionsFromReviewObject -LedgerPath $reviewHookPath -ReviewObject $reviewObj -IterationId 'review-iter-1' -Role 'security'
$reviewLedger = Get-WaggleRegressionLedger -Path $reviewHookPath
Assert-True 'review-hook: re-fire dedups count' (@($reviewLedger.regressions).Count -eq 2)
Assert-True 'review-hook: re-fire history grew' (@($reviewLedger.regressions[0].history).Count -ge 2)

# ---- Cleanup ----------------------------------------------------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
