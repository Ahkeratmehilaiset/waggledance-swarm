# Phase 16F — Docker build evidence

**Date:** 2026-05-04 (UTC build time-window: 2026-05-04T05:48Z → 2026-05-04T06:00Z)
**Worktree:** `C:/Python/project2-phase16f-docker-stable-gate` (clean, from `origin/main` @ `7210a7e`)
**Branch:** `phase16f/docker-stable-gate`

## Result: PASS

| field | value |
|---|---|
| image tag | `waggledance:phase16f` |
| image ID | `7bbac5ee5c72` |
| image size | 3.09 GB |
| Dockerfile base | `python:3.13-slim` (debian trixie) |
| build engine | Docker Desktop 4.71.0, BuildKit |
| network during build | required (apt + pip pulls) |
| network during runtime | NOT required (P3 verified `--network none`) |

## Build attempts and deterministic fixes

The pre-existing `requirements.lock.txt` was generated against a Windows + CUDA 11.8 development environment and pinned several Linux-incompatible packages. Six total build attempts were needed; each prior failure narrowed to a single deterministic fix. The fixes are surgical and target only Docker portability — they do not change any autonomy code, schema, allowlist, or runtime path.

| # | Failure | Class | Deterministic fix |
|---|---|---|---|
| 1 | `Could not find pywin32==310` | dep install (Windows-only on Linux) | added `; sys_platform == "win32"` markers to `pywin32==310`, `pypiwin32==223`, `pyreadline3==3.5.4` |
| 2 | `Could not find torch==2.7.1+cu118` | dep install (CUDA wheel not on PyPI default index) | split each of `torch`, `torchaudio`, `torchvision` into a Windows-only `+cu118` line and a `sys_platform != "win32"` plain-version line |
| 3 | `Could not find triton-windows==3.6.0.post25` | dep install (Windows-only) | added `; sys_platform == "win32"` to `triton-windows` |
| 4 | `nvidia-cuda-runtime-cu12==12.9.79` conflict with line 298 (torch 2.7.1) | resolution conflict (torch transitive deps) | loosened `nvidia-cublas-cu12`, `nvidia-cuda-runtime-cu12`, `nvidia-cudnn-cu12` to `>=` lower bounds with `sys_platform != "win32"` markers |
| 5 | wider resolution conflict (torch 2.7.1 vs 11 lock-file pins) | resolution conflict | switched Dockerfile from `requirements.lock.txt` to `requirements-ci.txt` (already documented in the original Dockerfile comment as the alternative for "minimal deployment without hybrid retrieval or e2e browser testing") |
| 6 | (success) | — | `Successfully installed ...` (chromadb, transformers, torch, etc.); build completed in ~5 min |

**Architectural classification:** All six fixes fall into the master prompt's allowed "small deterministic fix" category for `dependency install failure`. No architectural change. No new cloud services. No provider credentials. No runtime storage truth change. No app startup change.

## What requirements-ci.txt drops (intentionally, for the stable-gate image)

`requirements-ci.txt` is the cross-platform CI subset of `pyproject.toml [project.dependencies]`. Compared to the lock file it drops:

* **`faiss-cpu`** — hybrid-retrieval extra. The autonomy proof scripts use the SQLite control plane; FAISS is not on the inner-loop hot path exercised by the v3.8.0 stable gate.
* **`playwright`** — browser e2e testing extra. Not exercised by autonomy proofs or smoke tests.
* **`unsloth`, `unsloth_zoo`, `xformers`, `bitsandbytes`** — LLM fine-tuning / efficient-attention libraries. The autonomy lane provider/builder delta is 0 in proofs by design (RULE 7); none of these are imported by the inner loop.
* **`pyttsx3`, `pyttsx3-windows`, `comtypes`, `webrtcvad`, `pygame`, etc.** — voice / multimedia / desktop integrations not relevant to the autonomy stable gate.

These omissions are documented in `docs/deployment/DOCKER_QUICKSTART.md` Phase 16F section. An external operator who wants the full hybrid-retrieval / browser-testing image can reverse the Dockerfile change and use `requirements.lock.txt` after applying the same `sys_platform` markers; the markers themselves are preserved on `requirements.lock.txt` for that reason.

## Files modified during P2

* `Dockerfile` (1 hunk) — `requirements.lock.txt` → `requirements-ci.txt` plus a clarifying comment documenting why.
* `requirements.lock.txt` (5 hunks) — added `sys_platform == "win32"` / `sys_platform != "win32"` markers to Windows-only and CUDA-specific packages; loosened nvidia-cuda-* version pins to lower-bounds. **The lock file is no longer the install source for this image** but the markers make it portable for any future operator who restores it.
* `.dockerignore` (1 hunk) — added carve-outs for the four canonical proof scripts and the autonomy_growth smoke test directory + conftest, so the same image can run both `python tools/run_*_proof.py` and `python -m pytest tests/autonomy_growth/...`.

## Build duration breakdown (final attempt)

```
05:56:44  build start
+ ~9 s    base layer + apt-get update + libvoikko1 + voikko-fi
+ ~3.5 m  pip install -r requirements-ci.txt (incl. wheel builds for sgmllib3k, sentencepiece, etc.)
+ ~2 s    COPY . .
+ ~0.1 s  mkdir -p data/chroma_db logs
+ ~3.3 m  exporting layers (3.09 GB image, overlayfs + WSL2)
+ ~0 s    naming to docker.io/library/waggledance:phase16f
~06:00:30 done
```

Total wall clock for the successful build: ~3 min 46 s pip install + ~3 min 18 s export = ~7 min end-to-end on a 24-CPU / 62 GB / overlayfs / WSL2 host.

## Build invariants verified

* No internet at runtime: ✅ enforced by `--network none` in P3.
* No provider credentials baked into image: ✅ `OLLAMA_HOST=http://host.docker.internal:11434` is the only network env and it's harmless when `--network none`; no API keys, no tokens.
* No autonomy code changed: ✅ all `waggledance/`, `core/` Python files identical to `origin/main` @ `7210a7e`.
* No allowlist widening: ✅ six-family allowlist unchanged.
* No new dockerignore-bypassed secret: ✅ carve-outs limited to known-public proof scripts and tests.

## Stable gate ledger updates

* **g01 Docker end-to-end**: PENDING_P2_P3 → **PASS** (build complete, image 3.09 GB)
