# Acceptance Criteria — WaggleDance Refactor Sprint

All criteria must be GREEN before the refactor is considered complete.

## Automated checks

1. [ ] `python -m pytest tests/unit/ -v` — all green
2. [ ] `python -m pytest tests/unit_core/ -v` — all green
3. [ ] `python -m pytest tests/unit_app/ -v` — all green
4. [ ] `python -m pytest tests/contracts/ -v` — all green
5. [ ] `Container(settings=WaggleSettings.from_env(), stub=True).build_app()` — no crash
6. [ ] New `/api/chat` returns response in stub mode
7. [ ] `/ready` works even if `dashboard/dist` does not exist
8. [ ] Old `main.py` still works (non-destructive smoke)
9. [ ] Old `start.py` still works (non-destructive smoke)
10. [ ] `tools/waggle_backup.py --tests-only` — all 72 suites green

## Architecture checks

11. [ ] No `os.environ` / `os.getenv` / `dotenv` reads outside `WaggleSettings`
12. [ ] No forbidden imports in `waggledance/core/` or `waggledance/application/`
13. [ ] Every persistent write path has exactly one owner per `STATE_OWNERSHIP.md`
14. [ ] All port implementations match `PORT_CONTRACTS.md` signatures exactly
15. [ ] `routing_policy.select_route()` only returns route types from `ALLOWED_ROUTE_TYPES`

## Production bug regression checks

16. [ ] No code path can assign incompatible type to a typed attribute (prevents `is_training_due` bug)
17. [ ] Every `asyncio.create_task()` in new code has error handling (prevents silent task failures)
18. [ ] `OllamaAdapter` timeout is >=120s (prevents embed timeout under load)
19. [ ] `LearningService` has convergence stall detection (prevents infinite empty cycles)

## Code quality gates

20. [ ] `python -m compileall waggledance/` — all green (no syntax errors)
21. [ ] `grep -rn 'TODO\|FIXME\|\.\.\.' waggledance/ --include='*.py'` returns zero matches in implementation files (stubs in Protocol classes excluded)
22. [ ] No raw `asyncio.create_task(` without `_track_task` or `TaskGroup` wrapper in new code
23. [ ] Non-stub `Container(settings, stub=False)` constructs all required adapters without in-memory fallbacks
24. [ ] `memory_repository` in non-stub mode is NOT `InMemoryRepository`
25. [ ] New app startup hook initializes required resources without crash
26. [ ] New app shutdown hook cleanly closes external clients/resources
27. [ ] Event bus failure counter is incremented when a handler raises
