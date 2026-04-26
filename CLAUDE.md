# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Source-of-truth rules (load-bearing — read before editing)

These exist because on 2026-04-11 the `U:\project2` RAM-disk working tree disappeared together with a full day of Phase 7 work (commit `3babb93`). The rules below are written so that failure mode can never recur.

1. **Work exclusively in `C:\Python\project2`.** Never develop in `U:\`, `R:\`, any RAM-disk, `%TEMP%`, or a zip-extraction folder. Verify with `git remote -v` that you are pointed at the real GitHub repo before editing.
2. **GitHub is the primary history.** Zip backups are disaster recovery only. If asked to work from a zip, clone GitHub first, then overlay runtime data on top of the clone — see `docs/RECOVERY_POLICY.md`.
3. **Never `git init` on a restored backup snapshot.** A fresh `git init` erases every commit that ever pointed at that file tree.
4. **Use `tools/savepoint.ps1` for green checkpoints.** It refuses to run off the C: drive, runs the tests you pass via `-TestPath`, commits, and pushes in one step. Default test path: `tests/test_phase7_hologram_news_wire.py`.
5. **If you reconstruct work from reports, say so in the commit message** — include report paths and the known-good RC commit SHA.

## Architecture — solver-first multi-layer routing

```
Query → Solver Router → Solver Engines (Layer 3, authoritative)
                     → Specialist Models (Layer 2, sklearn, 14 models with canary lifecycle)
                     → LLM fallback (Layer 1, Ollama or stub)
                     ↓
                     Verifier (checks against World Model)
                     ↓
                     CaseTrajectory → MAGMA Audit Trail (Audit / Replay / Overlay / Provenance / Trust)
                     ↓
                     Night Learning  /  Dream Mode (counterfactual sims)
```

Hexagonal layout in `waggledance/`: `core/` is the domain, `adapters/http/routes/` and `adapters/llm/` are ports, `bootstrap/` is the DI container, `application/` holds DTOs/services. Hex-cell FAISS retrieval is keyed by `core/hex_cell_topology` — solvers are organized into 8 cells (`general, thermal, energy, safety, seasonal, math, system, learning`).

## Crown-jewel paths and BUSL Change Date rule

These directories are BUSL-1.1 protected (auto-converts to Apache 2.0 on `2030-03-19`):
- `waggledance/core/learning/` (dream mode, consolidator, meta-optimizer)
- `waggledance/core/projections/`
- `waggledance/core/magma/` (audit, provenance, replay, trust, confidence decay)
- Phase 8.5 / Phase 9 additions: `waggledance/core/{dreaming, meta, autonomy, ir, capsules, vector_identity, ingestion, world_model, conversation, identity, provider_plane, api_distillation, builder_lane, solver_synthesis, memory_tiers, hex_topology, promotion, proposal_compiler, local_intelligence, cross_capsule}/*`

**Rule:** any commit that introduces non-trivial logic (>50 LOC excluding imports/docstrings, OR adds scoring/validation/synthesis/promotion logic) to one of these paths MUST also confirm `LICENSE-BUSL.txt` Change Date is `2030-03-19` in the same commit. The file is currently at the correct date — verify with `grep "Change Date" LICENSE-BUSL.txt`.

## Multi-session worktree pattern (Phase 8.5 / Phase 9)

The repository runs multiple session branches in parallel. **Do not switch the primary worktree's HEAD** while a 400h gauntlet campaign is writing to `docs/runs/ui_gauntlet_400h_*/hot_results.jsonl` — switching branches will conflict with live writes. Use `git worktree add` for any session work:

```
C:/python/project2          phase8.5/dream-curriculum   ← live campaign here, do not switch HEAD
C:/python/project2-a        phase8.5/curiosity-organ    (Session A — gap_miner)
C:/python/project2-b        phase8.5/self-model-layer   (Session B — self-model + tensions)
C:/python/project2-d        phase8.5/hive-proposes      (Session D — meta-learner)
C:/python/project2-master   phase9/autonomy-fabric      (Phase 9 master)
C:/python/project2-r7_5     phase8.5/vector-chaos       (R7.5 — vector writer chaos)
```

Each session has a state file at `docs/runs/phase8_5_<session>_session_state.json` (or `phase9_autonomy_fabric_state.json`). The state file is **mandatory** and must be updated **before** every commit, not after — it carries `pinned_inputs`, `consumed_hook_contracts`, completed/remaining sub-parts, and `next_recommended_action` so a later session can resume after context exhaustion.

**Pinned-input rule:** every multi-session tool reads from `pinned_inputs[]` only. Never re-glob mid-session, never silently switch to fresher artifacts. Each pinned entry carries `path`, `size_bytes`, `mtime_epoch`, `sha256_first_4096_bytes`, `sha256_last_4096_bytes` (or `sha256_full` if file ≤ 8192 bytes). The aggregate `pinned_input_manifest_sha256` (first 12 chars) names default output directories.

**Hook contract rule:** session state files list `consumed_hook_contracts[]` with `file`, `version`, `file_sha256`. If the version is unchanged but the sha changes, fail loud with `"contract content changed without version bump"`. Operators must bump the version or explicitly reset the recorded sha to acknowledge.

## Domain-neutrality rule (Phase 9 onward)

New code/docs/comments must use neutral terms: **Cognitive Fabric, Reality View, Capsule, Cell, Runtime Topology, Provenance, Distillation, Builder Lane, Mentor Lane**. Do NOT introduce bee/hive/swarm/honeycomb/factory/PDAM/beverage/etc. metaphors into new core modules. Legacy paths and product names may remain for compatibility.

Use cases are represented as **capsules** (`factory_v1, personal_v1, research_v1, home_v1, cottage_v1, gadget_v1`), declared in capsule manifests — the core treats them as data, not hardcoded business logic.

## Test commands

```
python -m pytest -q                                    # full suite (~5817 tests, do NOT run during multi-session work)
python -m pytest tests/<file>.py -q --tb=short         # targeted
python -m pytest tests/test_dream_curriculum.py tests/test_dream_request_pack_and_collapse.py tests/test_dream_shadow_replay.py tests/test_dream_meta_proposal.py -q --tb=short -p no:warnings   # Phase 8.5 Session C suite
python -m pytest tests/test_vector_writer_resilience.py -q --tb=short -p no:warnings   # R7.5
python -m pytest tests/test_meta_learner.py tests/test_hive_proposes.py -q             # Session D
```

Multi-session prompts (`B.txt`, `C.txt`, `R7_5.txt`, `D.txt`, `Prompt_1_Master_v5_1.txt`) explicitly forbid running the full suite — that is reserved for the human PR-time gate. Use targeted pytest only.

## Common-task command shapes

```
# Real-data Session C end-to-end run
python tools/dream_curriculum.py --apply --json
python tools/run_dream_cycle.py --apply --json

# Real-data Session D meta-learner run
python tools/hive_proposes.py --apply --json

# Vector writer dry-run / replay-only
python tools/vector_indexer.py --replay-only --json
python tools/vector_indexer.py --apply --since <event_id>

# Native start (Ollama required)
python start_waggledance.py
python start_waggledance.py --stub                     # no Ollama
python start_waggledance.py --preset=cottage-full

# Safe checkpoint (refuses RAM-disk, runs tests, commits, pushes)
.\tools\savepoint.ps1 -Message "fix(foo): bar"
.\tools\savepoint.ps1 -Message "..." -TestPath "tests/test_foo.py"
```

## Hard safety constraints during session work

Sessions A/B/C/D, R7.5, and Phase 9 Master share these constraints — apply them when any session-style prompt is active:

- No port 8002 binding from new code.
- No live LLM in tests (`requests.post`, `httpx.post`, `openai.*`, `anthropic.*`, `ollama.*` are forbidden patterns in tool sources).
- No Prometheus surface changes.
- No background daemons.
- No automatic merge to main; no automatic apply to live runtime.
- No subprocess-driven code generation outside the U2 builder lane (Phase 9 §U2 only).
- All write paths in tests must use `tmp_path`. Production state is never mutated outside `docs/runs/<session>/<sha12>/` or `docs/runs/<session>/HISTORY.jsonl`.

## Repo conventions worth knowing

- Python 3.11/3.12/3.13 supported (CI matrix in `.github/workflows/ci.yml`).
- `pytest.ini_options.asyncio_mode = "strict"` — async tests need explicit `@pytest.mark.asyncio`.
- Setuptools is configured to ship only `waggledance*` and `integrations*`; `tests/`, `docs/`, `data/`, `models/`, `chroma_data/` are excluded from installable artifacts.
- Append-only JSONL logs (vector events, history chains, calibration corrections) silently skip malformed rows by design — see `docs/architecture/VECTOR_WRITER_RESILIENCE.md` for the formal at-least-once-idempotent contract.
- Hex topology cells are `general, thermal, energy, safety, seasonal, math, system, learning` (enforced by `schemas/solver_proposal.schema.json`).

## Documentation map

- `README.md` — public-facing overview and architecture diagram
- `AGENTS.md` — task rules and runtime-audit conventions
- `docs/architecture/HONEYCOMB_SOLVER_SCALING.md` — Phase 8 design
- `docs/architecture/MAGMA_VECTOR_STAGE2.md` — vector writer stage 2 contract
- `docs/architecture/SELF_MODEL_LAYER.md` + `SELF_MODEL_FORMULAS.md` + `SELF_MODEL_PROVENANCE.md` — Session B
- `docs/architecture/DREAM_MODE_2_0.md` + `DREAM_REPLAY_FORMULAS.md` + `DREAM_PROVENANCE.md` — Session C
- `docs/architecture/THE_HIVE_PROPOSES.md` + `META_PROPOSAL_FORMULAS.md` + `META_PROPOSAL_PROVENANCE.md` — Session D
- `docs/architecture/HOOKS_FOR_DREAM_CURRICULUM.md` + `HOOKS_FOR_META_LEARNER.md` + `HOOKS_FOR_RUNTIME_REVIEW.md` — versioned cross-session contracts
- `docs/architecture/VECTOR_WRITER_RESILIENCE.md` — R7.5 failure matrix
- `docs/RECOVERY_POLICY.md` — exact recipe for recovering from a lost worktree

## When in doubt

Stop. Verify you are on the C: drive. Verify `git status` is clean or your in-progress work is staged. Verify `git remote -v` points at the real GitHub repo. Then proceed.
