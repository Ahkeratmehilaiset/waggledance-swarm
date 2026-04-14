# 400h Campaign — Truth Intake

- **Date:** 2026-04-13 09:28 UTC
- **Campaign ID:** `ui_gauntlet_400h_20260413_092800`

## Git State

| Key | Value |
|---|---|
| Branch | `main` |
| HEAD | `145ab60` (fix: resolve CI test failures) |
| Origin/main | `0222058` (1 local-only commit not yet pushed) |
| Latest tag | `v3.5.7` → `88c91db` |
| pyproject.toml version | 3.5.7 |
| `__init__.py` version | 3.5.7 |
| Worktree | Clean (untracked: soak artifacts, gauntlet reports, screenshots) |
| Hardening branch | `hardening/post-v3.5.7-ui-gauntlet` → `8c58e6d` (merged to main) |
| PRODUCT diff on any branch | EMPTY — no runtime code changes since v3.5.7 tag |

## Current CI State

| Workflow | Commit | Result |
|---|---|---|
| Tests (tests.yml) | `0222058` (origin/main) | FAILURE |
| WaggleDance CI (ci.yml) | `0222058` (origin/main) | FAILURE |

CI failures are pre-existing. Root causes identified and fixed locally in `145ab60`:
1. `test_candidate_lab_routes.py` — hardcoded `U:/project2/` paths → `Path(__file__)` relative
2. `test_hybrid_retrieval.py` — missing `faiss-cpu` in CI → `pytest.skip` guard
3. `test_preset_plumbing.py` — `.env` leaking `WAGGLE_PROFILE=COTTAGE` → monkeypatched `_DEFAULT_DOTENV_PATH`
4. `waggledance/__init__.py` — `__version__` was `3.5.6`, should be `3.5.7`

Local test suite: **5580 passed, 3 skipped, 0 failed**.
CI fix commit `145ab60` not yet pushed (credential manager blocked in non-interactive shell).

## Known Carries

| Carry | Classification | Impact |
|---|---|---|
| Voikko DLL missing | INFRA | Cosmetic `__del__` traceback at startup, no runtime impact |
| `rss_ha_blog` DNS blocked | INFRA/ENV | Permanent `idle` state, displays honestly via NEWS-003 |
| Ollama embed timeout warnings | INFRA | Occasional, no data loss |
| `networkidle` on hologram | HARNESS | WebSocket/SSE prevents networkidle; use `domcontentloaded` |
| DOM/WS buildup after many chat messages | HARNESS | Context recycling mitigates |
| GitHub Actions Node.js 20 deprecation | CI/WORKFLOW | Warning only, not blocking |

## Previous Verification Summary

| Evidence | Result | Source |
|---|---|---|
| Phase 7 tests | 20/20 pass | `tests/test_phase7_hologram_news_wire.py` |
| Phase 9 final verify | 5378/0 tests, 15/15 endpoints | `PHASE9_FINAL_VERIFY.md` |
| 12h soak (first) | 306/306 ticks, 10.24h (host suspend) | `OVERNIGHT_SOAK_FINAL.md` |
| 12h soak (recheck) | 358/358 ticks, 11.99h | `overnight_soak_recheck.md` |
| UI gauntlet | 477 queries, 466 active, 0 XSS, 0 breaks | `ui_gauntlet_20260412/summary.md` |
| UI fidelity | 33/33 pass | `ui_gauntlet_20260412/ui_fidelity_baseline.md` |
| Fault drills | 6/7 pass, 1 inconclusive | `ui_gauntlet_20260412/fault_drills.md` |
| Mixed soak | 30 min stable | `ui_gauntlet_20260412/mixed_soak.md` |
| Docs sync | README, CHANGELOG, API.md updated | `release_followup_final.md` |

## Release Body / Docs Staleness

- GitHub release body for v3.5.7 exists (ID 307972297) but does not include post-release hardening summary
- `gh` CLI not installed → release body update is a pending manual step
- README, CHANGELOG, API.md were synced in `8c58e6d` (merged to main)

## Server State

- Port 8002: live server responding (HTTP 200 on `/health`)
- Ollama: running on 11434
