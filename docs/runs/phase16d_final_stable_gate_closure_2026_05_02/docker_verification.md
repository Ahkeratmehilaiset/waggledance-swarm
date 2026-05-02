# Phase 16D P3 — Docker end-to-end verification

## Environment pre-check (P0)

| check | result |
|---|---|
| `docker version` | command not found |
| `docker compose version` | command not found |
| `which docker` | not in PATH |
| `command -v docker.exe` | empty |
| `C:/Program Files/Docker` directory | does not exist |
| `powershell Get-Command docker` | empty |

**`docker_available = false`** in this Phase 16D dev shell. Same situation as Phase 16B and Phase 16C dev shells.

Per master prompt P0 ENVIRONMENT PRE-CHECK rule, this means:

* `stable_v3_8_0_possible_in_this_session = false`
* Phase 16D's possible outcomes reduce to:
  * **B** (`v3.7.8-docker-gate-alpha`) if B324 cleanup PASSES with persisted semantics preserved
  * **C** (no tag) if cleanup fails / no progress made
* DO NOT spend time attempting to install Docker (out of scope per RULE 14)

## Phase 16D Docker work performed

* Inspected `Dockerfile`, `docker-compose.yml`, `.dockerignore` — unchanged since Phase 16B.
* No new Docker files created.
* No Docker build attempted (no Docker CLI).
* No in-container proof attempted (no Docker CLI).
* Docker docs in `docs/deployment/DOCKER_QUICKSTART.md` remain truthful: every "tested in this session" row says "no — not available".

## Stable gate disposition

**g01 Docker end-to-end:** `FAIL_NOT_VERIFIED` carry-over from Phase 16B and Phase 16C. **Sole remaining substantive blocker for v3.8.0 stable.**

**g19 Docker runtime no-network proof:** `FAIL_NOT_VERIFIED` (same root cause).

## Recommended Docker verification path for an external operator

```bash
# On a machine with Docker Engine 24+ or Docker Desktop 4.29+
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout main  # post-Phase-16D
docker version
docker compose version
docker build -t waggledance:phase16d .

# Inner-loop autonomy proofs with no runtime network
docker run --rm --network none waggledance:phase16d python tools/run_full_restart_continuity_proof.py
docker run --rm --network none waggledance:phase16d python tools/run_upstream_structured_request_proof.py
docker run --rm --network none waggledance:phase16d python tools/run_automatic_runtime_hint_proof.py

# Targeted smoke tests
docker run --rm --network none waggledance:phase16d python -m pytest \
  tests/autonomy_growth/test_full_restart_continuity_smoke.py \
  tests/autonomy_growth/test_upstream_structured_request_proof_smoke.py \
  tests/autonomy_growth/test_automatic_runtime_hint_proof_smoke.py \
  tests/autonomy_growth/test_seed_library.py \
  -q

# Optional: bounded soak
docker run --rm --network none waggledance:phase16d python tools/run_phase16b_proof_soak.py --iterations 3
```

Expected per-command outcome is documented in `docs/deployment/DOCKER_QUICKSTART.md`.

If those commands all pass on a real machine with Docker:

1. Update `RELEASE_READINESS.md` to record the Docker verification (with build duration, image size, command outputs).
2. Mark gate g01 as PASS and g19 as PASS in a new gate ledger.
3. v3.8.0 stable becomes justifiable in the next release session.

## Phase 16D conclusion for Docker

* No code changes to Docker files.
* No new Docker docs.
* g01 / g19 status unchanged: FAIL_NOT_VERIFIED.
* Phase 16D outcome: B (`v3.7.8-docker-gate-alpha`) prerelease, conditional on B324 cleanup completing successfully (which it did — see `bandit_b324_cleanup.md` and `persisted_semantics_preservation.md`).
