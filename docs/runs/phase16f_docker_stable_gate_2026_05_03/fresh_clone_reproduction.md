# Phase 16F — Branch-ref fresh clone reproduction

**Date:** 2026-05-04
**Branch:** `phase16f/docker-stable-gate`
**Pushed SHA:** `1cfc5ddd4877178fd137de877966b26cc81ea182`

## Result: PASS — branch-ref fresh clone reproduces from GitHub HTTPS

## Step 1 — Clone

```bash
TMPCLONE=$(mktemp -d -t wd-p16f-branchref-XXXXXX)
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git "$TMPCLONE"
cd "$TMPCLONE"
git fetch origin phase16f/docker-stable-gate
git checkout phase16f/docker-stable-gate
```

Result: clone succeeded, branch checkout succeeded. Clone path: `/tmp/wd-p16f-branchref-wQ3R15` (Windows-host-mapped to `C:/Users/mfi0jjko/AppData/Local/Temp/wd-p16f-branchref-wQ3R15`).

## Step 2 — Verify clone identity

| field | expected | observed |
|---|---|---|
| `remote.origin.url` | `https://github.com/Ahkeratmehilaiset/waggledance-swarm.git` | ✅ matches |
| `git rev-parse HEAD` | `1cfc5ddd4877178fd137de877966b26cc81ea182` | ✅ matches pushed branch tip |
| `git fetch --tags origin` | sees all v3.6.x and v3.7.x tags | ✅ confirmed: `v3.6.1-substrate`, `v3.7.0-autogrowth-alpha`, `v3.7.1-autoloop-alpha`, `v3.7.2-runtime-harvest-alpha`, `v3.7.3-hotpath-alpha`, `v3.7.4-runtime-hint-alpha`, `v3.7.5-upstream-runtime-alpha`, `v3.7.6-stabilization-alpha`, `v3.7.7-stable-gate-alpha`, `v3.7.8-docker-gate-alpha` |
| Python import | `waggledance` resolves from clone path | ✅ `waggledance imported from: <TMPCLONE>/waggledance/__init__.py` |

## Step 3 — Smoke tests from fresh clone

```bash
python -m pytest tests/autonomy_growth/test_full_restart_continuity_smoke.py tests/autonomy_growth/test_seed_library.py -q
```

Result: **20 passed in 0.48 s**, 0 failures.

## Step 4 — Full restart continuity proof from fresh clone

```bash
python tools/run_full_restart_continuity_proof.py --out-dir /tmp/p16f_branchref --db /tmp/p16f_branchref/full_restart.db
```

| field | value | required | met |
|---|---|---|---|
| `corpus_total` | 104 | ≥ 100 | ✅ |
| pre-restart pass2 served / via-capability / miss | 104 / 104 / 0 | N / N / 0 | ✅ |
| persisted solver_count before/after | 104 / 104 | identical | ✅ |
| persisted capability_features before/after | 180 / 180 | identical | ✅ |
| post-restart pass2 served / via-capability / miss | 104 / 104 / 0 | N / N / 0 | ✅ |
| served_unchanged_across_restart | True | True | ✅ |
| served_via_capability_unchanged_across_restart | True | True | ✅ |
| solver_count_unchanged_across_reopen | True | True | ✅ |
| capability_features_unchanged_across_reopen | True | True | ✅ |
| provider_jobs_delta_across_restart | 0 | 0 | ✅ |
| builder_jobs_delta_across_restart | 0 | 0 | ✅ |
| cache_rebuild_success | True | True | ✅ |
| provider_jobs_delta_during_proof | 0 | 0 | ✅ |
| builder_jobs_delta_during_proof | 0 | 0 | ✅ |

## Step 5 — Cleanup

```bash
rm -rf "$TMPCLONE"
```

(Performed at end of P9.)

## Verdict

The branch-ref fresh-clone reproduction step asserts that an external operator who clones the public repo at the Phase 16F branch tip can reproduce the smoke tests and the full restart continuity proof byte-for-byte (104 / 104 / 180 / 0 / 0 / True invariants) without any local repo state. This satisfies the master prompt's P9 branch-ref fresh-clone contract.

The post-merge fresh-clone verification (P10) repeats the same exercise against `origin/main` after the PR merges, with the additional Docker rebuild and Docker proof steps. That step closes g02 + g20 stable gates.
