# Phase 16D — Reproduction reports

This document records three reproduction passes:

1. **P5 local-clone diagnostic** — pre-push, NOT remote/fresh.
2. **P9.5 branch-ref remote/fresh clone** — after branch push, before merge.
3. **P10 post-merge main remote/fresh clone** — after PR merge.

## 1. P5 local-clone diagnostic (pre-push)

| field | value |
|---|---|
| temp clone directory | `%TEMP%/wd-p16d-localclone-XXXXXX` (created and removed in-session) |
| `git clone --no-local` source | local file path `C:/python/project2-master` |
| branch checked out | `phase16d/final-stable-gate-closure` |
| HEAD SHA inside clone | `b87fbe443485cb7468e3381f5b57a654c51c71c0` (Phase 16C merge SHA — Phase 16D commits not yet pushed) |
| `git config --get remote.origin.url` | `C:/python/project2-master` (local path — **NOT a GitHub HTTPS URL**) |
| `python -c "import waggledance; print(waggledance.__file__)"` | imports from clone path ✓ |
| `pytest tests/autonomy_growth/test_full_restart_continuity_smoke.py tests/autonomy_growth/test_seed_library.py -q` | **20 passed** in 0.34s |

**Classification:** local-clone diagnostic. NOT remote/fresh. Per master prompt's `FRESH CLONE VERIFICATION RULE`, local-clone reproduction does NOT satisfy the stable g02 gate. Branch-ref remote/fresh in P9.5 + post-merge main remote/fresh in P10 are required for full evidence.

**Disposition:** local-clone diagnostic PASS — branch worktree is structurally self-contained.

---

## 2. P9.5 branch-ref remote/fresh clone (after push, before merge)

Will be executed after the Phase 16D PR is opened, against the pushed branch on `origin`. Recorded below post-push.

| field | value |
|---|---|
| status | <pending P9 push> |
| `git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git <tmpdir>` | <pending> |
| branch checked out | `phase16d/final-stable-gate-closure` |
| HEAD SHA inside clone | <pending — should equal local HEAD after P9 commit + push> |
| `git config --get remote.origin.url` | <pending — should be GitHub HTTPS URL> |
| `git fetch --tags origin` result | <pending> |
| tag list visible | <pending — should include all v3.6.x and v3.7.x tags including v3.7.7-stable-gate-alpha> |
| `python -c "import waggledance; print(waggledance.__file__)"` | <pending — should be inside fresh clone> |
| `pytest tests/autonomy_growth/test_full_restart_continuity_smoke.py tests/autonomy_growth/test_seed_library.py -q` | <pending> |
| `python tools/run_full_restart_continuity_proof.py` | <pending — should match P4 metrics> |
| classification | <pending — should be remote/fresh PASS> |

---

## 3. P10 post-merge main remote/fresh clone

Will be executed after the Phase 16D PR merges, against `origin/main` at the post-merge SHA. **This is the verification that satisfies g02 for stable.** Recorded in P10.

| field | value |
|---|---|
| status | <pending P10> |
| post-merge main SHA | <pending merge> |
| HEAD SHA inside clone | <pending — should equal post-merge main SHA> |
| classification | <pending — should be remote/fresh PASS> |
