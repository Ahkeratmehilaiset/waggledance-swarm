# MAGMA 100h Post-614 Synthesis

Generated: 2026-05-23T10:55Z
Baseline: `docs/runs/magma_100h_sprint_2026_05_23/baseline.json`
Main ref: `58eb646c` (`feat(magma): A4 solver provenance v1 receipt emission proof (#614)`)

## Decision

Continue the MAGMA 100h sprint, but keep claims narrow:

- A3 is still `MEASURED_LOCAL_PARTIAL`.
- A4 is still `MEASURED_LOCAL_SYNTHETIC`.
- Rival local checks are still `1/4`, so `consensus_grade` stays `false`.
- Receipt adoption is now correctly split as `receipt_bound=4` and `receipt_capable_opt_in=3`.
- No release, tag, Docker stable/latest, external-effect authority, or stable claim is authorized by this slice.

## What Landed In This Window

- #610: A3 counterfactual proof bound to EvaluationResult v1.
- #611: JamJet and Preloop registry corrected to open-source installable surfaces while leaving missing local manifests blocked.
- #612: WriteRCOGate gained a default-off runtime receipt sink.
- #613: Adoption report precision fixed so optional receipt sinks do not overstate receipt-bound coverage.
- #614: SolverProvenance gained a receipt-bound activation/revocation proof, with chain-head fail-closed ordering fixed before merge.

## Measured Post-Merge State

Commands run from `origin/main@58eb646c`:

```powershell
python tools/show_v12_proof.py --json
python tools/run_magma_100h_sprint_baseline.py --json
python tools/run_v12_rival_local_check_matrix.py --evidence-dir docs\benchmarks\rival_local_checks --json
python tools/run_magma_adversarial_eval.py --json
python tools/run_write_rco_gate_receipt_demo.py --out-dir .pytest_tmp_write_rco_demo_post614 --json
python tools/magma_slice_counter_read.py
```

Observed results:

- V12 proof: `ok=true`.
- A3: 3 variants, 3 kind deltas, 2 gate deltas, receipt chain verified, `MEASURED_LOCAL_PARTIAL`.
- A4: solver growth proof available, receipt chain verified, still `MEASURED_LOCAL_SYNTHETIC`.
- Adversarial corpus: `38/38` pass, gate/verdict/reason-code accuracy `1.0`.
- Rival matrix: `1/4` passed, `3` blocked, `consensus_grade=false`.
- WriteRCOGate receipt demo: one local artifact route bound to one receipt, binding report `ok=true`, writes applied `false`.
- Counter-read: `decision=pass`, findings `0`.
- Claude adversarial review: high overclaim risk in `AutoPromotionEngine` adoption classification, recorded at `iterations/codex_scout_tasks/post_614_adversarial_review_claude_2026_05_23.md`.

## Current Blockers

There are no MAGMA baseline blockers, but there are still strategic blockers:

- JamJet local evidence manifest is missing.
- Preloop local evidence manifest is missing.
- Asqav remains `cloud_dependent`.
- Governance throughput has only one `ok` metric; six remain `insufficient_data`.
- A4 is still synthetic/local proof evidence, not production-grade solver-growth lifecycle evidence.
- `waggledance/core/autonomy_growth/auto_promotion_engine.py` is classified `receipt_bound`, but `build_promotion_decision_receipt` is only used by the helper/tests and not by the `evaluate_candidate()` runtime path.

## Next 24h Slice

Codex owns C5: AutoPromotionEngine receipt sink honesty fix.

Acceptance:

- Add a default-off `emit_receipt_bundle` or equivalent opt-in receipt sink to `AutoPromotionEngine`.
- Preserve existing behavior when the sink is absent.
- Wire the existing `build_promotion_decision_receipt` helper into the real `evaluate_candidate()` promotion path, with emit-before-chain-head-advance ordering if a chain head is introduced.
- Add a focused proof CLI and tests covering sink absent, sink emits/verifies,
  tampered payload/evaluation rejection, and post-commit sink failure behavior:
  the durable promotion/rollback decision stays committed, the local receipt
  chain head does not advance, and the raised error keeps the missing receipt
  visible.
- After landing, adoption classification should become more honest, likely `receipt_bound=3` and `receipt_capable_opt_in=4`, unless the implementation proves a stronger always-bound path.
- `magma_slice_counter_read.py` must remain pass.
- No label upgrade unless the proof source changes materially and counter-read permits it.

Post-merge contract repair (2026-05-24): PR #632 chose commit-first receipt
semantics for `AutoPromotionEngine`. This deliberately fixes the higher-risk
ghost-head case by running the opt-in receipt sink only after SQLite `COMMIT`.
If that post-commit sink raises, the already-committed promotion or rollback is
not rolled back; `_last_emitted_receipt` remains unchanged and
`AutoPromotionReceiptEmissionError` surfaces the receipt gap. A durable
transactional outbox would be a separate future hardening step, not the C5
acceptance contract landed by #632.

Claude owns C6: RCO/adversarial review of AutoPromotionEngine sink wiring plus rival blocker plan.

Acceptance:

- Review the AutoPromotionEngine patch for the same chain-head failure mode found in #614.
- Verify that adoption classification becomes more honest and does not overstate runtime receipt coverage.
- Propose the smallest safe path to reduce rival blockers from `3` to `2` without running untrusted rival code in CI.
- Keep `consensus_grade=false`.

## No-Go Rules

- Do not mark WriteRCOGate `receipt_bound`; it remains opt-in unless receipt emission is mandatory on the runtime path.
- Do not leave AutoPromotionEngine classified `receipt_bound` if the real runtime path remains receipt-optional or helper-only.
- Do not upgrade A4 from `MEASURED_LOCAL_SYNTHETIC`.
- Do not count JamJet or Preloop as local passes until pinned machine-readable manifests and offline artifacts verify.
- Do not mutate release readiness, release notes, Docker policy, tags, or stable claims in this sprint slice.
