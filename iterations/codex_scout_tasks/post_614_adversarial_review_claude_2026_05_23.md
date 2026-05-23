# Post-#614 Adversarial Review — Claude

Date: 2026-05-23
Author: Claude
Task: Codex 10:54:51 read-only adversarial review of the post-#614 surface
(origin/main @ 58eb646c) focused on (a) overclaim risk, (b) receipt-bound
vs opt-in classification precision, (c) rival local-check blockers, (d)
WriteRCOGate-follow-up vs phase-synthesis as next 24h slice. No
release/tag/Docker/stable/consensus_grade changes proposed.

## Method

Read-only static analysis at origin/main @ 58eb646c. Ran
`tools/magma_slice_counter_read.py`, `tools/magma_receipt_adoption_report.py`,
inspected the four newly-merged source files, traced production
callers of helper functions named in the adoption report. No code
execution beyond the read-only tools.

## Headline finding (HIGH severity overclaim risk)

**`waggledance/core/autonomy_growth/auto_promotion_engine.py` is
classified `receipt_bound` by the adoption report but has the exact
same helper-exists-no-callers gap that #612 and #614 just closed for
WriteRCOGate and SolverProvenance.** This is the most actionable
overclaim risk on the post-#614 surface.

### Evidence

- Adoption report at status `receipt_bound`, criticality `high`,
  reason "Promotion decisions define solver-growth authority."
- The receipt builder is `build_promotion_decision_receipt` at line 549.
  It returns a complete EvaluationResult + MAGMA receipt bundle for an
  outcome, with policy_digest, charter_digest, world_snapshot_digest,
  solver_contract_digest, full v0 envelope shape.
- `grep -r build_promotion_decision_receipt` finds **two** files:
  the engine itself (where it is exported in `__all__` at line 730)
  and `tests/autonomy_growth/test_auto_promotion_engine.py` (test).
  **Zero production callers.**
- `AutoPromotionEngine.evaluate_candidate()` (line 89) is the runtime
  entry point. It does NOT call `build_promotion_decision_receipt`,
  does NOT have an `emit_receipt_bundle` or `runtime_receipt_sink`
  hook in its constructor, and emits no receipts during the
  validate→shadow→decide→persist loop.
- The adoption report classifier sees `build_magma_receipt` and
  `build_evaluation_result` in the file and labels it `receipt_bound`,
  but the existence of those helper calls *inside a builder function
  with no production caller* is exactly the gap pattern that motivated
  the #607 anti-overclaim teeth (synthetic manifests cannot bypass)
  and the #613 precision rule (opt-in hook is not `receipt_bound`).

### Why this matters for the substrate honesty story

A4 solver-growth lifecycle just landed at `MEASURED_LOCAL_PARTIAL` via
#614. But the *promotion* axis (auto-promotion via low-risk family
allowlist) is conceptually right next to A4: it is the production
runtime decision that elevates a SIGNED candidate to ACTIVATED. The
post-#614 substrate now has:

- SolverProvenance `sign/activate/revoke` emits receipts via opt-in
  sink (correctly `receipt_capable_opt_in` per #613 precision).
- AutoPromotionEngine `evaluate_candidate` emits ZERO receipts and
  is classified `receipt_bound` purely on the basis of the unused
  builder.

That is an asymmetric truth: the *less-trusted* path (SolverProvenance)
honestly says "opt-in"; the *more-trusted* path (AutoPromotionEngine)
overclaims `receipt_bound` while emitting nothing.

### Falsifiability

The classification can be re-checked by running:

```
grep -r build_promotion_decision_receipt waggledance/
```

If the only hits are in `auto_promotion_engine.py` (the definition +
`__all__` entry) and in `tests/`, the gap is confirmed.

## Receipt-bound vs opt-in classification precision (status_counts)

`{"receipt_bound": 4, "receipt_capable_opt_in": 3}` per #613. Per-file
breakdown from the current adoption report:

- `waggledance/core/v3_13_0/write_rco_gate.py` → `receipt_capable_opt_in` ✅
  (post-#612, post-#613 — correct).
- `waggledance/core/v3_13_0/solver_provenance.py` → `receipt_capable_opt_in` ✅
  (post-#614 — correct).
- `waggledance/core/autonomy/runtime.py` → `receipt_capable_opt_in` ✅
  (AutonomyRuntime via #606 `runtime_receipt_sink`).
- `waggledance/core/autonomy_growth/auto_promotion_engine.py` →
  `receipt_bound` ⚠ (see HIGH finding above — overclaim risk).
- `tools/run_pdam_counterfactual_demo.py` → `receipt_bound` ✅
  (counterfactual demo emits and verifies receipts inline).
- `tools/run_magma_composition_demo.py` → `receipt_bound` ✅
  (composition demo emits and verifies inline).
- `tools/run_magma_adversarial_eval.py` → `receipt_bound` ✅
  (adversarial eval emits and verifies inline).

So **3 of 4 `receipt_bound` entries are tool demos** that actually
emit and verify receipts inline (correct). The fourth — auto-promotion
— is the false-positive.

## Rival local-check blocker review (post-#611)

Per `tools/run_v12_rival_local_check_matrix.py` at HEAD:

```
PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL = {
    "JamJet": "open_source_installable",
    "Preloop": "open_source_installable",
    "Microsoft AGT": "open_source_installable",
    "Asqav": "pypi_installable_cloud_dependent_headline",
}
```

Pass count `1/4` (AGT only). `consensus_grade=false` unchanged.
Blocker breakdown (mode 2 init template):

- AGT: `not_passed` (template smoke_result=not_run; real evidence at
  `docs/benchmarks/rival_local_checks/microsoft-agt.json` passed
  separately).
- Asqav: `not_passed` / `cloud_dependent` (`#600` partial verification).
- JamJet: `not_passed` ← unblocked by #611, awaiting actual local check.
- Preloop: `not_passed` ← unblocked by #611, awaiting actual local check.

**Honest next step (not a code change)**: now that JamJet and Preloop
have OSS-installable surfaces verified, an actual local-check execution
could move `pass_count` from 1/4 toward 2-3/4. This requires either
operator approval to install + run rival code, or operator-supervised
manual artifact collection. It is NOT in the 100h plan's next-24h
window — it is a follow-up beyond the current sprint.

## Counter-read invariants (post-#614)

`tools/magma_slice_counter_read.py` against
`docs/runs/magma_100h_sprint_2026_05_23/baseline.json`:

- decision: `pass`
- findings_count: 0
- static_findings: empty
- delta_findings: empty

So no regression in `release_boundary` all-false, no
`UNQUALIFIED_LABELS` leak, no `forbidden_claims` mutation. Healthy.

## Recommended next 24h slice (Q4 the operator asked Codex to clarify)

**Top recommendation: AutoPromotionEngine receipt sink wiring**
(closes the high-severity false-`receipt_bound` finding above).

Why this beats both alternatives:

1. **vs WriteRCOGate v1 EvaluationResult dispatcher** (#612 follow-up
   nit I raised in my RCO): real value but lower urgency. #612 already
   shipped opt-in receipts with the existing v0 EvaluationResult; v1
   is a polish, not a correctness gap.
2. **vs phase synthesis / baseline.json update** (72-100h plan task):
   premature. The synthesis should reflect a complete substrate state,
   and the auto-promotion overclaim is the most-visible asymmetry left
   to close before synthesizing.

### Proposed AutoPromotionEngine slice (scope ~150 LOC, 3 files)

1. `waggledance/core/autonomy_growth/auto_promotion_engine.py`:
   add `emit_receipt_bundle: Optional[Callable[[dict], None]] = None`
   to `AutoPromotionEngine.__init__` (the dataclass conversion mirrors
   the SolverProvenance pattern from #614, including the
   `_last_emitted_receipt` chain head with emit-before-advance
   ordering Codex's #614 BLOCK established).
2. Route the existing `build_promotion_decision_receipt` helper
   through the new sink at the persist step of `evaluate_candidate`
   (after `validate → shadow → decide` succeed, before commit).
3. New `tools/run_auto_promotion_receipt_emission_proof.py` mirroring
   the #606/#610/#614 proof CLI pattern. New
   `tests/tools/test_auto_promotion_receipt_emission_proof.py`
   covering: sink=None preserved; sink wired emits + verifies;
   tampered payload/evaluation rejected; chain head fail-closed on
   sink raise (the #614 lesson).
4. After landing, adoption-report classification flips from
   `receipt_bound` to `receipt_capable_opt_in` ⇒ status_counts
   becomes `{"receipt_bound": 3, "receipt_capable_opt_in": 4}` —
   honest.

### Acceptance tests

- `pytest tests/autonomy_growth/test_auto_promotion_engine.py` stays
  green (no behavior change when sink=None).
- New emission-proof test passes 8-10 cases mirroring #614.
- `magma_slice_counter_read.py` stays `pass, findings_count=0`.
- baseline.json should be regenerated by Codex's synthesis pass after
  the slice lands, NOT in the slice PR itself.
- `consensus_grade` stays `false`. No release_boundary or
  forbidden_claims touch.

### No-go

- If AutoPromotionEngine has an existing redesign ticket in Sprint 3
  backlog → defer.
- If `build_promotion_decision_receipt` would need to change shape to
  match the sink contract → either keep it as-is and adapt the sink
  call, or RFC the shape change separately.
- If the persist step (`_persist_outcome` or equivalent) is wrapped in
  a transaction that the receipt sink would interfere with → wire the
  sink AFTER commit, accept the small "receipt emitted post-persist"
  window, document it.

## Anti-overclaim guardrails this review honors

- No `consensus_grade=true` claim.
- No A3/A4/A1 label upgrade proposed.
- No release_boundary mutation proposed.
- No `forbidden_claims` mutation.
- The auto-promotion finding is framed as "overclaim risk in adoption
  classifier" — the FIX is to lower the classification to
  `receipt_capable_opt_in` by wiring an opt-in sink (matching #612/#614
  precedent), not to claim the runtime is already receipt-bound.

## Confidence

**High** on the auto-promotion overclaim finding (grep result is
unambiguous; classifier source visible).

**Medium-high** on the next-slice ranking — depends on the operator
weighting "close honest gaps" vs "polish existing"; both are valid
priorities.

## End of adversarial review — awaiting Codex synthesis + operator next-slice decision
