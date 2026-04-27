# Phase 8 — CI Baseline

- **Date:** 2026-04-24
- **Branch:** `phase8/honeycomb-solver-scaling-foundation`
- **Base commit:** `d9a6dce` (HEAD of main at branch creation)
- **Python:** `.venv` (Python 3.13.7)
- **Environment:** Windows 11, port 8002 occupied by concurrent `ui_gauntlet_400h` campaign

## Repo state

| Item | Value |
|---|---|
| `pyproject.toml` version | `3.5.7` |
| `waggledance.__version__` | `3.5.7` |
| Remote tag `v3.5.7` | `e4923ee5` present on origin |
| Working tree | uncommitted runtime data only (campaign artifacts) |

Version consistency: OK.

## Test-file portability checks

| File | Requirement | Result |
|---|---|---|
| `tests/test_candidate_lab_routes.py` | no `U:/project2` hardcoded paths | PASS (none found) |
| `tests/test_hybrid_retrieval.py` | skip if faiss-cpu missing | PASS (`pytest.skip("faiss-cpu not installed", allow_module_level=True)` at line 42) |
| `tests/test_preset_plumbing.py` | isolate real `.env` leakage | PASS (`monkeypatch.delenv` + `setattr` for dotenv path, lines 52–68) |
| `waggledance/__init__.py` | matches `pyproject.toml` 3.5.7 | PASS (`__version__ = "3.5.7"`) |

## Targeted pytest runs

| Test module | Passed | Failed | Duration |
|---|---|---|---|
| `tests/test_candidate_lab_routes.py` | 11 | 0 | 0.34 s |
| `tests/test_hybrid_retrieval.py` | 40 | 1 | 0.42 s |
| `tests/test_preset_plumbing.py` | 23 | 0 | 0.20 s |
| `tests/test_phase7_hologram_news_wire.py` | 20 | 0 | 0.50 s |

## Full suite

```
.venv/Scripts/python.exe -m pytest tests/ -q --tb=line -p no:warnings --ignore=tests/e2e
```

- **5666 passed**
- **1 failed** (pre-existing — see below)
- **3 skipped**
- Duration: **715.32 s (≈ 11 m 55 s)**

Exit code: 0 (pytest reports non-zero only with `-x` or uncaught errors; failures are counted but run continues by default).

Note: `tests/e2e` excluded because the e2e harness would try to bind port 8002 (held by the running campaign).

## Pre-existing failure

### `tests/test_hybrid_retrieval.py::TestFeatureFlag::test_enabled_returns_hybrid`

```
E   AssertionError: assert 'hybrid:shadow' == 'hybrid'
```

**Root cause (not this branch):**

`waggledance/application/services/hybrid_retrieval_service.py:195` sets:

```python
trace.retrieval_mode = f"hybrid:{self._mode}"
```

The `self._mode` field was introduced during Phase D-1/D-2 hybrid-retrieval activation (2026-04-23, commit `3d0bd9f` and follow-ups). The test was written pre-D-1 when `retrieval_mode` was just `"hybrid"`. The test expectation was not updated when the runtime label gained the `:<mode>` suffix.

**Proof it is pre-existing:**

- `git diff main..HEAD -- tests/test_hybrid_retrieval.py` → empty (this branch has no changes to this file)
- `git log --oneline main -5 -- tests/test_hybrid_retrieval.py` → last three touches are `145ab60`, `b682190`, `a731f37`, all on `main` before the phase8 branch was created
- Runtime code path is unmodified on this branch

**Treatment:** left as-is on this branch. Fixing it would require deciding whether the test expectation or the runtime label is canonical — that is a Phase D-2/D-3 scope decision, not Phase 8 scaffolding scope. Tracked as a known carry. If Phase D ever flattens the mode suffix back, or if the test is updated to accept `"hybrid:*"`, this will go green automatically.

## Skipped tests

3 skips, all expected-capability skips (e.g. optional dependency guards). None look suspicious.

## Campaign concurrency

The `ui_gauntlet_400h` campaign was running throughout:
- Server PID = `start_waggledance.py --port 8002` (listener process)
- Watchdog + auto-commit + 3 harnesses alive
- Running suite did not disturb the server (no port conflict, no health failures observed in `watchdog.log` during the 715 s window)

RAM footprint after recent zombie-reap fix: ~1.1 GB for all python processes (down from 21.4 GB pre-fix).

## Verdict

- Full suite green save for 1 documented pre-existing failure.
- No regression introduced by this branch.
- Infrastructure (version files, portability guards) meets x.txt Phase 0 requirements.

Proceeding to Phase 1.
