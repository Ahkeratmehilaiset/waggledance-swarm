# Docker Quickstart - WaggleDance

This document describes the current Docker truth boundary for
WaggleDance.

Current release posture:

* Latest stable GitHub release remains `v3.8.0`.
* Latest R21 prerelease image is
  `ghcr.io/ahkeratmehilaiset/waggledance:v3.11.0-r20-axis-b-activated-alpha`.
* R22/R23 changes are on main as stable-candidate substrate, with no
  `pyproject.toml` version bump.
* v3.12.0 stable is targeted for 2026-05-24 or later, after R22.5 soak
  and promotion gates pass.
* Docker Hub is not used yet. GHCR is the primary registry.

## Prerequisites

* Docker Engine 24+ or Docker Desktop 4.29+.
* No cloud account is required for Profile S / offline proofs.
* Optional Ollama integration expects an already-running host Ollama
  service at `OLLAMA_HOST=http://host.docker.internal:11434`.
* Profile L cloud use is opt-in and requires provider credentials plus
  the redaction/budget gates.

## Build Locally

```bash
docker build -t waggledance:local .
```

The Dockerfile uses `python:3.13-slim` and installs the Linux-portable
CI dependency subset from `requirements-ci.txt`. The Windows/CUDA lock
file (`requirements.lock.txt`) is not the Docker install source.

## Run The Current Web Runtime

```bash
docker run --rm -p 8000:8000 waggledance:local
```

The current Dockerfile default command is:

```bash
python start_waggledance.py
```

That boots the legacy/hexagonal web runtime and dashboard. The R22.5
release-surface audit noted that the Dockerfile CMD, docker-compose
command, and `pyproject.toml` console script are not yet fully
canonicalized. This is not treated as a current release blocker, but it
must be resolved before the v3.12.0 stable Docker surface is finalized.

## Pull The R21 Prerelease Image

```bash
docker pull ghcr.io/ahkeratmehilaiset/waggledance:v3.11.0-r20-axis-b-activated-alpha
```

Smoke Profile S behavior:

```bash
docker run --rm -e WAGGLE_PROFILE=small \
  ghcr.io/ahkeratmehilaiset/waggledance:v3.11.0-r20-axis-b-activated-alpha \
  python -c "import sys; from waggledance.core.bridge_llm import BridgeLLMClient, BridgeLLMRedactor; leaked=[n for n in ('anthropic','openai','ollama') if n in sys.modules]; assert not leaked, leaked; assert not BridgeLLMClient.disabled('profile_s').is_enabled(); assert BridgeLLMRedactor().redact('alice@example.org').applied; print('SMOKE_OK')"
```

Expected output:

```text
SMOKE_OK
```

## Reproduce Solver Scale Locally

The solver-scale proof uses synthetic deterministic descriptors. It is
not a claim about a 10k human-authored corpus.

```bash
python tools/run_solver_scale_proof.py \
  --out-dir .codex-audit/docker_readme_solver_scale_10k \
  --descriptors 10000 \
  --lookup-pass-count 1000
```

Latest local claim audit on this laptop:

* 10,000 descriptors: 1,000/1,000 capability hits, zero FIFO fallback,
  zero miss, warm p99 0.0497 ms.
* 50,000 descriptors: 2,000/2,000 capability hits, zero FIFO fallback,
  zero miss, warm p99 0.2198 ms.
* 50,000 cold lookup p99 was 354.8117 ms, so do not claim all 50k
  lookup paths are low-latency.

## Reproduce Mined Solver Dispatch

```bash
python tools/run_phase18c_mined_solver_runtime_dispatch_proof.py \
  --out-dir .codex-audit/docker_readme_mined_solver_dispatch
```

Expected current shape:

* 30 runtime signals -> 14 candidates.
* 6 allowlisted candidates registered.
* 8 non-allowlisted candidates rejected.
* 18/18 dispatch cases pass across the six low-risk families.
* Provider jobs delta = 0.
* Builder jobs delta = 0.

## Reproduce 2D Branch-Isolation Baseline

```bash
python tools/run_branch_isolation_benchmark.py \
  --db .codex-audit/docker_readme_branch_isolation.sqlite \
  --out-json .codex-audit/docker_readme_branch_isolation.json \
  --repeats 3 \
  --probe-events 200 \
  --hot-events 1000 \
  --uniform-events-per-branch 80 \
  --cold-flood-events-per-branch 120
```

This benchmark is a bottleneck detector. It does not prove sharding or
3D topology. Latest local audit:

* idle p99: 10.5245 ms
* single-hot cross-branch p99: 29.5368 ms (2.806x degradation)
* adversarial cold-flood p99: 128.5763 ms (12.217x degradation)

## Compose

```bash
docker compose up -d
```

Compose boots the current web runtime. It is not the stable R22.5
promotion workflow and does not move `:latest`.

### Vector backend under Compose

The compose service sets:

```yaml
- WAGGLE_VECTOR_BACKEND=${WAGGLE_VECTOR_BACKEND-inmemory}
- CHROMA_DIR=${CHROMA_DIR-/app/data/chroma_data}
```

The **single dash** is required, and the distinction from `:-` is the
whole point:

| Your environment | Container receives | Result |
| --- | --- | --- |
| unset | `inmemory` | non-persistent; the documented Compose default |
| `WAGGLE_VECTOR_BACKEND=chroma` | `chroma` | preserved; needs a derived image (below) |
| `WAGGLE_VECTOR_BACKEND=` (empty) | empty | **preserved and rejected at startup** |

`${VAR:-default}` would treat that explicit empty value as unset and
silently select the non-persistent backend. `${VAR-default}` defaults only
when the variable is genuinely **unset**, so an empty value survives and
the runtime refuses it as an unknown backend. Silent selection of a
non-persistent store is exactly what the fail-closed contract forbids.

A hard `WAGGLE_VECTOR_BACKEND=inmemory` entry would be wrong for a
different reason: values under `environment:` take precedence over
`env_file:`, so a literal pin would override an operator who asked for
`chroma` without any error at all.

`tests/test_chroma_opt_in_truth.py` rejects both the literal pin and the
`:-` form.

### Chroma under Docker requires a derived image

Setting `WAGGLE_VECTOR_BACKEND=chroma` is **not sufficient** in Docker.
The image installs `requirements-ci.txt`, which intentionally omits
`chromadb`, so the runtime fail-closes at startup with a `RuntimeError`
naming the install command. Env alone cannot conjure the package.

To run Chroma in Docker, build a derived image that adds the extra from
the **checkout** (not the package index, so you install the code you are
actually running):

```dockerfile
FROM waggledance:local
RUN pip install --no-cache-dir .[chroma]
```

```bash
WAGGLE_VECTOR_BACKEND=chroma docker compose up -d
```

**Where the store lands.** The runtime default `CHROMA_DIR` is
`./chroma_data`, which under `WORKDIR /app` resolves to `/app/chroma_data`
— a path **no** compose volume covers, so the store would be lost on
container replacement. The compose service therefore sets
`CHROMA_DIR=/app/data/chroma_data`, which sits inside the existing
`./data:/app/data` bind mount and survives replacement. If you override
`CHROMA_DIR`, keep it under a mounted path.

### Plain `docker run`

`docker run` does not read `docker-compose.yml`, so it inherits no
default from it. With no `WAGGLE_VECTOR_BACKEND` set, the runtime default
is `chroma`, and on the stock image that **fail-closes** at startup. Pass
the backend explicitly:

```bash
docker run --rm -p 8000:8000 -e WAGGLE_VECTOR_BACKEND=inmemory waggledance:local
```

## Profiles

* **Profile S**: fully offline / local-first. No cloud provider SDKs
  should leak into import state on the disabled path.
* **Profile L**: opt-in cloud path. PII redaction is required before
  provider dispatch; `AcceptPiiToCloud=False` is the hard default.

## Known Limits

* Stable Docker `:latest` still points at the existing stable line until
  R22.5 promotion explicitly moves it.
* Docker Hub is not configured.
* The canonical v3.12.0 Docker entrypoint is still an R22.5 decision.
* ARM builds are not verified.
* Persistent runtime volumes are operator-managed; the default container
  filesystem is ephemeral.
* Compose injects `inmemory` when the variable is unset, so semantic
  memory is **not persistent** out of the box. That is Compose supplying a
  value, not the runtime falling back. Chroma in Docker needs a derived
  image with the `[chroma]` extra and a volume-backed `CHROMA_DIR`, not
  just an environment variable.
* 3D hex topology and per-cell DB sharding are not part of this Docker
  surface.

## Historical Docker Evidence

The first stable Docker contract was verified in Phase 16F:

* Docker Desktop 4.71.0 / Engine 29.4.1.
* `--network none`.
* Corpus 104.
* All four canonical proofs and the autonomy_growth smoke suite passed
  in the rebuilt image.

Historical evidence remains under:

* `docs/runs/phase16f_docker_stable_gate_2026_05_03/docker_build.md`
* `docs/runs/phase16f_docker_stable_gate_2026_05_03/docker_runtime_proofs.md`
