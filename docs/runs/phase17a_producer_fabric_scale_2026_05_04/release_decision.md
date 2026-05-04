# Phase 17A — Release Decision

**Date:** 2026-05-04
**Branch:** `phase17a/producer-fabric-scale`
**Decision authority:** Phase 17A master prompt P9

## Decision: **A. v3.9.0-producer-fabric-alpha PRERELEASE candidate**

The Phase 17A session selects **option A** from the master prompt's two release-decision paths.

The prerelease tag will be created **only after** the Phase 17A PR merges, post-merge verification (P11) passes, and post-merge fresh-clone reproduction confirms the artifact set on `origin/main`. This document captures the branch-level evidence supporting the candidate status.

## Evidence supporting A

All branch-side gates required for `v3.9.0-producer-fabric-alpha` (PRERELEASE) are PASS:

| gate | name | status | evidence |
|---|---|---|---|
| Phase 8.5 branch preservation | All five `phase8.5/*` branches on origin (no force-push, no history rewrite) | **PASS** | `phase85_branch_preservation.md` |
| Producer fabric proof | 68 IR objects across 6 kinds; 6/6 negative cases; 0/0 deltas | **PASS** | `tools/run_phase17a_producer_fabric_proof.py` + `producer_fabric_proof.json` + 18/18 integration tests |
| 10k solver-scale proof | 10000 synthetic descriptors built; 1000/1000 capability hits; 0 FIFO fallback; 0 miss; p50=4.24 ms / p95=10.78 ms / p99=14.10 ms | **PASS** (real capability lookup path exercised) | `tools/run_solver_scale_proof.py --descriptors 10000` + `solver_scale_proof.json` + 21/21 integration tests |
| Canonical seed corpus growth | 104 → 128 (+24, +4 per family); 32+21+21+18+18+18 split | **PASS** | `low_risk_seed_library.py` + 10/10 seed library tests including new Phase 17A material-growth + per-family floor tests |
| Provider/builder delta = 0 | 0/0 in all 4 critical proofs (Phase 15, 16A, 16B P2, Phase 17A producer fabric, Phase 17A 10k scale, soak) | **PASS** | every proof JSON |
| Docker `--network none` proof | producer fabric + 10k scale + full restart all PASS in `waggledance:phase17a` (9 GB) `--network none` | **PASS** | `docker_phase17a_verification.md` |
| Targeted tests | 268 passed in autonomy_growth + phase10; 122 passed in storage + ui_hologram + solver_router | **PASS** (no failures) | pytest output |
| Proof soak (3 iterations) | 9/9 PASS, no flakes, mean ~26 s/iter on 128-seed corpus | **PASS** | `proof_soak_report.json` |
| No Stage-2 atomic flip | producer fabric proof negative-case asserts Stage-2 flip request in offline proof is rejected | **PASS** | `producer_fabric_proof.json` `negative_cases[stage2_flip_request_in_offline_proof].passed = true` |
| No HUMAN_APPROVAL collected | producer fabric proof negative-case asserts HUMAN_APPROVAL in offline proof is rejected | **PASS** | `producer_fabric_proof.json` `negative_cases[human_approval_in_offline_proof].passed = true` |
| No allowlist widening | seeds and producers stay strictly within the existing six families | **PASS** | producer-fabric proof's `no_allowlist_widening = true`; per-family floor test (32/21/21/18/18/18) |
| No consciousness claim | competitive evidence matrix explicitly disclaims it | **PASS** | `docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` |
| v3.8.0 untouched | tag SHA `7fc7725f...`, target `824176eb`, isPrerelease=false, GitHub Latest — all unchanged | **PASS** | will be re-verified at P11 post-merge |
| Release docs truthful | competitive matrix labels every axis PROVEN / MEASURED / INFERRED / NOT CLAIMED | **PASS** | `COMPETITIVE_EVIDENCE_MATRIX_2026.md` + `LOCAL_AI_RUNTIME_COMPARISON.md` |

## Gates pending post-merge (NOT branch-side)

These are intentionally NOT verified on the branch; they are verified in P11 (post-merge):

| gate | when | what it verifies |
|---|---|---|
| PR-level CI green | P10 | PR-level CI on `phase17a/producer-fabric-scale` |
| Post-merge proofs reproduce on `origin/main` | P11 | `git checkout --detach origin/main` reruns all 5 proofs |
| Post-merge Docker rebuild reproduces | P11 | `docker build -t waggledance:v3.9.0-producer-fabric-alpha-rc . && docker run --rm --network none ... ` |
| Post-merge fresh clone reproduces | P11 | tmpdir clone from GitHub HTTPS at post-merge SHA reruns smoke + restart + producer + scale |
| Annotated tag points at post-merge main SHA | P12 | `git rev-parse v3.9.0-producer-fabric-alpha^{}` matches `git rev-parse origin/main` after merge |
| GitHub release metadata correct | P12 | `gh release view v3.9.0-producer-fabric-alpha --json` shows `isPrerelease=true`, `targetCommitish=main`, non-empty `publishedAt` |
| v3.8.0 still GitHub Latest | P12 | `gh release list` shows `v3.8.0 - stable release` with `Latest` flag; the new prerelease appears as `Pre-release` |

## Why not B (NO TAG)?

Option B is the documented response when:

* phase8.5 local-only branches cannot be preserved → preservation done in P1 with 4 fast-forward pushes; no force-push.
* producer code requires unsafe runtime activation → producer fabric is offline-only and proof asserts no Stage-2 flip / no HUMAN_APPROVAL collection.
* producer proof fails → 18/18 integration tests pass; 6/6 negative cases pass.
* 10k scale proof fails → 1000/1000 capability hits; 0 FIFO fallback; 0 miss.
* provider/builder delta != 0 → 0/0 in every critical proof.
* Docker fails → producer + scale + restart all pass `--network none` in `waggledance:phase17a`.
* CI fails → not yet verified (P10 dependent), but PR-level CI of similar surface (Phase 16F PR #68) passed.
* Evidence is too incomplete → competitive matrix uses honest labels for every axis; no axis is unsupported.
* Wall clock 25 h exceeded → P0 through P8 elapsed within budget.

None of those triggers fire. Option B is not appropriate.

## Tag plan (P12)

If P11 post-merge verification passes:

```
git fetch origin main --tags
git checkout --detach origin/main
git tag -a v3.9.0-producer-fabric-alpha -m "v3.9.0-producer-fabric-alpha — Phase 17A producer fabric + 10k solver scale"
git push origin v3.9.0-producer-fabric-alpha
gh release create v3.9.0-producer-fabric-alpha --prerelease --title "v3.9.0-producer-fabric-alpha — Phase 17A" --notes-file <release_notes.md>
```

`--prerelease` flag REQUIRED. v3.8.0 must remain GitHub Latest after the new prerelease is published.

## Stable v3.9.0?

**FORBIDDEN this session.** Master prompt rule 4: "This sprint may create only `v3.9.0-producer-fabric-alpha` as prerelease, and only if gates pass." Promoting to stable v3.9.0 requires:

* a separate stable-gate session,
* a paired benchmark against a frontier model (currently NOT CLAIMED per the competitive matrix),
* operator-driven approval that this session must not collect.

## Stable gate ledger final pre-PR state

* PASS at branch tip: 13 gates (every Phase 17A branch-side criterion)
* PENDING post-merge / CI: 7 gates (PR CI, post-merge proofs, post-merge Docker, post-merge fresh clone, tag target, release metadata, GitHub Latest)
* FAIL: 0 gates
* Decision: **A — proceed to PR creation, autonomous merge if guardrails pass, post-merge verification, and conditional v3.9.0-producer-fabric-alpha PRERELEASE creation**.
