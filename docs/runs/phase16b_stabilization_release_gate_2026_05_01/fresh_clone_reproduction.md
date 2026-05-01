# Phase 16B P5 — Reproduction reports

This document records two reproduction passes:

1. **Local-clone reproduction at the v3.7.5-upstream-runtime-alpha state** (this session, P5) — confirms that the published Phase 16A state is reproducible from a clone, with `waggledance` imported from the clone (not from the source worktree). Per the master prompt, **a local-clone reproduction does NOT satisfy the stable v3.8.0 remote/fresh-clone gate**.
2. **Final remote/fresh-clone reproduction against post-merge main** — required for v3.8.0 stable, executed in P13.5 after the Phase 16B PR merges. Recorded below as `pending`.

## 1. Local-clone reproduction at v3.7.5 (P5)

### Clone setup

| field | value |
|---|---|
| temp clone directory | `C:/Users/mfi0jjko/AppData/Local/Temp/wd-p16b-localclone-Ci8wRb` |
| `git clone` source | local file path `C:/python/project2-master` |
| `git clone --no-local` | yes (avoids hardlinks; produces a real copy) |
| branch checked out | `phase16b/stabilization-release-gate` |
| HEAD SHA inside clone | `d18e1dad32b998ba446290cafdb26b21be9adeb1` (Phase 16A merge of PR #63) |
| `git config --get remote.origin.url` inside clone | `C:/python/project2-master` (local path — **NOT a GitHub HTTPS URL**) |
| `python -c "import waggledance; print(waggledance.__file__)"` inside clone | `C:\Users\mfi0jjko\AppData\Local\Temp\wd-p16b-localclone-Ci8wRb\waggledance\__init__.py` (proves import path is in clone, not in source worktree) |
| Python | system 3.13.7 (no fresh venv created — out of session budget; uses already-installed deps) |
| tags visible after `git fetch --tags`: | `v3.6.0`, `v3.6.1-substrate`, `v3.7.0-autogrowth-alpha`, `v3.7.1-autoloop-alpha`, `v3.7.2-runtime-harvest-alpha`, `v3.7.3-hotpath-alpha`, `v3.7.4-runtime-hint-alpha`, `v3.7.5-upstream-runtime-alpha` |
| temp clone created during this session | yes |

### Commands run inside clone

```
python -c "import waggledance; print(waggledance.__file__)"
python -m pytest tests/autonomy_growth/ -q
python tools/run_upstream_structured_request_proof.py
```

### Result

| command | result |
|---|---|
| import waggledance | OK — imported from clone path |
| `pytest tests/autonomy_growth/` | **200 passed** in 192.27s |
| `tools/run_upstream_structured_request_proof.py` | passed; `auto_promotions_total = 98`, `provider_jobs_delta = 0`, `builder_jobs_delta = 0` |

### Classification

**Local-clone reproduction.** NOT remote/fresh-clone. Reasons:

1. The clone's `remote.origin.url` is `C:/python/project2-master` (a local filesystem path), not a GitHub HTTPS URL.
2. The session did not create a fresh venv inside the temp directory — system Python's already-installed packages were used.
3. This pass reproduces the **v3.7.5 state** (Phase 16A merge SHA = `d18e1dad`), not Phase 16B's expanded 104-corpus.

Per the master prompt's `FRESH CLONE VERIFICATION RULE`, this result does NOT satisfy the stable v3.8.0 remote/fresh-clone gate.

### Stable gate disposition

g02 (remote/fresh clone reproduction verified against post-merge main) — **NOT YET PASSED** at P5. Remote/fresh-clone verification is required at P13.5 for v3.8.0 stable.

---

## 2. Remote/fresh-clone reproduction against post-merge main (P13.5)

**Executed in P13.5 after the Phase 16B PR merges.** Recorded below.

| field | value |
|---|---|
| status | <to be filled in P13.5> |
| `git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git <tmpdir>` | <pending> |
| HEAD SHA inside clone | <pending> |
| post-merge main SHA | <pending> |
| `git fetch --tags origin` | <pending> |
| tag list visible (must include all v3.6.x, v3.7.x, plus the new tag) | <pending> |
| install command run | <pending> |
| `python -c "import waggledance; print(waggledance.__file__)"` | <pending> |
| import path inside fresh clone | <pending> |
| commands run | <pending> |
| pass/fail | <pending> |
| classification | <pending> |

### Stable gate disposition

g02 — **PENDING** until P13.5.
