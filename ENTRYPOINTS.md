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

### `main.py` — Full HiveMind Runtime

```bash
python main.py
```

Runs the complete legacy system: HiveMind, 20+ agents, FAISS, bilingual cache, night learning, dashboard. Production-proven but monolithic.

### `start.py` — Interactive Launcher

```bash
python start.py              # interactive menu
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
| Agent spawning | Yes (20+ agents) | Not yet (future work) |
| Dashboard API | 40+ endpoints | 5 endpoints (health, ready, chat, memory) |
| Night learning | Yes | Not yet (future work) |
| FAISS | Yes | Not yet (future work) |
| Test coverage | 946 tests (72 suites) | 172 tests (unit/core/app/contracts) |

---

## When to Use Which

| Scenario | Entrypoint |
|----------|-----------|
| Production (full features) | `main.py` (until new runtime catches up) |
| Development / testing new architecture | `start_runtime.py --stub` |
| 24h validation of new stack | `start_runtime.py` (non-stub) |
| Dashboard development | `start.py --stub` (legacy, starts React dev server) |
| CI/CD smoke tests | `start_runtime.py --stub` + pytest |
