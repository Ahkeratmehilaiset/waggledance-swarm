# Docker Quickstart — WaggleDance (v3.8.0 stable, Phase 16F verified)

This document describes how Docker works for the WaggleDance autonomy runtime and proof scripts. **Phase 16F verified the documented contract end-to-end** on Docker Desktop 4.71.0 / Engine 29.4.1 with `--network none`. The `Dockerfile` was updated in Phase 16F to use `requirements-ci.txt` (the cross-platform CI subset already proven on GitHub Actions Linux runners) instead of `requirements.lock.txt` — the lock file was generated against a Windows + CUDA 11.8 dev environment and pins Linux-incompatible packages.

**Tested in this session:** Yes. All four canonical proofs and the 4-file autonomy_growth smoke suite ran inside `waggledance:phase16f` with `--network none` at corpus 104, matching local results 1-to-1. See `docs/runs/phase16f_docker_stable_gate_2026_05_03/docker_runtime_proofs.md` for full evidence.

If a step below fails on your machine, the `Dockerfile` is the authoritative source. File an issue with the failing step + your `docker version` output.

## Prerequisites

* Docker Engine 24+ or Docker Desktop 4.29+.
* If you want the optional Ollama integration, run Ollama on the host (`ollama serve`); the container reaches it via `OLLAMA_HOST=http://host.docker.internal:11434` (default in the Dockerfile).
* No cloud account is required. The Phase 15 inner-loop autonomy proof runs **without any provider credentials** and writes only to a scratch SQLite under `docs/runs/phase15_runtime_hints_release_2026_05_01/`.

## Build the image

```
docker build -t waggledance:v3.8.0 .
```

(or `waggledance:phase16f` — both refer to the same Phase 16F-verified image; v3.8.0 is the stable tag.)

The build pulls `python:3.13-slim` plus a small APT layer (`curl`, `git`, `libvoikko1`, `voikko-fi`) and installs Python deps from `requirements-ci.txt` (the Phase 16F default — cross-platform Linux-portable subset). Expected build time: 5–10 minutes on a clean cache (Phase 16F measured ~7 min on a 24-CPU / 62 GiB / overlayfs / WSL2 host). Image size: 3.09 GB.

**Why requirements-ci.txt and not requirements.lock.txt?** The lock file was generated against a Windows + CUDA 11.8 dev environment and pins Linux-incompatible packages (`pywin32`, `triton-windows`, `torch==2.7.1+cu118`, `nvidia-cuda-runtime-cu12==12.9.79`). Phase 16F switched the Dockerfile to `requirements-ci.txt` — the same install source GitHub Actions CI uses on Ubuntu runners. This drops `faiss-cpu`, `playwright`, `unsloth`, `xformers`, `bitsandbytes`, `webrtcvad`, `pyttsx3`, `comtypes` and similar — none required by the autonomy proof scripts (which use the SQLite control plane only) or by the targeted smoke suite. The lock file remains in the repo with `sys_platform` markers added to the Windows-only / CUDA-only lines, so any operator who needs the full hybrid-retrieval / browser-testing image can revert the Dockerfile change locally without further surgery.

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

## Run the Phase 16A upstream structured_request proof

```
docker run --rm -v "$(pwd)/docs/runs/phase16_upstream_structured_request_2026_05_01:/app/docs/runs/phase16_upstream_structured_request_2026_05_01" \
    waggledance:phase15-alpha \
    python tools/run_upstream_structured_request_proof.py
```

What this should produce:

* Console summary ending with the Phase 16A invariants block.
* Updated `upstream_structured_request_proof.json` on the host (via the volume mount).
* Post-Phase-16B-P4: corpus is **104** (was 98 in Phase 16A); proof should report `auto_promotions_total = 104`.

## Run the Phase 16B full-corpus restart continuity proof

```
docker run --rm -v "$(pwd)/docs/runs/phase16b_stabilization_release_gate_2026_05_01:/app/docs/runs/phase16b_stabilization_release_gate_2026_05_01" \
    waggledance:phase15-alpha \
    python tools/run_full_restart_continuity_proof.py
```

What this should produce:

* Console summary ending with the restart-invariants block (all `True`).
* Updated `full_restart_continuity_proof.json`: `corpus_total = 104`, persisted solver count + capability-feature count identical across reopen, `provider_jobs_delta_across_restart = builder_jobs_delta_across_restart = 0`.

## Run the Phase 16B proof soak

```
docker run --rm waggledance:phase15-alpha \
    python tools/run_phase16b_proof_soak.py --iterations 5
```

What this should produce:

* 15 / 15 iterations pass (3 proofs × 5 iterations); no flakes; mean ~33 s / iteration. Each iteration writes to a unique temp output directory inside the container.

Expected key fields in the JSON:

| field | expected |
|---|---|
| `selected_upstream_caller` | `"waggledance.application.services.autonomy_service.AutonomyService.handle_query"` |
| `corpus_total` | 98 |
| `manual_structured_request_in_input_detected` | `false` |
| `manual_low_risk_hint_in_input_detected` | `false` |
| `proof_constructed_runtime_query_objects` | `false` |
| `proof_bypassed_selected_caller` | `false` |
| `proof_bypassed_handle_query` | `false` |
| `structured_request_derived_total` | 98 |
| `low_risk_hint_derived_total` | 98 |
| `before.served_total` | 0 |
| `after.served_via_capability_lookup_total` | 98 |
| `negative_cases_passed_total` | 7 |
| `kpis.provider_jobs_delta_during_proof` | 0 |
| `kpis.builder_jobs_delta_during_proof` | 0 |

## Run the Phase 16A restart-continuity smoke

```
docker run --rm waggledance:phase15-alpha \
    python -m pytest tests/autonomy_growth/test_upstream_restart_continuity.py -q
```

Expected: 1 passed. The smoke test creates a temp scratch DB inside the container, drives a six-seed corpus through `AutonomyService.handle_query`, harvests + auto-promotes, closes the DB, reopens it, and verifies that the same flat upstream input is still served via capability lookup with `provider_jobs_delta = builder_jobs_delta = 0` across the restart.

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

The Phase 15 and Phase 16A inner-loop autonomy proofs do not call any external service. They do not require:

* internet access at runtime (only at build time for `pip install` and APT),
* a Claude / Anthropic / OpenAI API key,
* an Ollama instance,
* a vector store backend.

Both write to a scratch SQLite file inside the mounted output directory and exit with an exit code reflecting test outcome. This is the alpha's local-first contract.

## Compose

A `docker-compose.yml` is shipped for convenience:

```
docker compose up -d
```

This boots the legacy webserver. There is no Phase 15-specific compose service; the autonomy proof is a one-shot script and does not need long-running orchestration.

## Status summary (Phase 16F verified)

| concern | status |
|---|---|
| build steps documented | yes |
| build tested in this session | **yes (Phase 16F, image 3.09 GB)** |
| run steps documented | yes |
| run tested in this session | **yes (Phase 16F, `--network none`)** |
| Phase 15 hint proof inside Docker `--network none` tested | **yes — corpus 104, 0/0 delta** |
| Phase 16A upstream proof inside Docker `--network none` tested | **yes — corpus 104, 7/7 negative cases pass** |
| Phase 16B full-corpus restart proof inside Docker `--network none` tested | **yes — 104/104 served pre+post, all invariants True** |
| autonomy_growth smoke (4 files) inside Docker `--network none` tested | **yes — 16 passed, 27 conditional skips, 0 failures** |
| Phase 16B proof soak inside Docker tested | not in this session (local soak ran instead, 9/9 no flakes) |
| persistent volume layout production-grade | **no — still alpha posture** |
| ARM build verified | **no — linux/amd64 only** |
| no provider credential required for inner loop | **yes — proven inside Docker `--network none`** |
| no internet required at runtime | **yes — proven inside Docker `--network none`** |

This document now reflects a tested deployment contract. Phase 16F evidence: `docs/runs/phase16f_docker_stable_gate_2026_05_03/docker_build.md` and `.../docker_runtime_proofs.md`.
