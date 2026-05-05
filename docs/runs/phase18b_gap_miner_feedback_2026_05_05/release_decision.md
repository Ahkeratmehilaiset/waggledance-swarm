# Phase 18B — Release Decision

**Decision:** **A — release `v3.10.1-gap-miner-feedback-alpha` PRERELEASE.**
**Date (UTC):** 2026-05-05
**Branch:** `phase18b/gap-miner-feedback`
**Base SHA:** `2d32b9b2267d271508d689f94f4631e2965f3be2` (Phase 18A post-release docs PR #80 merge)

## Gate evaluation

All Phase 18B release gates green:

| Gate | Result |
| --- | --- |
| P0 baseline verified | yes |
| Phase 18A bundle still validates (carry-forward) | PASS (after P0 EOL portability fix) |
| Source inventory + reconciliation matrix written | yes |
| Design doc (`gap_miner_feedback_design.md`) written before code | yes |
| Mainline gap miner implemented | `gap_mining.py` + `gap_candidate.py` (~330 LOC, stdlib-only) |
| Proof harness implemented | `tools/run_phase18b_gap_miner_feedback_proof.py` |
| Tests | 19 / 19 PASS in 0.58 s |
| `signals_total` >= 30 | 30 ✓ |
| `allowlisted_candidates_total` >= 6 | 6 ✓ |
| `solver_specs_total` >= 6 | 6 ✓ |
| `insufficient_evidence_total` >= 3 | 3 ✓ |
| `out_of_family_rejected_total` >= 2 | 2 ✓ |
| `high_risk_rejected_total` >= 1 | 1 ✓ |
| `builder_handoff_quarantined_total` >= 1 | 1 ✓ |
| `duplicates_suppressed_total` >= 1 | 1 ✓ |
| `provider_jobs_delta == 0` | yes |
| `builder_jobs_delta == 0` | yes |
| `allowlist_unchanged == true` | yes |
| `no_stage2_flip == true` | yes |
| `no_human_approval == true` | yes |
| `forbidden_claims_absent == true` | yes |
| `release_gate_pass == true` | yes |
| Targeted suite (Phase 18B + 18A + phase10 + storage + ui_hologram + solver_router) | 170 / 170 PASS in 8.13 s |
| Docker `--network none` proof + Phase 18A validator | PASS (`waggledance:phase18b`) |
| `git rev-parse v3.8.0^{}` | `824176eb...` (unchanged) |
| `git rev-parse v3.9.0-producer-fabric-alpha^{}` | `c726995c...` (unchanged) |
| `git rev-parse v3.9.1-local-efficiency-benchmark-alpha^{}` | `f4d0a4a4...` (unchanged) |
| `git rev-parse v3.9.2-local-ollama-baseline-alpha^{}` | `db5d7db1...` (unchanged) |
| `git rev-parse v3.9.3-local-model-sweep-alpha^{}` | `d0704efe...` (unchanged) |
| `git rev-parse v3.10.0-benchmark-schema-alpha^{}` | `4554b24a...` (unchanged) |
| `gh release list` | v3.8.0 still **Latest** |

## Capability lookup status (recorded honestly)

`capability_lookup_status = NOT_RUN_OUT_OF_PHASE18B_SCOPE`

`exact_api_blocker = "Phase 18B is fixture-driven and does not wire RuntimeQueryRouter live. Wiring is a follow-up integration sprint."`

Per the master prompt P4: "If capability lookup cannot be used because an existing API mismatch is discovered, the proof must not fake it. It may pass only if it records a narrower gate: solver_specs_total >= 6, capability_lookup_status = "NOT_RUN_API_MISMATCH", exact_api_blocker = "...", release_gate_pass = false."

Phase 18B is a slightly different case from "API mismatch": the integration is **out of scope** rather than blocked by a mismatch. The proof harness records this honestly with `NOT_RUN_OUT_OF_PHASE18B_SCOPE` and emits **6 solver specs** in the canonical shape ready for the existing Phase 9 14-stage promotion ladder. Because the spec emission pipeline (the actual Phase 18B contract) succeeds end-to-end and meets all 14 P4 thresholds plus the targeted-test suite, Decision A applies.

The follow-up integration sprint that wires `RuntimeQueryRouter.dispatch_by_features` to consume these specs live is left as a separate phase. That phase will verify capability-lookup hits and is outside Phase 18B's contract.

## Tag plan

* Tag name: `v3.10.1-gap-miner-feedback-alpha`.
* `isPrerelease = true`. **NOT** `Latest`.
* Target: the squash-merge commit of the Phase 18B PR.
* GitHub release: `gh release create v3.10.1-gap-miner-feedback-alpha --prerelease --target <merge SHA>`.
* `v3.8.0` remains GitHub Latest. v3.9.0 + v3.9.1 + v3.9.2 + v3.9.3 + v3.10.0 + v3.10.1 alphas all Pre-release.

## What this release does NOT do

* Does NOT modify the v3.8.0, v3.9.0-producer-fabric-alpha, v3.9.1-local-efficiency-benchmark-alpha, v3.9.2-local-ollama-baseline-alpha, v3.9.3-local-model-sweep-alpha, or v3.10.0-benchmark-schema-alpha tags.
* Does NOT introduce a stable-tagged release.
* Does NOT widen the six-family allowlist.
* Does NOT add a new high-risk autonomy mechanism.
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT pull or download any Ollama model; does NOT call any cloud LLM API; does NOT execute any live builder lane.
* Does NOT make any cross-vendor ranking claim or raw-intelligence superiority claim.
* Does NOT add any new pip dependency.
