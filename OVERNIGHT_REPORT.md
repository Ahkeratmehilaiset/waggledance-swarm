# Overnight Hardening Report — 2026-03-19

## Summary
- Cycles completed: 6
- Bugs fixed: 3
- Tests at start: 3836 passed, 0 failed
- Tests at end: 3836 passed, 0 failed
- Duration: ~25 min (6 cycles, each with full pytest run)
- Idle cycles: 3 consecutive (triggered stop)

## Fixes Applied

1. **7948e60** — `pyproject.toml`: Added PyPI classifiers (Apache license, Python 3.13, OS Independent)
2. **870a3ec** — `tools/restore.py`: Fixed env template path mismatch. restore.py looked for `env.template` but repo has `.env.example`. Now checks both names.
3. **0d3cd86** — `core/meta_learning.py`: Marked stale `from backend.routes.chat import _YAML_INDEX` as legacy-only. Import always fails silently (backend is deprecated architecture).

## Proactive Checks Performed (all passed)

- MIT license references: none found in .md/.toml
- Individual test dir failures: none
- `python -m compileall waggledance/ core/`: clean, no syntax issues
- `python -c "import waggledance"`: clean, no ImportErrors
- Dashboard build (`npm run build`): success (674ms)
- End-to-end runtime test (`2+2` query): gold path, 18ms
- Multi-query test (math, thermal, retrieval): all 5 executed correctly
- ResourceKernel start/stop: clean
- NightLearningPipeline init with defaults: clean
- All YAML configs parseable: 100%
- All 13 BUSL-protected files have correct header
- No circular imports in waggledance modules
- No missing `__init__.py` in packages
- No bare except in waggledance/core or core/
- No print() calls in waggledance/core library code
- No hardcoded passwords
- .env properly in .gitignore
- Dockerfile + docker-compose.yml consistent with v2.0

## Issues Found But Not Fixed (too large / too risky)

1. **Benchmark output filename stale** — `tools/run_benchmark.py` outputs `benchmark_v1_18.json` but we're on v2.0. CI artifact path matches the script, so no breakage, but naming is confusing. Fix: rename in both script and `.github/workflows/tests.yml` (2 files, low risk, but purely cosmetic).

2. **4 DeprecationWarnings from third-party libraries** — faiss SWIG modules (`SwigPyPacked`, `SwigPyObject`, `swigvarlink`) have no `__module__` attribute. Not actionable — upstream faiss issue. Unsloth import order warning is also third-party.

## Recommended Next Steps

1. Rename benchmark output files from `v1_18` to `v2_0` (script + CI)
2. Consider adding `py.typed` marker for PEP 561 type checking support
3. Consider adding `[project.urls]` to pyproject.toml (homepage, repository, issues)
4. The codebase is in excellent shape — 3836 tests, 0 failures, all proactive checks pass

## Test Count Progression
| Cycle | Passed | Failed | Fix |
|-------|--------|--------|-----|
| 0 (baseline) | 3836 | 0 | — |
| 2 | 3836 | 0 | Add PyPI classifiers |
| 3 | 3836 | 0 | Fix restore.py env template path |
| 4 | 3836 | 0 | Mark stale backend import |
| 5 | 3836 | 0 | (idle — proactive checks) |
| 6 | 3836 | 0 | (idle — stop triggered) |
