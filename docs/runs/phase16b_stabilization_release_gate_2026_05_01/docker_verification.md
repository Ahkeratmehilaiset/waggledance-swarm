# Phase 16B P6 — Docker end-to-end truth

## Environment

| field | value |
|---|---|
| `docker version` | **command not found** — Docker is not installed/available in this Phase 16B development shell |
| `Dockerfile` | present and inspected |
| `docker-compose.yml` | present and inspected |
| `.dockerignore` | present and inspected |
| changed in this session? | **no** — Phase 16B does not modify `Dockerfile`, `docker-compose.yml`, or `.dockerignore` |

## What was actually verified

Per the master prompt's bounded-timebox rule (max two focused fix attempts; do not consume the whole session debugging Docker), and given that **Docker is not available locally**, the following bounded verification was performed:

* `Dockerfile` inspected: `FROM python:3.13-slim`, copies `requirements.lock.txt` and runs `pip install --no-cache-dir -r requirements.lock.txt`, then copies app code, exposes 8000, default `CMD ["python", "start_waggledance.py"]`. Build is self-contained; no provider credentials required at build or runtime for the inner-loop autonomy proofs.
* `docker-compose.yml` inspected: present (boot legacy webserver). Not used by the autonomy proofs.
* `.dockerignore` inspected: excludes `.git`, `.github`, `.claude`, Python caches, `.venv`, `data/`, `logs/`. The two autonomy-proof tools and the autonomy_growth tests are NOT excluded — they live under `tools/` and `tests/` which are copied into the image.

### Phase 16B-aware Docker docs

`docs/deployment/DOCKER_QUICKSTART.md` will be updated in P9 with two new commands (kept as **not tested in this session**):

* `docker run --rm -v "$(pwd)/docs/runs/phase16b_stabilization_release_gate_2026_05_01:/app/docs/runs/phase16b_stabilization_release_gate_2026_05_01" waggledance:phase16b python tools/run_full_restart_continuity_proof.py` — Phase 16B P2 full-corpus restart proof inside Docker.
* `docker run --rm waggledance:phase16b python tools/run_phase16b_proof_soak.py --iterations 5` — Phase 16B P3 soak inside Docker.

## Stable gate disposition

**g01 (Docker end-to-end proof tested) — FAIL / NOT VERIFIED.**

Per the master prompt's `DOCKER STABLE GATE` rule:

> Docker gate passes for stable only if ALL are true:
> 1. `docker build` succeeds from clean checkout.
> 2. Container runs without host-mounted source hacks except documented read/write output dir.
> 3. Inside container, at least one latest proof command passes.
> 4. Container result records `provider_jobs_delta == 0` and `builder_jobs_delta == 0`.
> 5. Docker docs contain the exact command that was tested.
> 6. Docker image does not require provider credentials for inner-loop proof.

Criterion 1 cannot be verified because Docker is unavailable. Criteria 2–5 therefore cannot be verified either. Criterion 6 is true in principle (Dockerfile runs no provider calls) but not proven by execution.

**Effect on release gate:** v3.8.0 stable is **blocked** by g01 unless an external operator with Docker re-verifies and amends RELEASE_READINESS.

**Effect on prerelease:** Docker not being verified does NOT block `v3.7.6-stabilization-alpha`. Per the master prompt's `Time/complexity rule`:

> If Docker fix exceeds two focused correction attempts, stop Docker work and document blocker. Do not let Docker debugging consume the whole session. Docker failure blocks v3.8.0 stable, but can still allow v3.7.6-stabilization-alpha.

## Recommended Docker verification path for an external operator

```bash
# On a machine with Docker Engine 24+ or Docker Desktop 4.29+
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout main
docker build -t waggledance:phase16b .
docker run --rm waggledance:phase16b python tools/run_upstream_structured_request_proof.py
docker run --rm waggledance:phase16b python tools/run_full_restart_continuity_proof.py
docker run --rm waggledance:phase16b python -m pytest tests/autonomy_growth/ -q
```

Expected per-command result is documented in `docs/deployment/DOCKER_QUICKSTART.md` (P9 update).

## Conclusion

* Docker docs (`Dockerfile`, `compose`, `.dockerignore`) inspected and unchanged.
* Docker build/run not tested in this session because Docker is not installed.
* g01 stable gate is **NOT VERIFIED** — blocks v3.8.0 stable per fail-closed rule.
* g01 does not block `v3.7.6-stabilization-alpha`.
