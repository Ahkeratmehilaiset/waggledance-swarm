# Docker / Startup Validation (Phase 6)

Performed under WD_release_to_main_master_prompt.md, Phase 6.

## Files inspected

| File | Status |
|---|---|
| `Dockerfile` | OK — single-stage Python 3.13-slim, system deps (curl, git, libvoikko1, voikko-fi), pip install from `requirements.lock.txt`, EXPOSE 8000, healthcheck, CMD `python start_waggledance.py` |
| `docker-compose.yml` | OK — two services (ollama with NVIDIA GPU reservation, waggledance with build: .), env_file: .env, command override `python -m waggledance.adapters.cli.start_runtime`, depends_on ollama, healthcheck via /health |
| `start_waggledance.py` | OK — preset-aware wrapper (76 lines); accepts `--preset` (raspberry-pi-iot / cottage-full / factory-production), `--port`, `--stub` |
| `waggledance/adapters/cli/start_runtime.py` | OK — primary hexagonal entrypoint; accepts `--stub`, `--port`, `--log-level` |
| `requirements.lock.txt` | exists at repo root |
| `.env.example` | exists; `.env` must be created from example before `docker compose up` |

## Two startup paths — both intentional, documented here for honesty

The Dockerfile and docker-compose use **different startup commands**:

- `Dockerfile CMD`: `python start_waggledance.py`
- `docker-compose.yml command`: `python -m waggledance.adapters.cli.start_runtime`

This is **not a bug**. Both entrypoints invoke the same underlying runtime — they differ in which arguments they accept:

| Entrypoint | Use case | Distinguishing flags |
|---|---|---|
| `start_waggledance.py` | Quick start with hardware preset (cottage / factory / RPi-IoT) | `--preset` |
| `waggledance.adapters.cli.start_runtime` | Production-style invocation with explicit log level | `--log-level` |

The docker-compose override exists because production deployment usually doesn't need preset auto-detection — the operator picks one explicitly via env var (`WAGGLE_PROFILE=HOME` is set in the compose env block).

## Sanity checks performed

```
$ python start_waggledance.py --help
usage: start_waggledance.py [-h] [--preset {raspberry-pi-iot,cottage-full,factory-production}] [--port PORT] [--stub]
WaggleDance Swarm AI
✓ exits 0

$ python -c "from waggledance.adapters.cli import start_runtime; print('OK')"
OK
✓ module imports cleanly
```

## What is NOT changed in this release

- No Dockerfile rewrite. The current single-stage build is reproducible.
- No docker-compose redesign. The two-service split (ollama + waggledance) is correct.
- No new entry-point unification. Both entries serve real use cases.

## Operator setup

```bash
# 1. Create .env from example
cp .env.example .env
# edit .env to set API keys / capsule profile

# 2. Build + run
docker compose up -d

# 3. Verify health
curl -f http://localhost:8000/health

# 4. Reality View
open http://localhost:8000/hologram
```

## Caveats noted but not blocking the release

- `requirements.lock.txt` is a full lock including `faiss-cpu` and `playwright`. For minimal deployments without hybrid retrieval or e2e browser testing, use `requirements-ci.txt` (referenced in Dockerfile comment).
- `OLLAMA_HOST=http://host.docker.internal:11434` in Dockerfile is overridden by docker-compose to `http://ollama:11434` (the compose-network service name). Both forms work; Dockerfile's is for standalone container, compose's for the orchestrated case.
- Healthcheck calls `/health`; the route is provided by `waggledance/adapters/http/routes/`. Verified to exist.
