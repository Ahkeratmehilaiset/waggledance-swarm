# Phase 18F — P0 Baseline Verification

**Date (UTC):** 2026-05-06
**Operator worktree:** `C:\Python\project2-master`
**origin URL:** `https://github.com/Ahkeratmehilaiset/waggledance-swarm.git`

## origin/main

`36ff4ec8d81e1addccd7025a71decbba25a73f05` — matches operator-reported expected SHA (Phase 18E post-release docs PR #87 squash-merge, 2026-05-06T05:52:46Z).

## Tag SHAs (9 prior tags — must remain unchanged through Phase 18F)

| Tag | Commit SHA | Status |
| --- | --- | --- |
| `v3.8.0` | `824176ebf2a6b8debed41982090a125cbe2ddad1` | GitHub **Latest**, isPrerelease=false |
| `v3.9.0-producer-fabric-alpha` | `c726995c816ee4c09e031c2190c3de6592e82879` | Pre-release |
| `v3.9.1-local-efficiency-benchmark-alpha` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` | Pre-release |
| `v3.9.2-local-ollama-baseline-alpha` | `db5d7db1ecb9ae6f17293f0bf7261f4c9d40e91c` | Pre-release |
| `v3.9.3-local-model-sweep-alpha` | `d0704efe46be18d480ed425ff83b087cd36ef9bd` | Pre-release |
| `v3.10.0-benchmark-schema-alpha` | `4554b24a47045ab10c1c0fbcb010f695d47d867c` | Pre-release |
| `v3.10.1-gap-miner-feedback-alpha` | `b408b14a4209ee9f8da00f040223a988815d0f87` | Pre-release |
| `v3.10.2-mined-solver-dispatch-alpha` | `e9aa1de10376109987a7c18d331bbcc996a9ddf9` | Pre-release |
| `v3.10.3-runtime-gap-replay-alpha` | `6c6ca8598333e47e3a1089c1db92367d571d6cdf` | Pre-release (most recent) |

`gh api repos/.../releases/latest --jq '.tag_name'` → `v3.8.0` (confirmed Latest).
`gh release view v3.10.3-runtime-gap-replay-alpha` → `isPrerelease=true`, `targetCommitish=6c6ca85...`, `publishedAt=2026-05-06T05:42:51Z`.

## Token-hygiene posture

* `git remote -v` → plain `https://github.com/Ahkeratmehilaiset/waggledance-swarm.git` (fetch + push).
* `git config --list | grep -cE 'gho_*|github_pat_*|x-access-token:...@'` → **0** real-token hits.
* All branch upstream entries scanned (count-only): **0** LEAK matches.

P0 secret-hygiene cleanup from Phase 18E (9 stale token-bearing branch upstreams rewritten to `origin`) is still in effect; no regression.

## Worktree

* Path: `C:\Python\project2-phase18f-incremental-gap-replay`.
* Branch: `phase18f/incremental-gap-replay` based on `origin/main` (`36ff4ec`).

## Tag plan (P0 → only confirmed at P11)

* Default candidate tag: `v3.10.4-incremental-gap-replay-alpha` (`isPrerelease=true`).
* Created at the Phase 18F core PR squash-merge SHA only after all gates pass.
* `v3.8.0` remains GitHub Latest.

## Verdict

P0 GREEN. Phase 18F may proceed to P1 inventory.
