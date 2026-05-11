# EIG2-M0 Reality Check — existing repo inventory

Owner: Claude (Reality Check Owner, EIG2 Part 12.7)
Peer review: Codex
Status: draft (M0 deliverable per ownership split agreed in bridge `eig2-m0-ownership-split-2026-05-11`)
Date: 2026-05-11

Purpose: enumerate every existing hook EIG2 plans to use, with file:line and signature, BEFORE Codex proposes adapter shapes. Locks the inventory at this commit; future changes via PR amendment.

All paths relative to repo root. Line numbers are from `origin/main` @ `bcc4753`.

## 1. Hex topology selector (Part 4 routing chain step 5)

- File: `waggledance/application/services/hex_topology_registry.py`
- Class: `HexTopologyRegistry` (line 28)
- Constructor: `__init__(config_path: str = "configs/hex_cells.yaml", agents: list | None = None)` (line 31)
- Primary selector: `select_origin_cell(query: str, intent: str = "") -> str | None` (line 239)
  - Uses pre-lowercased selectors built at load time via `_build_selector_index`.
  - Post H1+H24 (PR #245): agent.domain distribution across cells, no hub-dominance.
- Source-of-truth config: `configs/hex_cells.yaml` (7-cell agent-routing topology).
- Separate file: `waggledance/core/hex_cell_topology.py` (8-cell solver-retrieval topology). See `docs/architecture/HEX_TOPOLOGIES.md` for the disambiguation post #256.

## 2. MAGMA event/append API (Part 1 hard rule 1, Part 7 progressive replay)

- File: `waggledance/core/magma/event_log_adapter.py`
- Class: `EventLogAdapter` (line 47) — wraps legacy LearningLedger.
- Append API: `log_event(event_type: str, source: str = "autonomy", goal_id: str = "", capability_id: str = "", quality_grade: str = "", **details) -> None` (line 70)
- Other MAGMA modules: `audit_projector.py`, `confidence_decay.py`, `provenance.py`, `replay_engine.py`, `reflective_workspace.py`, `self_model.py`, `trust_adapter.py`, `vector_events.py`.
- Burst-perf invariant: O(N) trim is the regression class flagged in PR #248 unified-CI triage (`test_log_event_avoids_O_N_trim_under_burst`).

## 3. BridgeLLMAdapter constructor (Part 1 hard rule 4, post-#257 / D3.4)

- File: `waggledance/adapters/llm/bridge_llm_adapter.py`
- Class: `BridgeLLMAdapter` (line 29) — LLMPort wrapper delegating to BridgeLLMClient + fallback.
- Constructor: `__init__(client: Any, fallback_adapter: Any, injection_point: str = "orchestrator.generate") -> None` (line 38)
  - `client` is duck-typed (BridgeLLMClient) to avoid Profile S import side-effects.
  - `fallback_adapter` is an LLMPort impl (OllamaAdapter today) used when BridgeLLMClient disabled OR raises.
- Implication for EIG2: any new EIG2-internal LLM-eligible path MUST construct via BridgeLLMAdapter, never raw OllamaProvider. Bypassing skips operator-decision-4 PII redactor.

## 4. AliasRegistry public surface used by `container._resolve_agent_domain` (H24 / PR #245)

- File: `waggledance/core/capabilities/aliasing.py`
- Class: `AliasRegistry` (line 47) — bidirectional lookup legacy ID ↔ alias ↔ canonical.
- Constructor: `__init__(agents: List[AgentAlias])` (line 54)
- Class methods:
  - `from_yaml(cls, path: str | Path) -> AliasRegistry` (line 77)
  - `from_yaml_default(cls) -> AliasRegistry` (line 92)
- Instance methods used by EIG2-relevant code paths:
  - `resolve(name: str) -> Optional[str]` (line 108) — canonical or None.
  - `resolve_strict(name: str) -> str` (line 115) — canonical or KeyError.
- Consumer: `waggledance/bootstrap/container.py:350 _resolve_agent_domain(agent_id, header, alias_registry)` — drops `profiles[0]` substitution, picks canonical part [1] from the registry.

## 5. RuntimeGapDetector emission contract (D2.2 / PR #260)

- File: `waggledance/core/autonomy_growth/gap_intake.py`
- Class: `RuntimeGapDetector` (line 62) — records runtime gap signals into the control plane.
- Constructor: `__init__(control_plane: ControlPlaneDB) -> None` (line 65)
- Emit API: `record(signal: GapSignal) -> RuntimeGapSignalRecord` (line 76)
  - Signal payload JSON-encoded with sort_keys=True.
- Wired via `container.runtime_gap_detector` cached_property; emitted from `chat_service` when response confidence < configured threshold.

## 6. ControlPlaneDB read/write surface (D2.1 / PR #253, post Option B / R22.2e)

- File: `waggledance/core/storage/control_plane.py`
- Class: `ControlPlaneDB` (line 376) — thin SQLite wrapper.
- Lifecycle: `__init__(db_path: Optional[Path | str] = None)` (line 461), `close()` (line 526)
- Read surface (subset relevant to EIG2):
  - `get_solver_family(name: str) -> Optional[SolverFamilyRecord]` (line 634)
  - `get_solver(name: str) -> Optional[SolverRecord]` (line 696)
  - `get_solver_name(solver_id: int) -> Optional[str]` (line 709)
  - `get_solver_artifact(...)` (line 1245)
- Write surface (subset):
  - `record_provider_job(...)` (line 926)
  - `record_builder_job(...)` (line 1013)
  - `record_promotion_state(...)` (line 1049)
  - `record_cutover_state(...)` (line 1088)
  - `record_validation_run(...)` (line 1342)
  - `record_shadow_evaluation(...)` (line 1395)
- Option B (R22.2e) relaxed read-side locks — every NEW EIG2 writer must pass an "Option-B compliance" contract test per R15:
  - write does not block existing readers beyond budget P
  - reader observes monotonic progress under concurrent write storm
  - histogram of write durations uses wall-clock denominator (PR #224 fix)

## 7. AgentLifecycleManager.spawn_for_profile (D3.2 / PR #251)

- File: `waggledance/core/orchestration/lifecycle.py`
- Class: `AgentLifecycleManager` (line 15) — manages agent spawning, promotion, demotion.
- Primary API: `spawn_for_profile(all_agents: list[AgentDefinition], profile: str) -> list[AgentDefinition]` (line 18) — filters and activates agents matching the given profile.
- Sole owner of `agent.active` state transitions. Container constructs with `active=False`; lifecycle promotes per profile membership.

## 8. HotPathCache miss/hit telemetry hooks

- File: `waggledance/core/autonomy_growth/hot_path_cache.py`
- Stats container: `HotPathCacheStats` (line 64) — `warm_hits`, `cold_hits_warmed`, etc.
- Main class: `HotPathCache` (line 329) — bundle of warm caches + buffered sink the router shares.
- Implication for EIG2: tunnel-candidate cache should reuse this pattern (`bounded LRU + warm/cold split`) per R7 latency rules and Part 14.3 OOM protection. Do not create a new parallel cache implementation.

## 9. Existing alarm vocabulary

EIG2 prompt names 10+ alarms (Part 10.1, 10.6, 10.7, 15.2, 28.4):
`tunnel_inflation_alarm`, `magma_write_pressure_alarm`, `replay_backpressure_alarm`, `hot_path_llm_violation`, `safety_fence_alarm`, `recursive_drift_alarm`, `disk_pressure_alarm`, `tunnel_thrashing_alarm`, `tunnel_contagion_alarm`, `autonomous_merge_flag_tamper_alarm`.

**Grep result (origin/main @ bcc4753): zero occurrences of any of these strings.**

Existing repo alarm vocabulary is unrelated (e.g., `freezer_alarm_above_neg5` in `low_risk_seed_library.py`). EIG2 alarms create a new namespace; no collision risk. Codex's bridge_classify.py reference impl (`.orchestrator/bridge_classify.py`) introduces `RegressionClass` enums but those are classification labels, not alarm event names.

## 10. Bridge protocol surface (Part 12)

- File: `.agent-bridge/shared/events.jsonl` — JSONL append-only event stream.
- Current event fields (live observed via grep on events.jsonl):
  - `ts_utc` (str ISO8601)
  - `agent` (str: "claude" | "codex" | "operator" | "system")
  - `type` (str: "message" | "claim" | "done" | "finding" | "heartbeat" | "synthesis" | "ownership_proposal" | "sandbox_drop" | "simulation_open" | etc.)
  - `task_id` (str)
  - `status` (str: "open" | "active" | "answered" | "done" | "closing" | etc.)
  - `severity` (str: "low" | "medium" | "high" | "critical" | "")
  - `to` (str — recipient agent or "")
  - `message` (str — free-form)
  - `paths` (list[str])
  - `write_scope` (list[str])
  - `run_id` (str)
  - `pid` (int)
  - `cwd` (str)
  - `payload` (dict)
- Fields EIG2 Part 12.5 requires that are NOT in the live schema:
  - `protocol_version`
  - `message_type` (vs current `type`)
  - `id` (vs current `task_id`)
  - `timestamp` (vs current `ts_utc`)
  - `author` (vs current `agent`)
  - `related_milestone`
  - `parent_id` (no current analog — task_id continuity is implicit)
  - `payload_hash` (no current analog)
- Decision (R3 + Codex C011): adapter-first, NOT migration. EIG2 read/write goes through a projection layer that maps live fields to EIG2 schema view. Existing readers see live schema unchanged. Live event-loss bug (R3 incident: `ownership_proposal` dropped by Codex's `type == "message"` filter) is being fixed by Codex on branch `bridge-polymorphic-reply-polling-2026-05-11` — when that lands, ADR 020 (`docs/eig2/adr/020-bridge-type-field-non-gating.md`) codifies the no-filter-by-type convention.

## 11. Ledger / config files EIG2 will reference

- `configs/hex_cells.yaml` — 7-cell agent-routing topology (load by HexTopologyRegistry).
- `configs/alias_registry.yaml` — canonical ID map (load by AliasRegistry.from_yaml_default).
- `configs/profiles/{small,medium,large}.json` — profile definitions consumed by AgentLifecycleManager.spawn_for_profile (Note: actual path may be `solver-profiles/*.json` per repo convention; M0 PR2 owner verifies).
- `data/control_plane.db` — SQLite (created on first write per D2.1 lifecycle).

## 12. What does NOT yet exist (and which milestone creates it)

| Component | First milestone | Owner |
|---|---|---|
| `waggledance/core/reasoning/tunnel_registry.py` | M2 (interfaces) → M4 (impl) | Codex |
| `waggledance/core/reasoning/tunnel_overlay.py` | M4 | Codex |
| `waggledance/core/reasoning/tunnel_lifecycle.py` | M5 | Codex |
| `waggledance/core/reasoning/topology_provider.py` | M2 | Codex |
| `waggledance/core/magma/progressive_replay.py` | M3 | Claude |
| `waggledance/core/magma/compact_decision_card.py` | M3 | Claude |
| `waggledance/core/magma/aaak_dialect.py` | **REJECTED v1.1** (Codex C060) | n/a |
| `waggledance/core/magma/closet_index.py` | M3 (deferred — not yet needed) | Claude |
| `waggledance/core/autonomy_growth/tunnel_miner.py` | M5 (shadow only) | Codex |
| `waggledance/core/autonomy_growth/swarm_consensus.py` | M5 | Codex |
| `waggledance/core/safety/backpressure.py` | M0 ADR + M3 impl | Claude |
| `waggledance/core/safety/circuit_breakers.py` | M0 ADR + M3 impl | Claude |
| `waggledance/core/safety/eig2_halt.py` | M0 ADR + M3 impl | Claude |
| `waggledance/core/safety/resource_budget.py` | M3 | Claude |
| `waggledance/core/benchmarks/topology_latency_bench.py` | M6 | Codex |
| `waggledance/core/benchmarks/swarm_scale_simulator.py` | M6 | Codex |
| `benchmarks/eig2_acceptance_verdict.py` | M8 (joint) | both |
| `.orchestrator/bridge_classify.py` | **M0 PR2** | Codex |
| `.orchestrator/no_human_prompt_lint.py` | **M0 PR2** | Codex |
| `configs/explosive_intelligence_growth_v2.yaml` | **M0 PR2** | Codex |
| `configs/eig2_self_modification_denylist.yaml` | **M0 PR2** | Codex |

## 13. Cross-cutting reality-check notes

- **CSafeLoader (H21)** — YAML loads under `waggledance/core/profile_loader.py` use `yaml.CSafeLoader`. EIG2 new YAML loads (config + denylist) must continue this pattern; default `yaml.safe_load` is ~7× slower and counts as a boot regression per R6.
- **Cached_property pattern (D2.3)** — `container.runtime_gap_detector`, `container.autogrowth_scheduler`, `container.control_plane_db` use `@cached_property`. EIG2 new container additions follow the same pattern; eager instantiation at construction is a boot regression.
- **`asyncio.to_thread` for sync IO (H47)** — `chat_service._record_case` wraps sync DB call in `asyncio.to_thread`. Every new EIG2 IO writer on the chat path must follow this pattern or be entirely off the request path (queue + worker per R14).
- **Whole-token regex pattern cache (H22+H58)** — `solver_router._has_signal` uses `\b`-anchored pre-compiled regex per signal-set. EIG2 tunnel semantic matching must reuse the same pattern cache primitive, not implement parallel substring logic.

## 14. Sign-off

- Author: Claude (Reality Check Owner, EIG2 Part 12.7).
- Peer reviewer required: Codex.
- Convergence gate: this document must be endorsed by Codex via bridge or GitHub PR review before M0 PR2 (Codex's adapter spec + reference impls) starts.
- M6 trust-filter applies: ship only what both agents endorse after at least one push-back round.
