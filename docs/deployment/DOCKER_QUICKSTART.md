# Docker Quickstart — WaggleDance (alpha)

This document describes how Docker is **expected** to work for the WaggleDance autonomy runtime and proof scripts. The `Dockerfile` and `docker-compose.yml` shipped in this repository are inherited from earlier phases; Phase 15 does not change them.

**Tested in this session:** No. Docker is not available in the Phase 15 development shell. The build / run commands below are the documented contract; an external operator should verify on their own machine before treating Docker as production-ready.

If a step below fails on your machine, the `Dockerfile` is the authoritative source. File an issue with the failing step + your `docker version` output.

## Prerequisites

* Docker Engine 24+ or Docker Desktop 4.29+.
* If you want the optional Ollama integration, run Ollama on the host (`ollama serve`); the container reaches it via `OLLAMA_HOST=http://host.docker.internal:11434` (default in the Dockerfile).
* No cloud account is required. The Phase 15 inner-loop autonomy proof runs **without any provider credentials** and writes only to a scratch SQLite under `docs/runs/phase15_runtime_hints_release_2026_05_01/`.

## Build the image

```
docker build -t waggledance:phase15-alpha .
```

The build pulls `python:3.13-slim` plus a small APT layer (`curl`, `git`, `libvoikko1`, `voikko-fi`) and installs Python deps from `requirements.lock.txt`. Expected build time: 3–8 minutes on a clean cache, depending on network and CPU.

## Run the Phase 15 automatic runtime hint proof

```
docker run --rm -v "$(pwd)/docs/runs/phase15_runtime_hints_release_2026_05_01:/app/docs/runs/phase15_runtime_hints_release_2026_05_01" \
    waggledance:phase15-alpha \
    python tools/run_automatic_runtime_hint_proof.py
```

What this should produce:

* Console summary ending with `Wrote /app/docs/runs/phase15_runtime_hints_release_2026_05_01/automatic_runtime_hint_proof.json`.
* Updated `automatic_runtime_hint_proof.json` on the host (via the volume mount).

Expected key fields in the JSON:

| field | expected |
|---|---|
| `selected_caller` | `"waggledance.core.autonomy.runtime.AutonomyRuntime.handle_query"` |
| `corpus_total` | 98 |
| `manual_low_risk_hint_in_input_detected` | `false` |
| `proof_constructed_runtime_query_objects` | `false` |
| `hints_derived_total` | 98 |
| `before.served_total` | 0 |
| `after.served_via_capability_lookup_total` | 98 |
| `kpis.provider_jobs_delta_during_proof` | 0 |
| `kpis.builder_jobs_delta_during_proof` | 0 |

## Run the targeted test suite

```
docker run --rm waggledance:phase15-alpha \
    python -m pytest tests/autonomy_growth/ tests/storage/ tests/ui_hologram/ -q
```

Expected: 0 failures across the autonomy_growth / storage / ui_hologram suites at the head of `phase15` work.

## Run the legacy webserver (optional, NOT required for the autonomy proof)

```
docker run --rm -p 8000:8000 waggledance:phase15-alpha
```

The default `CMD` is `python start_waggledance.py`, which boots the legacy / hexagonal stack and exposes the dashboard at `http://localhost:8000` and the static Reality View at `http://localhost:8000/hologram`. This path is unrelated to the Phase 11–15 autonomy proof; it is documented here for completeness.

## Limitations and open questions

* **Persistent volumes for runtime state.** The Dockerfile creates `/app/data/chroma_db` and `/app/logs` inside the container but does not mount them as volumes by default. For any non-trivial run you should add `-v` mounts; otherwise state is lost on container removal. There is no production-grade persistent layout in this alpha.
* **Ollama via host.** `OLLAMA_HOST=http://host.docker.internal:11434` works on Docker Desktop (Windows / Mac). On Linux Docker Engine you may need `--add-host=host.docker.internal:host-gateway` or a custom network.
* **Voikko**. `libvoikko1` and `voikko-fi` are installed for Finnish morphological analysis. If you don't need it, the runtime degrades gracefully (warning is logged; suffix-stripper fallback is used).
* **Architecture target**. Tested only on `linux/amd64` historically. ARM (Apple Silicon, Raspberry Pi 4/5) builds may require additional work.
* **Image size**. The lock-file install includes `faiss-cpu` and Playwright dependencies; the image is large (≥ 1 GB). For minimal deployments, edit the Dockerfile to use `requirements-ci.txt` instead.

## Offline / local-first note

The Phase 15 inner-loop autonomy proof does not call any external service. It does not require:

* internet access at runtime (only at build time for `pip install` and APT),
* a Claude / Anthropic / OpenAI API key,
* an Ollama instance,
* a vector store backend.

It writes to a scratch SQLite file inside the mounted output directory and exits with an exit code reflecting test outcome. This is the alpha's local-first contract.

## Compose

A `docker-compose.yml` is shipped for convenience:

```
docker compose up -d
```

This boots the legacy webserver. There is no Phase 15-specific compose service; the autonomy proof is a one-shot script and does not need long-running orchestration.

## Status summary

| concern | status |
|---|---|
| build steps documented | yes |
| build tested in this session | **no — not available** |
| run steps documented | yes |
| run tested in this session | **no — not available** |
| autonomy proof inside Docker tested | **no — not available** |
| persistent volume layout production-grade | **no — alpha** |
| ARM build verified | **no** |
| no provider credential required for inner loop | yes (proven outside Docker) |

Treat this document as the contract WaggleDance Docker should honour, not as a tested production deployment guide.
