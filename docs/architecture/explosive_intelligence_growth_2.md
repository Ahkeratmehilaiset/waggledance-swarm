# Explosive Intelligence Growth 2 Architecture Map

Status: M1.0 prep draft.
Source commit: `origin/main` `2cc6fec28d4103b8c7deab6aad8488866b5e3ba6`.
Date: 2026-05-11.
Author: Codex, with Claude RCO review requested through bridge task `eig2-m1-architecture-map-2026-05-11`.

This document maps the EIG2 design onto the current WaggleDance repo after
M0. It is intentionally docs/orchestrator-only: no runtime module is touched in
this PR. The first runtime touch remains a later M1.x/M2 PR and is scoped by
ADR-010, ADR-012, ADR-015, and ADR-020.

## Current repo substrate

EIG2 builds on existing code rather than replacing it.

| Substrate | Current source | EIG2 use |
|---|---|---|
| Agent-routing hex mesh | `HexTopologyRegistry` in `waggledance/application/services/hex_topology_registry.py`, backed by `configs/hex_cells.yaml` | Production Hex2D base mesh for agent/domain routing. |
| Solver-retrieval topology | `HexCellTopology` in `waggledance/core/hex_cell_topology.py` | Separate 8-cell knowledge retrieval topology. EIG2 must not conflate this with the 7-cell agent mesh. |
| Reasoning route seam | `waggledance/core/reasoning/solver_router.py` plus the runtime caller chain documented in `docs/architecture/RUNTIME_ENTRYPOINT_TRUTH_MAP.md` | Future topology provider must stay deterministic and outside LLM/provider calls. |
| MAGMA append path | `EventLogAdapter` in `waggledance/core/magma/event_log_adapter.py` | Raw append-only authority for replay and audit. |
| Bridge LLM guard | `BridgeLLMAdapter` in `waggledance/adapters/llm/bridge_llm_adapter.py` | Any EIG2 LLM-eligible path must route through this adapter; hot routing must not call it. |
| Alias/profile mapping | `AliasRegistry` and `KNOWN_PROFILES` | EIG2 must preserve canonical agent IDs and not invent runtime profile labels. |
| Runtime gap intake | `RuntimeGapDetector` and `ControlPlaneDB.record_runtime_gap_signal` | Later M3/M5 growth loops consume gap signals, but request-path writes need H47/Option-B protection. |
| Lifecycle | `AgentLifecycleManager.spawn_for_profile` | EIG2 may propose agent activity, but lifecycle remains the owner of active-state transitions. |
| Hot-path cache pattern | `HotPathCache` in `waggledance/core/autonomy_growth/hot_path_cache.py` | Tunnel candidate caches should reuse the bounded warm/cold pattern. |
| Bridge protocol | `.agent-bridge/shared/events.jsonl` plus `.orchestrator/eig2_bridge_projection.py` | EIG2 adds projection fields without migrating the live bridge schema. |

The authoritative M0 inventory is `docs/eig2/spikes/M0-reality-check.md`.
The method that produced the constraints is summarized in
`docs/eig2/spikes/M0-200-option-summary.md`.

## Target shape

EIG2 is a sparse overlay on the existing system:

```text
incoming request
  -> current service/runtime caller chain
  -> deterministic intent/profile/cell selection
  -> Hex2D base topology
  -> optional sparse semantic tunnel overlay
  -> existing solver/agent dispatch
  -> MAGMA raw append
  -> optional queued compact-card derivation
```

The production baseline remains Hex2D. Virtual 3D and virtual 4D providers are
benchmark-only until a later ADR changes that. Tunnels are shortcuts over the
base mesh, not a replacement for MAGMA, not a replacement for profile gating,
and not a place to run an LLM.

## Subsystems

### 1. Topology provider

First runtime module: `waggledance/core/reasoning/topology_provider.py`.

M1.0 only documents the planned interface. The later implementation should:

- expose a protocol for base topology providers and overlay providers;
- start with a Hex2D provider that reads existing repo truth;
- keep virtual 3D/4D providers benchmark-only;
- return bounded candidate sets, not unbounded graph expansions;
- preserve the 7-cell agent-routing vs 8-cell solver-retrieval distinction;
- include tests before any production caller imports it.

Latency expectation: zero runtime impact in M1.0. Interface-only PRs have zero
production impact until a caller is wired. The first wired PR must report
p99 and p99.9 for routing with wall-clock denominators.

### 2. Tunnel registry and overlay

Future modules: `tunnel_registry.py`, `tunnel_overlay.py`, and
`tunnel_lifecycle.py`.

Tunnels are sparse, provenance-bearing edges over the base topology. The M0
config starts conservative:

- `max_depth: 2`
- `max_candidates: 16`
- `max_tunnels_per_node: 4`
- `min_trust: 0.65`
- learned candidates shadow-only
- zero daily promotions by default

Overlay scoring must be deterministic, bounded, and cacheable. It must use
structured or whole-token matching, never broad substring matching, and it must
emit evidence for any promotion proposal. Negative tunnels, temporal layers,
and capability inheritance remain spike/defer candidates until poison and
state-space risks have dedicated tests.

### 3. MAGMA progressive replay and compact cards

Future modules: `progressive_replay.py` and `compact_decision_card.py`.

Raw MAGMA events stay the source of truth. Compact cards are derived pointers
and summaries. Missing, stale, or malformed cards must fall back to raw replay.
Card creation is a bounded optional write path, so ADR-011, ADR-014, and
ADR-015 apply before any production writer lands.

Replay levels should be budgeted by config:

- L0/L1 for cheap context pointers;
- L2/L3 for deeper hydration;
- L4 only for high-risk or audit paths.

Latency expectation: compact cards may reduce hydration cost only after tests
prove raw-state reconstructability, missing-card fallback, and non-blocking
reader progress under write storm.

### 4. Control plane and growth loop

The existing `ControlPlaneDB`, `RuntimeGapDetector`, and
`AutogrowthBackgroundTicker` are the near-term substrate for EIG2 learning
signals. They are also where post-M0 hardening matters most.

New EIG2 writers must be queued or moved off the request path. Each writer must
ship the three ADR-015 Option-B checks:

- existing reader non-blocking budget;
- monotonic reader progress during a write storm;
- wall-clock denominator for write-duration histograms.

Growth-loop outputs are proposals until the safety layer, replay evidence, and
promotion tests accept them. The default M0 config sets
`max_tunnel_promotions_per_day: 0`.

### 5. Bridge and orchestrator projection

Live bridge events keep their current JSONL shape. EIG2 consumes an additive
projection through `.orchestrator/eig2_bridge_projection.py`. ADR-020 is the
rule: bridge `type` is metadata, not a continuity gate. Readers scan task
continuity across all later events from the target agent.

The bridge is also part of the learning surface. The 200-option exercise found
that disagreement class, convergence latency, and correction rate are useful
future routing signals. Those signals must be appended or projected; they must
not break existing bridge tools.

### 6. Safety layer

EIG2 remains disabled by default:

- `configs/explosive_intelligence_growth_v2.yaml` has `enabled: false`;
- runtime use requires an explicit profile or test flag;
- `.eig2.halt` is an external stop file and must not be removed by autonomy;
- `.eig2.autonomous_merge` is a label-only operator-intent signal per ADR-017;
- `configs/eig2_self_modification_denylist.yaml` blocks runtime writes to the
  rule, bridge, MAGMA, safety, config, and cutover surfaces.

The no-human policy is scoped by ADR-019: implementation decisions resolve
through bridge consensus and tests, while repo safety policy and external
authority remain governed by the standing repo rules.

## Milestone map

| Milestone | Purpose | Runtime impact |
|---|---|---|
| M1.0 | Architecture map, startup snapshot, checkpoint, M0 index sealed | None |
| M1.x | First topology provider interface PR | None until imported by a production caller |
| M2 | Interfaces: topology provider, tunnel registry in-memory backend, compact-card schema | None unless explicitly wired |
| M3 | MAGMA progressive replay and compact-card writer | Must prove queue/backpressure and Option-B compliance |
| M4 | Tunnel overlay routing integration | Requires p99/p99.9 routing benchmarks and no-LLM-hot-path proof |
| M5 | Lifecycle/autonomy growth proposals | Shadow-first; promotion disabled until evidence gates pass |
| M6 | Benchmarks and scale simulation | Benchmark-only |
| M7 | Regression loop and adversarial checkpoints | Fixes only after classified evidence |
| M8 | Finalization, report, rollback notes, release naming | Governed by ADR-018 and existing release rules |

## Known post-M0 hardening backlog

These are inherited substrate risks reported by Claude RCO audit events. They
do not block M1.0 because this PR has no runtime effect, but they should be
tracked before M3/M4 consume the affected paths under load.

| ID | Area | Risk | Smallest safe direction |
|---|---|---|---|
| B7 | `autogrowth_scheduler.py` | `tick()` can leak a claimed queue row if the grower raises an unexpected exception. | Release or fail the claim in a broad exception guard, with a test injecting a failing grower. |
| B9 | `chat_service.py` and `RuntimeGapDetector` | Low-confidence gap writes are synchronous on an async chat path. | Move the detector write to `asyncio.to_thread` or a queue, then add an event-loop non-blocking test. |
| B18 | `hybrid.py` | GET topology/cell reads can create collections through `get_or_create` fallback. | Refuse read-path mutation when no non-mutating count API exists. |
| B19 | `hologram.py` | Secret redaction substring matching drops benign metrics such as token counts. | Use exact marker, boundary, or suffix matching and preserve metric fields in tests. |
| B26 | `prompt_builder.py` | `set_language` and `build_system_prompt` are not locked as the docstring claims. | Lock the full critical section or use per-language bridge instances, with a concurrent multilingual test. |

Tracking source: bridge findings `claude-rco-audit-pr261-autogrowth-scheduler-2026-05-11`,
`claude-rco-audit-pr260-chat-gap-write-2026-05-11`,
`claude-rco-audit-pr244-hexacon-harden-2026-05-11`, and
`claude-rco-audit-pr252-prompt-builder-2026-05-11`.

## M1.0 exit criteria

This PR exits M1.0 when:

- the M0 index records M0 complete at merge commit `2cc6fec...`;
- `.orchestrator/autonomous_merge_snapshot.json` records that
  `.eig2.autonomous_merge` was absent at startup;
- `.orchestrator/checkpoints/M1-initial.md` records accepted ADRs, active
  locks, modified files, feature flags, open risks, and validation commands;
- `tests/orchestrator tests/contracts` pass;
- `no_human_prompt_lint.py` reports zero findings on the new and existing EIG2
  docs/config surfaces;
- `git diff --check` passes;
- Claude RCO review has an explicit bridge or PR-review endorsement.
