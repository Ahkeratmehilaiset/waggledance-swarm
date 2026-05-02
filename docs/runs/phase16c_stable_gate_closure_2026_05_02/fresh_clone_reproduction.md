# Phase 16C P5 — Reproduction reports

## 1. Local-clone reproduction at branch state (P5)

Done before P9 branch push. Verifies that the branch is structurally self-contained: a `git clone --no-local` of the local worktree imports `waggledance` from the clone path, not from the source worktree.

| field | value |
|---|---|
| temp clone directory | created in `%TEMP%/wd-p16c-localclone-XXXXXX` and removed after evidence collection |
| `git clone --no-local` source | local file path (`C:/python/project2-master`) |
| branch checked out in clone | `phase16c/stable-gate-closure` |
| HEAD SHA inside clone | `bada64c47aeb542bf8d51aa8079e173a1a175618` (Phase 16B merge SHA — Phase 16C commits not yet pushed) |
| `git config --get remote.origin.url` inside clone | `C:/python/project2-master` (local path — **NOT a GitHub HTTPS URL**) |
| `python -c "import waggledance; print(waggledance.__file__)"` | imports from inside the clone temp directory ✓ |
| `pytest tests/autonomy_growth/test_seed_library.py tests/autonomy_growth/test_full_restart_continuity_smoke.py -q` | **20 passed** in 0.37s |

### Classification

**Local-clone reproduction.** NOT remote/fresh-clone. Per the master prompt's `FRESH CLONE VERIFICATION RULE`:

* the clone source was a local filesystem path, not a GitHub HTTPS URL.

This pass verifies structural reproducibility but does NOT satisfy the stable v3.8.0 g02 remote/fresh-clone gate.

### Stable gate disposition

g02 — local-clone evidence only. Branch-ref remote/fresh-clone (after P9 push) and post-merge main remote/fresh-clone (P10) are required for stable.

---

## 2. Branch-ref remote/fresh clone after P9 push

Will be executed after the Phase 16C PR is opened, against the pushed branch on origin. Recorded below post-push.

| field | value |
|---|---|
| status | <pending P9 push> |
| `git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git <tmpdir>` | <pending> |
| branch checked out | `phase16c/stable-gate-closure` |
| HEAD SHA inside clone | <pending — should equal local HEAD after P9 commit + push> |
| `git config --get remote.origin.url` | <pending> |
| `git fetch --tags origin` result | <pending> |
| tag list visible | <pending — should include all v3.6.x and v3.7.x tags> |
| `python -c "import waggledance; print(waggledance.__file__)"` | <pending> |
| import path inside fresh clone | <pending — should be inside fresh clone> |
| `pytest tests/autonomy_growth/test_full_restart_continuity_smoke.py tests/autonomy_growth/test_seed_library.py -q` | <pending> |
| classification | <pending> |

---

## 3. Post-merge main remote/fresh clone (P10)

Will be executed after the Phase 16C PR merges, against `origin/main` at the post-merge SHA. **This is the verification that satisfies g02 for stable.** Recorded in P10.

| field | value |
|---|---|
| status | <pending P10> |
| post-merge main SHA | <pending merge> |
| HEAD SHA inside clone | <pending> |
| classification | <pending> |
