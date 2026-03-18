# Entrypoints — WaggleDance AI

**Updated:** 2026-03-14

---

## Primary Entrypoint (Recommended)

```bash
# Production (requires Ollama + ChromaDB)
python -m waggledance.adapters.cli.start_runtime

# Stub mode (no external dependencies)
python -m waggledance.adapters.cli.start_runtime --stub

# Custom host/port
python -m waggledance.adapters.cli.start_runtime --host 127.0.0.1 --port 9000

# Debug logging
python -m waggledance.adapters.cli.start_runtime --stub --log-level debug
```

If installed via `pip install -e .`:

```bash
waggledance --stub
waggledance --port 9000 --log-level info
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--stub` | `False` | Use in-memory adapters (no Ollama/ChromaDB) |
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8000` | Listen port |
| `--log-level` | `warning` | Logging level (debug/info/warning/error/critical) |

### What It Runs

- `WaggleSettings.from_env()` — loads config from env vars / `.env`
- `Container(settings, stub=)` — wires all dependencies via DI
- `container.build_app()` — creates FastAPI with routes, middleware, lifespan
- `uvicorn.run()` — serves the application

### What It Does NOT Run (Yet)

- Agent spawning (HiveMind agents are not part of the new architecture yet)
- FAISS / bilingual / fi_fast cache population
- Night learning heartbeat loop
- Dashboard React build

These features remain in the legacy entrypoint (`main.py`) until ported.

---

## Legacy Entrypoints (Deprecated)

### `main.py` — Full HiveMind Runtime (DEPRECATED)

```bash
python main.py  # emits DeprecationWarning at startup
```

Runs the complete legacy system: HiveMind, 20+ agents, FAISS, bilingual cache, night learning, dashboard. Production-proven but monolithic. **DEPRECATED** — use `start_runtime.py` instead.

### `start.py` — Interactive Launcher (DEPRECATED)

```bash
python start.py              # emits DeprecationWarning at startup
python start.py --stub       # legacy stub mode
python start.py --production # legacy production mode
python start.py --new-runtime # launches the new runtime
```

Interactive menu with 4 options:
1. STUB mode (legacy)
2. PRODUCTION mode (legacy)
3. NEW RUNTIME (recommended) — launches `start_runtime.py`
4. Change PROFILE

---

## Architecture Comparison

| Feature | Legacy (`main.py`) | New (`start_runtime.py`) |
|---------|-------------------|--------------------------|
| Architecture | Monolith (hivemind.py) | Hexagonal (ports & adapters) |
| DI container | None (manual wiring) | `Container` with `cached_property` |
| Testability | Needs live services | In-memory stubs, <1s tests |
| Agent spawning | Yes (128 agents) | Via CompatibilityLayer |
| Dashboard API | 40+ endpoints | 12 endpoints (health, ready, chat, memory, autonomy) |
| Night learning | Yes | Yes (NightLearningPipeline v2) |
| Autonomy runtime | No | Yes (solver-first, 29 capabilities) |
| Test coverage | 1468 tests (79 suites) | 3836 pytest tests |

---

## When to Use Which

| Scenario | Entrypoint |
|----------|-----------|
| Production (recommended) | `start_runtime.py` (autonomy runtime, primary) |
| Production (legacy fallback) | `main.py` (DEPRECATED — HiveMind orchestrator) |
| Development / testing | `start_runtime.py --stub` |
| Dashboard development | `start.py --stub` (DEPRECATED — starts React dev server) |
| CI/CD smoke tests | `start_runtime.py --stub` + pytest |
