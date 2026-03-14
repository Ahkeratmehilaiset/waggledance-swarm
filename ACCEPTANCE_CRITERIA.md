# Acceptance Criteria — WaggleDance Refactor Sprint

All criteria must be GREEN before the refactor is considered complete.

**Status: ALL 27 CRITERIA GREEN (2026-03-14)**

## Automated checks

1. [x] `python -m pytest tests/unit/ -v` — all green (97 passed)
2. [x] `python -m pytest tests/unit_core/ -v` — all green (37 passed)
3. [x] `python -m pytest tests/unit_app/ -v` — all green (16 passed)
4. [x] `python -m pytest tests/contracts/ -v` — all green (22 passed)
5. [x] `Container(settings=WaggleSettings.from_env(), stub=True).build_app()` — no crash
6. [x] New `/api/chat` returns response in stub mode
7. [x] `/ready` works even if `dashboard/dist` does not exist
8. [x] Old `main.py` still works (non-destructive smoke)
9. [x] Old `start.py` still works (non-destructive smoke)
10. [x] `tools/waggle_backup.py --tests-only` — all 72 suites green (946 ok, 0 fail)

## Architecture checks

11. [x] No `os.environ` / `os.getenv` / `dotenv` reads outside `WaggleSettings`
12. [x] No forbidden imports in `waggledance/core/` or `waggledance/application/`
13. [x] Every persistent write path has exactly one owner per `STATE_OWNERSHIP.md`
14. [x] All port implementations match `PORT_CONTRACTS.md` signatures exactly
15. [x] `routing_policy.select_route()` only returns route types from `ALLOWED_ROUTE_TYPES`

## Production bug regression checks

16. [x] No code path can assign incompatible type to a typed attribute (prevents `is_training_due` bug)
17. [x] Every `asyncio.create_task()` in new code has error handling (prevents silent task failures)
18. [x] `OllamaAdapter` timeout is >=120s (prevents embed timeout under load)
19. [x] `LearningService` has convergence stall detection (prevents infinite empty cycles)

## Code quality gates

20. [x] `python -m compileall waggledance/` — all green (no syntax errors)
21. [x] `grep -rn 'TODO|FIXME' waggledance/ --include='*.py'` returns zero matches
22. [x] No raw `asyncio.create_task(` without `_track_task` or `TaskGroup` wrapper in new code
23. [x] Non-stub `Container(settings, stub=False)` constructs all required adapters without in-memory fallbacks
24. [x] `memory_repository` in non-stub mode is NOT `InMemoryRepository` (is ChromaMemoryRepository)
25. [x] New app startup hook initializes required resources without crash (lifespan startup)
26. [x] New app shutdown hook cleanly closes external clients/resources (lifespan shutdown)
27. [x] Event bus failure counter is incremented when a handler raises (get_failure_count)
