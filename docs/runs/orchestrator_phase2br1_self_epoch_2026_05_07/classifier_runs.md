# Phase 2B-R1 — auto-repair classifier routing log

Per operator override, every bug discovered in P1–P9 goes through
`Get-WaggleFindingClass` (REL-014). This file records the classifier
input/output for each.

## P2-A: Build-WaggleCockpitData two cockpit-data bugs

### BWCD-BUG-001 — bundle name split on first underscore

* **Class:** TRIVIAL_AUTO_FIX (fixability=trivial, severity=medium, reason=trivial_with_small_scope)
* **Repair prompt built:** `repair_prompt_BWCD-BUG-001.md` (max_files=2)
* **Action taken:** local repair iteration applied —
  `orchestrator/Build-WaggleCockpitData.ps1:96-99` now uses
  `LastIndexOf('_')` so `claude_web_architect` parses as
  `(claude_web, architect)`.
* **Verification:** Test-CockpitData re-run with a new
  `cw3seg` test fixture asserting the 3-segment name parses
  correctly. 30/30 PASS (was 22/22).

### BWCD-BUG-002 — prompt_text serialized as PSObject

* **Class:** TRIVIAL_AUTO_FIX (fixability=trivial, severity=medium, reason=trivial_with_small_scope)
* **Action taken:** cast `Get-Content -Raw` output to `[string]`
  explicitly + null-guard.
  `orchestrator/Build-WaggleCockpitData.ps1:108-111`
* **Verification:** the previously-broken cockpit_data.json now
  shows `prompt_text` as a plain string (9445 bytes for the
  legacy claude_web fixture). New assertion in
  Test-CockpitData: every bundle's `prompt_text` `-is [string]`.

Both bugs found by exercising the cockpit data builder against a
real-repo iteration tree (the validation iteration left over from
the Phase 2B-R session). The synthetic Test-CockpitData fixture
used `gemini_architect` / `grok_reliability` (single-underscore
names) which masked BUG-001, and used inline `Out-File` content
which somehow returned a plain string and masked BUG-002. Both
synthetic and real-repo coverage now in place.

## P7-A: matrix builder StrictMode null-guard

### BWP-BUG-001 — `$Obj.PSObject.Properties[X] | ForEach-Object { $_.Value }` throws under strict mode

* **Class:** LOCAL_REPAIR (fixability=clear, severity=medium, reason=clear_or_trivial_local_scope)
* **Symptom found in P7:** matrix builder threw when an internal
  review JSON lacks `suggested_next_actions`. The pipe pattern
  yields one $null iteration; `$null.Value` violates strict mode.
* **Action taken:** explicit null-guard
  `if ($null -ne $Obj -and $Obj.PSObject.Properties[$proposalKey]) { $proposals = $Obj.$proposalKey }`
* **Verification:** new test `old-shape: ok=true (no throw under strict)` in
  `Test-ProposalMatrix.ps1`. 30/30 PASS (was 28/28).

## P6-A: classifier heuristic regex too strict

### CLF-BUG-001 — `\s+actual\s+` regex misses `actual:`

* **Class:** LOCAL_REPAIR (fixability=clear, severity=low, reason=clear_or_trivial_local_scope)
* **Symptom found in P6:** synthetic finding F2 with evidence
  `"expected key: foo_count actual: fooCount"` was classified
  as EXTERNAL_REVIEW_REQUIRED instead of TRIVIAL/LOCAL. The
  classifier's clear-signal regex required whitespace on both
  sides of `actual`, which doesn't match real-world
  punctuation-prefixed forms like `actual:`.
* **Action taken:** loosened the regex to
  `expected\s+.{1,80}[\s:;,]actual[\s:;,]` so colons / semicolons
  / commas around `actual` are accepted as boundaries.
* **Verification:** new tests `C17b` + `C17c` in
  `Test-FindingClassifier.ps1` assert the schema-mismatch
  evidence shape is now classified as LOCAL_REPAIR or
  TRIVIAL_AUTO_FIX. 28/28 PASS (was 26/26).

## P9-A: Invoke-WaggleReview top-level caller / DryRun mismatch

### INVK-BUG-001 — `$r.role` accessed on DryRun pscustomobject that lacks the `role` member

* **Class:** EXTERNAL_REVIEW_REQUIRED (fixability=ambiguous, severity=medium, reason=fixability_ambiguous)
* **Symptom found in P9:** `Invoke-WaggleReview.ps1 -DryRun` exits 1
  because the DryRun branch returns a pscustomobject without a
  `role` property and the top-level caller's
  `Write-Host ('Review {0} ...' -f $r.role, ...)` (lines 801/808)
  dereferences `$r.role` under StrictMode.
* **Action taken:** **NOT fixed locally per P10 procedure.** Added to
  `proposal_matrix.json` with full provenance for an external
  reviewer to weigh in on the right shape (add `role` to DryRun
  return vs. guard the caller vs. unify the two return shapes).
  This bug does not block P9 — the real (non-DryRun) review path
  builds a return object that DOES include `role` (line 756), so
  the architect review can run without touching DryRun.
* **Real-use note:** captured in `phase2br1_real_use_learnings.md`
  (P11.5) as evidence that a 1-line fix being routed to external
  review is the correct conservative behavior when the proposed
  fix offers multiple shapes. The classifier is not bypassed here.
