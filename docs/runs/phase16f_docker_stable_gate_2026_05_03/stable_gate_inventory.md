# Phase 16F — Stable Gate Inventory (22 gates)

**Date:** 2026-05-03
**Branch:** `phase16f/docker-stable-gate`
**Carry-forward from:** Phase 16D (PR #66, SHA `7210a7e`)

## Summary

| Metric | Value |
|---|---|
| Total gates | 22 |
| Carry-forward PASS from 16D | 8 |
| Pending action this session | 14 |
| Failed at session start | 0 |
| **Stable v3.8.0 blocked at start by** | **g01 + g19 only (Docker e2e, Docker no-network)** |

## Gate ledger

| ID | Name | Required for stable | Initial status | Phase 16D status | Remaining action |
|---|---|:---:|---|---|---|
| g01 | Docker end-to-end | YES | PENDING_P2_P3 | FAIL_NOT_VERIFIED | build phase16f image; 3 proofs --network none; corpus 104 |
| g02 | Remote/fresh clone post-merge | YES | PENDING_P10 | PARTIAL (local only in 16D) | post-merge clone from GitHub HTTPS; smoke + restart proof |
| g03 | README current truth | YES (docs) | PENDING_P7 | PASS (16D v3.7.8) | update to v3.8.0 candidate language |
| g04 | 100+ solver release gate | YES | CARRY_FORWARD_PASS | PASS (104 seeds) | re-verify in P3 + P4 + P10 |
| g05 | Security audit / Bandit | YES | PENDING_P5 | PASS (16D HIGH 16→0) | re-run bandit; HIGH=0; pip-audit |
| g06 | Provider/builder delta = 0 | YES | PENDING_P3_P4 | PASS (Phase 15/16A/16B) | re-assert delta=0 in Docker + local proofs |
| g07 | Full-corpus restart proof | YES | PENDING_P3_P4 | PASS (104/104 served pre+post) | re-run full restart proof |
| g08 | Proof soak (3 iter) | YES | PENDING_P4 | PASS (16D 9/9 no flakes) | run --iterations 3; 9/9 |
| g09 | No Stage-2 atomic flip | YES | CARRY_FORWARD_PASS | PASS | verify 00_README PREPARATION ONLY held |
| g10 | No HUMAN_APPROVAL collected | YES | CARRY_FORWARD_PASS | PASS (CLAUDE.md rule 10) | this session does not collect approval |
| g11 | No allowlist widening | YES | CARRY_FORWARD_PASS | PASS (six-family unchanged) | verify low_risk_policy unchanged |
| g12 | No high-risk auto-promotion | YES | CARRY_FORWARD_PASS | PASS (14-stage ladder gated) | verify human_approval_id required |
| g13 | No actuator autonomy | YES | CARRY_FORWARD_PASS | PASS (SafeActionBus gated) | verify action_bus + action_gate |
| g14 | Control-plane / MAGMA truth | YES | CARRY_FORWARD_PASS | PASS (16D semantic fingerprint preserved) | verify _DEFAULT_FAISS_DIR + persisted state |
| g15 | Release docs truth | NO (cosmetic) | PENDING_P7 | PASS (16D truthful) | update to v3.8.0 candidate; preserve no-consciousness |
| g16 | Tag target correctness | YES | PENDING_P11 | N/A | annotated v3.8.0 on post-merge main SHA |
| g17 | GitHub release metadata | YES | PENDING_P12 | N/A | tagName=v3.8.0; isPrerelease=false; publishedAt set |
| g18 | CI green | YES | PENDING_P9 | RED on main (truth-regression shallow-clone) | PR CI must be green; PR #67 fixes shallow-clone issue separately |
| g19 | Docker runtime no-network | YES | PENDING_P3 | FAIL_NOT_VERIFIED | docker run --network none → corpus 104 |
| g20 | Fresh-clone tag fetch | YES | PENDING_P10 | N/A | post-merge tmpdir clone sees all tags |
| g21 | Bandit B324 carry-forward | YES | CARRY_FORWARD_PASS | PASS (16D 16→0) | verify usedforsecurity=False additions intact |
| g22 | GitHub Latest = v3.8.0 | YES | PENDING_P12 | GitHub Latest = v3.6.0 | gh release list shows v3.8.0 as Latest |

## Carry-forward PASSes from Phase 16D (8 gates)

g04, g09, g10, g11, g12, g13, g14, g21 — all carry forward from 16D with no expected change. They will be re-verified by source-grep / proof-replay in P3 + P4.

## Stable blockers at session start (2 gates)

* **g01 Docker end-to-end** — Docker is now available in this session (verified P0). Will be exercised in P2 (build) and P3 (runtime proofs).
* **g19 Docker runtime no-network** — same Docker availability resolves this; specifically requires `--network none` runtime success.

Both gates are addressed in P2 + P3.

## Known issue carry-forward

* **g18 CI** — main has been red since Phase 16A due to a shallow-clone test (`tests/phase10/test_truth_regression.py::test_phase10_branch_history_is_linear_descended_from_main`). Fix is in flight as PR #67 (`fix/ci-fetch-depth-truth-regression`). Phase 16F PR will likely fail CI until #67 lands or unless this PR carries the fetch-depth=0 fix. Decision in P9 based on #67's merge status.

## Pre-existing constraint that is NOT a Phase 16F gate (informational only)

`.dockerignore` excludes `tools/`, `tests/`, and `docs/` from the build context. The Phase 16F runtime-proof step requires those paths inside the image. P2 will apply a deterministic, minimal `.dockerignore` adjustment (carve-outs for `tools/run_*.py` and `tests/autonomy_growth/`) per the master prompt's "small deterministic fixes" allowance. This is the only Dockerfile-related fix anticipated; no architecture changes.
