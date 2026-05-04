# Phase 17A — Baseline Verification (P0)

**Date:** 2026-05-04
**Worktree:** `C:/Python/project2-phase17a-producer-fabric-scale` (clean, from `origin/main` @ `48ac0b3`)
**Branch:** `phase17a/producer-fabric-scale`

## Result: PASS — proceed to P1

## v3.8.0 stable invariants (must not move)

| field | value | unchanged from session start |
|---|---|---|
| tagName | `v3.8.0` | ✅ |
| tag object SHA | `7fc7725f757a5f1a9a0bcc07f66093ed0b6c1a00` | ✅ |
| tag target SHA | `824176ebf2a6b8debed41982090a125cbe2ddad1` | ✅ |
| isPrerelease | `false` | ✅ |
| name | `v3.8.0 — stable release` | ✅ |
| publishedAt | `2026-05-04T07:13:27Z` | ✅ |
| targetCommitish | `main` | ✅ |
| GitHub Latest | yes (`gh release list` shows `Latest` flag) | ✅ |

## v3.7.8-docker-gate-alpha (must remain prerelease)

| field | value |
|---|---|
| tagName | `v3.7.8-docker-gate-alpha` |
| isPrerelease | `true` |
| publishedAt | `2026-05-02T08:00:33Z` |

## origin/main state

* SHA: `48ac0b376c03a45890fb40cc83d9ed960e5c80de`
* Last 5 commits:
  * `48ac0b3` docs(phase16g): post-stable CI status truth — PR #67 merged (#70)
  * `86cde94` fix(ci): fetch-depth: 0 unblocks main CI (truth-regression test) (#67)
  * `8ed9541` docs(phase16f): post-stable docs update for v3.8.0 release (#69)
  * `824176e` Phase 16F — Docker stable gate and v3.8.0 release candidate (#68)  ← **v3.8.0 tag target**
  * `7210a7e` Phase 16D — final stable gate closure: Docker and Bandit (#66)

## Main push-event CI status (g18 carry-forward)

🟢 GREEN. Last 4 main push-event runs all `success`:

* PR #70 merge `48ac0b3`: WaggleDance CI ✅ 4m 19s, Tests ✅ 5m 30s
* PR #67 merge `86cde94`: WaggleDance CI ✅ 4m 7s, Tests ✅ 5m 29s
* (PR #68 / PR #69 push-runs failed before PR #67 merged — pre-fetch-depth-fix; not Phase 17A regressions.)

## Open PRs (informational, not Phase 17A scope)

7 dependabot PRs open: #19 setup-python, #21 actions/checkout, #22 scipy, #23 av, #24 cachetools, #25 websockets, #26 psutil. **All orthogonal**; not addressed in this session per rule 17 (sprint focus).

## Phase 8.5 branch inventory

| branch | origin SHA | local SHA | local→origin | substantive ahead | needs push? | preservation action |
|---|---|---|---|---:|---|---|
| `phase8.5/curiosity-organ` | `1a31b24` | `2efc4f7` | fast-forward | 1 | YES | fast-forward push (Session A real outputs) |
| `phase8.5/dream-curriculum` | `bfa526a` | `bfa526a` | identical | 0 | no | none |
| `phase8.5/self-model-layer` | `e3479dd` | `8478c59` | fast-forward | 1 | YES | fast-forward push (Session B real outputs) |
| `phase8.5/hive-proposes` | (none) | `de8c341` | local-only | 4 | YES | create new origin branch |
| `phase8.5/vector-chaos` | (none) | `322d8b8` | local-only | 4 | YES | create new origin branch |

**All 4 push targets are fast-forward — no force-push, no history rewrite. Rule 5 honored.**

The prompt explicitly authorizes preservation of `hive-proposes` and `vector-chaos` (rule 6). The two fast-forward updates to `curiosity-organ` and `self-model-layer` are also preservation per rule 9 ("Read and preserve") — they're empirical-output commits ("Session A real curiosity outputs from gap_miner run", "Session B real self-model outputs from build_self_model_snapshot run") that have value if/when Phase 17A ports producer code. Rule 5 (no force-push) is honored because all 4 are pure fast-forwards.

## Worktrees observed (not touched)

| path | branch | HEAD | role this session |
|---|---|---|---|
| `C:/Python/project2-master` | main | `7210a7e` (stale, pre-Phase-16F) | operator's dirty — **DO NOT TOUCH** |
| `C:/Python/project2` | phase8.5/dream-curriculum | `bfa526a` | source for read-only inspection |
| `C:/Python/project2-a` | phase8.5/curiosity-organ | `2efc4f7` | source + push target |
| `C:/Python/project2-b` | phase8.5/self-model-layer | `8478c59` | source + push target |
| `C:/Python/project2-d` | phase8.5/hive-proposes | `de8c341` | source + push target |
| `C:/Python/project2-r7_5` | phase8.5/vector-chaos | `322d8b8` | source + push target |
| `C:/Python/project2-flip` | phase9/post-campaign-atomic-flip | `8bf1869` | NOT TOUCHED (Stage-2 flip is forbidden) |
| `C:/Python/project2-phase16f-docker-stable-gate` | phase16g/post-stable-ci-truth | `89aca2d` | Phase 16F/16G artifact, NOT TOUCHED |
| `C:/Python/project2-phase17a-producer-fabric-scale` | phase17a/producer-fabric-scale | `48ac0b3` | **THIS SESSION'S WORK** |

## P0 stop-trigger check

Stop / handoff triggers checked, none tripped:

* v3.8.0 tag/release missing or changed: NO ✅
* v3.8.0 not stable (`isPrerelease=true`): NO ✅
* v3.8.0 no longer GitHub Latest: NO ✅
* Wall clock exceeded: NO ✅ (P0 ~5 min, 9h 55min remaining)

## Decision

**P0 PASS — proceed to P1 (preserve local-only phase8.5 branches via fast-forward push).**
