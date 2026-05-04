# Phase 16F — Release Decision

**Date:** 2026-05-04
**Branch:** `phase16f/docker-stable-gate`
**Decision authority:** Phase 16F master prompt P8

## Decision: **A. v3.8.0 stable candidate**

The Phase 16F session selects **option A** from the master prompt's three release-decision paths.

The stable tag will be created **only after** the Phase 16F PR merges and post-merge verification (P10–P12) passes. This document captures the branch-level evidence supporting the candidate status.

## Evidence supporting A

All branch-side stable gates required for v3.8.0 are PASS:

| gate | name | status | evidence |
|---|---|---|---|
| g01 | Docker end-to-end | **PASS** | `docker_build.md` — `waggledance:phase16f` 3.09 GB built reproducibly |
| g04 | 100+ solver release gate | PASS | corpus 104 carry-forward, asserted by `tests/autonomy_growth/test_seed_library.py::test_seed_library_meets_v3_8_0_release_gate_minimum` |
| g05 | Security audit / Bandit | PASS | `PHASE16F_SECURITY_CARRY_FORWARD.md` — HIGH=0, B324=0 |
| g06 | Provider/builder delta = 0 | PASS | 0/0 in 3 Docker proofs + 3 local proofs + 9-iter soak |
| g07 | Full-corpus restart proof | PASS | 104/104 pre+post restart, all invariants True (Docker + local) |
| g08 | Proof soak 3 iter | PASS | 9/9 no flakes (local) |
| g09 | No Stage-2 atomic flip | PASS | `STAGE2_CUTOVER_RFC.md` unchanged; `_DEFAULT_FAISS_DIR=data/faiss/` |
| g10 | No HUMAN_APPROVAL collected | PASS | CLAUDE.md rule 10 honored — Phase 16F is build/proof, not flip |
| g11 | No allowlist widening | PASS | six families unchanged, 104 seeds unchanged |
| g12 | No high-risk auto-promotion | PASS | promotion ladder unchanged |
| g13 | No actuator autonomy | PASS | SafeActionBus + ActionGate carry-forward |
| g14 | Control-plane / MAGMA truth | PASS | persisted state byte-identical Docker vs local |
| g15 | Release docs truth | PASS | README + CURRENT_STATUS + CHANGELOG + RELEASE_READINESS + DOCKER_QUICKSTART updated to "stable candidate" language |
| g19 | Docker runtime no-network | **PASS** | `docker_runtime_proofs.md` — all 3 proofs + 4 smoke files passed `--network none` |
| g21 | Bandit B324 cleanup carry-forward | PASS | B324 count = 0 |

## Gates pending post-merge (NOT branch-side)

These are intentionally NOT verified on the branch; they are verified in P10–P12 after merge:

| gate | name | when | what it verifies |
|---|---|---|---|
| g02 | Remote/fresh clone post-merge | P10 | post-merge clone from GitHub HTTPS reproduces smoke + restart proof |
| g16 | Tag target correctness | P11 | annotated tag v3.8.0 points at post-merge main SHA |
| g17 | GitHub release metadata | P12 | tagName=v3.8.0, isPrerelease=false, publishedAt set, target=post-merge SHA |
| g18 | CI green | P9 | PR-level CI on `phase16f/docker-stable-gate` |
| g20 | Fresh-clone tag fetch | P10 | post-merge tmpdir clone sees v3.8.0 + all carry-forward tags |
| g22 | GitHub Latest = v3.8.0 | P12 | `gh release list` shows v3.8.0 as Latest, not Pre-release |

## Why not B (v3.7.9-docker-verification-alpha)?

Option B (a fresh fail-closed alpha tag) is the documented fallback when material Docker progress is made but the stable gate cannot close. In Phase 16F the stable gate **does** close on the branch — Docker is fully verified end-to-end with `--network none`, and only the post-merge verification chain remains. Issuing a new alpha tag now would mis-signal the gate state and create an unnecessary v3.7.9 tag that supersedes nothing material from v3.7.8.

If post-merge verification fails for a non-Docker reason (CI flake, race against another merge, etc.), the appropriate response is to fix the post-merge regression and try again — not to issue a v3.7.9 alpha.

## Why not C (no tag)?

Option C (no tag) is the documented response when Docker is unavailable or Docker proof fails with no material progress. Phase 16F has full Docker availability, a successful build, and three passing canonical proofs `--network none` plus the smoke suite. C is not appropriate.

## Tag plan (P11)

If P10 post-merge verification passes:

```
git fetch origin main --tags
git checkout --detach origin/main
git tag -a v3.8.0 -m "v3.8.0 — stable release"
git push origin v3.8.0
gh release create v3.8.0 --title "v3.8.0 — stable release" --notes-file <release_notes.md>
```

NO `--prerelease` flag.

## Pre-merge invariants

* `EXPECTED_HEAD` will be captured at PR-merge time and used in `gh pr merge --match-head-commit="$EXPECTED_HEAD"`.
* No `--admin`, no `--no-verify`, no force-push.
* Branch preserved after merge per master prompt.
* No phase8.5/* branches touched.
* No `HUMAN_APPROVAL.yaml` collected.
* No `_DEFAULT_FAISS_DIR` mutation.
* No actuator autonomy added.
* No allowlist widening.

## Stable gate ledger final pre-PR state

* PASS at branch tip: 15 gates (g01, g04, g05, g06, g07, g08, g09, g10, g11, g12, g13, g14, g15, g19, g21)
* PENDING post-merge / CI: 6 gates (g02, g16, g17, g18, g20, g22)
* FAIL: 0 gates
* Decision: **A — proceed to PR creation, autonomous merge if guardrails pass, post-merge verification, and conditional v3.8.0 tag creation**.
