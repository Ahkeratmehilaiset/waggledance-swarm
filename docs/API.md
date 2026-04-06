# WaggleDance API Reference

*API-viite — All endpoints on port 8000*

## Overview

WaggleDance exposes a REST API via FastAPI. Two modes:
- **Production** (`python start_waggledance.py`) — full runtime, real data
- **Stub** (`start_waggledance.py --stub`) — mock backend for dashboard development, no Ollama needed

### Authentication

Protected `/api/*` endpoints accept two auth methods:

1. **Bearer token** (for cURL, scripts, CI):
   ```
   Authorization: Bearer <WAGGLE_API_KEY>
   ```
2. **HttpOnly session cookie** (for browser — set automatically by dashboard):
   ```
   Cookie: waggle_session=<opaque_token>
   ```

- Token auto-generated on first startup and saved to `.env` as `WAGGLE_API_KEY`
- **Public (no auth):** `/health`, `/ready`, `/api/status`, `/api/auth/check`, `/api/feeds`, `/api/hologram/state`, `/api/capabilities/state`, `/api/learning/state-machine`
- **Session endpoints:** `POST /api/auth/session` (create, requires Bearer), `GET /api/auth/check` (public), `DELETE /api/auth/session` (logout)
- **WebSocket:** session cookie (browser) or `?token=` query parameter (scripts)
- The API key value never appears in served HTML, inline JS, or browser storage

### Rate Limits & Input Validation

Rate limit: **60 requests/min** per IP (token bucket).
Input limits: chat message 10,000 chars, voice text 5,000 chars, voice audio 10MB.

---

## Health & Readiness

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /health` | GET | Liveness probe. Returns `{"status": "ok"}` |
| `GET /ready` | GET | Readiness probe. Checks runtime running state |

```json
// GET /health
{"status": "ok"}

// GET /ready
{"status": "ready", "hivemind": true}
```

---

## Core

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/status` | GET | System status, uptime, agent count, metrics |
| `POST /api/chat` | POST | Send message to runtime (auto FI/EN detection) |
| `GET /api/heartbeat` | GET | Latest agent activity feed entries |
| `GET /api/hardware` | GET | Live CPU/GPU/VRAM/RAM stats |
| `GET /api/system` | GET | psutil CPU% + nvidia-smi GPU% |
| `POST /api/language` | POST | Set language preference |
| `GET /api/language` | GET | Get current language |
| `POST /api/confusion` | POST | Report confusing response for correction |

### Chat

```json
// POST /api/chat
// Request
{"query": "Miten optimoin energiankulutusta?"}

// Response
{
  "response": "Energiankulutuksen optimointiin...",
  "agent": "energy_advisor",
  "confidence": 0.87,
  "source": "chromadb",
  "language": "fi",
  "response_time_ms": 142
}
```

Every non-cached chat response (solver and LLM routes) creates a `CaseTrajectory`
row in the learning funnel. Hot-cache hits are excluded. The case records the
query, response, confidence, source, and route type for downstream night learning.

---

## Storage Health (v3.3.9)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/storage/health` | GET | Per-database sizes, WAL sizes, row counts, growth warnings |
| `POST /api/storage/wal-checkpoint` | POST | Trigger WAL checkpoint on all SQLite databases |

`/api/status` also returns `degraded` (bool) and `degraded_components` (list) when circuit breakers are open.
`/api/learning` returns `llm_degraded` (bool) when the LLM circuit breaker is open.

---

## Autonomy Runtime (v3.3)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/autonomy/status` | GET | Full autonomy runtime status, KPIs, resource kernel |
| `GET /api/autonomy/kpis` | GET | 13 autonomy KPIs with targets and current values |
| `POST /api/autonomy/learning/run` | POST | Trigger night learning cycle. Auto-loads pending cases from store when day_cases not provided. Watermark prevents reprocessing. |
| `GET /api/autonomy/learning/status` | GET | Night learning pipeline status: cycles, pending_cases, scheduler state, last result |
| `POST /api/autonomy/goals/check-proactive` | POST | Check world model for proactive goal opportunities |
| `GET /api/autonomy/safety-cases` | GET | Recent safety cases (optional `?limit=N`) |
| `GET /api/autonomy/capability-confidence` | GET | Per-solver capability confidence scores, trends |
| `GET /api/autonomy/prediction-ledger` | GET | Prediction error ledger analysis |
| `GET /api/autonomy/user-model` | GET | Lightweight user model: interactions, corrections, pending promises |
| `GET /api/autonomy/safety-cases/stats` | GET | Safety case verdict distribution |

```json
// GET /api/autonomy/kpis
{
  "kpis": {
    "route_accuracy": {"value": 0.92, "target": 0.90},
    "llm_fallback_rate": {"value": 0.25, "target": 0.30},
    "specialist_accuracy": {"value": 0.88, "target": 0.85}
  }
}

// POST /api/autonomy/goals/check-proactive
// Request
{"observations": {"zone1.temperature": 25.0}, "threshold": 2.0}
// Response
{"goals_proposed": 1, "goal_ids": ["goal-abc123"]}
```

---

## Autonomy v3.3 — User Model Lite

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/autonomy/user-model` | GET | User entity state + pending promises from GoalEngine |

```json
// GET /api/autonomy/user-model
{
  "available": true,
  "interaction_count": 42,
  "explicit_correction_count": 0,
  "verification_fail_count": 3,
  "promises_pending": [
    {"goal_id": "goal-abc", "description": "Fix sensor", "priority": 70, "status": "executing"}
  ],
  "preferred_language": "",
  "last_interaction_at": 1711100000.0,
  "last_user_correction_at": 0.0
}
```

---

## Autonomy v3.2 — Self-Entity & Projections

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/autonomy/epistemic-uncertainty` | GET | Uncertainty report: total uncertainty, observability gaps, per-entity uncertainty |
| `GET /api/autonomy/attention-budget` | GET | Current attention allocation across 4 buckets (critical/normal/background/reflection) |
| `GET /api/autonomy/dream-mode/latest` | GET | Latest dream session results: simulations run, insights generated |
| `GET /api/autonomy/memory/consolidation-stats` | GET | Memory consolidation stats: episodes consolidated, significance distribution |
| `GET /api/autonomy/introspection` | GET | Self-introspection snapshot (profile-gated: FACTORY=full, GADGET=counts only) |
| `GET /api/autonomy/narrative` | GET | Human-readable self-narrative in en or fi (60s cache) |

---

## Agents & Learning

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/agent_levels` | GET | All agents with current trust levels |
| `GET /api/agents/levels` | GET | All agents with level/trust/hallucination rate |
| `GET /api/agents/leaderboard` | GET | Top agents by trust, queries, reliability |
| `GET /api/consciousness` | GET | Memory engine state + user model summary (v3.3: interaction count, corrections, promises) |
| `GET /api/hologram/state` | GET | Hologram brain state: 32 nodes (4 rings), node_meta, edges, events, hex_mesh overlay, magma_timeline, ops overlay |
| `GET /hologram` | GET | Hologram brain v6 HTML page (32 nodes, docked panels, FI/EN i18n) |
| `GET /api/profile/impact` | GET | Profile impact: target environment, enabled/disabled capabilities, risk mode, learning permissions |
| `GET /api/capabilities/state` | GET | Per-family capability state: state/device/quality/source_class (shared derivation with hologram) |
| `GET /api/learning/state-machine` | GET | Current learning lifecycle state (awake/replay/consolidation/dream/training/canary/morning_report) |
| `GET /api/swarm/scores` | GET | SwarmScheduler agent scores |
| `GET /api/learning` | GET | LearningEngine status + leaderboard |
| `GET /api/ops` | GET | OpsAgent status + model recommendations |
| `GET /api/micro_model` | GET | MicroModel V1/V2 status and promotion stats |

---

## Hybrid Retrieval (v3.4)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/hybrid/status` | GET | Hybrid retrieval enabled/disabled, mode, hit/miss rates, FAISS stats |
| `GET /api/hybrid/topology` | GET | Hex-cell topology: all 8 cells, ring-1 neighbors, per-cell document counts |
| `GET /api/hybrid/cells` | GET | Per-cell FAISS collection sizes |
| `GET /api/hybrid/test-assign` | GET | Debug: test cell assignment for a query+intent (`?query=...&intent=...`) |

All hybrid endpoints require authentication (Bearer token or session cookie).

**Feature flag:** `hybrid_retrieval.enabled` in `configs/settings.yaml` (default: `false`).

When hybrid is enabled, `/api/status` and `/api/ops` include `hybrid_retrieval` section with hit counters.
`/api/hologram/state` includes additive `hybrid` overlay section.

### Hologram Mesh Observatory (v3.5.5)

`/api/hologram/state` now includes three additional sections when hex_mesh is enabled:

```json
{
  "hex_mesh": {
    "enabled": true,
    "cells": [{"id": "hub", "coord": {"q": 0, "r": 0}, "domain": "...", "state": "idle", "health_score": 1.0, "load": 0, "quarantined": false, "self_heal_pending": false, "agent_count": 41}],
    "links": [{"source": "hub", "target": "bee_ops"}],
    "active_trace": {"trace_id": "...", "origin_cell_id": "hub", "local_confidence": 0.65, "neighbor_cells": ["bee_ops"], "escalated_global": true},
    "counters": {"origin_cell_resolutions": 7, "global_escalations": 7, "neighbor_assist_resolutions": 0},
    "health": {"tracked_cells": 7, "quarantined_cells": 0}
  },
  "magma_timeline": [{"event_type": "HEX_QUERY_COMPLETED", "source": "hub", "timestamp": 1712345678}],
  "ops": {
    "llm_parallel": {},
    "hex_mesh": {"origin_cell_resolutions": 7, "global_escalations": 7},
    "cache": {"size": 1, "hits": 1, "misses": 11},
    "request_counters": {"total_queries": 0, "solver_hits": 0, "llm_calls": 0}
  }
}
```

When `hex_mesh.enabled=false`, `hex_mesh` returns `{"enabled": false}`, and other sections return empty defaults.
MAGMA timeline sanitizes entries: `api_key` and `token` fields are stripped.

## Hybrid Backfill (v3.5)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/hybrid/backfill/status` | GET | Backfill service status: running, total_runs, indexed_ids_count, last_result |
| `POST /api/hybrid/backfill/run` | POST | Trigger idempotent backfill run. Body: `{"dry_run": true, "limit": 5000}` |

All backfill endpoints require authentication. Backfill is NOT auto-run on boot — must be triggered manually via admin API.

## Solver Candidate Lab (v3.5)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/candidate_lab/status` | GET | Lab status: total_analyses, llm_available, registry stats |
| `GET /api/candidate_lab/recent` | GET | Recent solver candidates (`?limit=10`). Returns candidate_id, domain, state, confidence |

All candidate lab endpoints require authentication. The candidate lab does **NOT** auto-modify production routing. Candidates are structured specs for review only.

## Learning Accelerator (v3.5)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/learning/accelerator` | GET | Synthetic training accelerator status: total_runs, gpu_available, device_used, last_metrics |

Requires authentication. GPU acceleration is optional and off by default. CPU fallback is always safe.

`/api/status` includes additive `backfill` and `candidate_lab` summary sections.
`/api/ops` includes additive `backfill` and `accelerator` metrics sections.

## Gemma 4 Profiles (v3.5.1)

Optional dual-tier Gemma 4 model profiles. No new endpoints — metrics exposed through existing `/api/status` and `/api/ops`.

**Feature flag:** `gemma_profiles.enabled` in `configs/settings.yaml` (default: `false`).

When enabled, `/api/status` and `/api/ops` include a `gemma_profiles` section:

```json
{
  "gemma_profiles": {
    "enabled": true,
    "active_profile": "dual_tier",
    "active_fast_model": "gemma4:e4b",
    "active_heavy_model": "gemma4:26b",
    "fast_model_calls": 42,
    "heavy_model_calls": 3,
    "heavy_reasoning_calls": 3,
    "default_fallback_calls": 0,
    "gemma_fast_degraded": false,
    "gemma_heavy_degraded": false
  }
}
```

When disabled: `{"gemma_profiles": {"enabled": false}}`.

**Profiles:** `disabled` (default), `fast_only`, `heavy_only`, `dual_tier`.

**Degradation:** If a Gemma model is unavailable, falls back to the default model (phi4-mini) when `degrade_to_default: true`.

---

## Parallel LLM Dispatch (v3.5.2+)

Optional bounded concurrent LLM dispatch with per-model inflight limits. No new endpoints — metrics exposed through existing `/api/status` and `/api/ops`.

**Feature flag:** `llm_parallel.enabled` in `configs/settings.yaml` (default: `false`).

When enabled, `/api/status` and `/api/ops` include an `llm_parallel` section:

```json
{
  "llm_parallel": {
    "enabled": true,
    "queue_depth": 0,
    "inflight_total": 0,
    "inflight_fast": 0,
    "inflight_heavy": 0,
    "inflight_default": 0,
    "completed_parallel_batches": 12,
    "total_dispatched": 48,
    "total_completed": 48,
    "timeout_count": 0,
    "cancelled_count": 0,
    "deduped_requests": 2,
    "degrade_to_sequential_count": 0
  }
}
```

When disabled: `{"llm_parallel": {"enabled": false}}`.

**Configuration keys** (in `configs/settings.yaml` under `llm_parallel`):

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Master switch |
| `max_concurrent` | `4` | Global asyncio semaphore limit |
| `max_inflight_per_model` | `2` | Per-model-line semaphore (fast/heavy/default) |
| `request_timeout_s` | `120` | Timeout per LLM call |
| `round_table_parallel_first_pass` | `false` | Parallel agent first pass in round table |
| `dream_batch_parallelism` | `1` | Dream mode batch size (1 = sequential) |
| `candidate_lab_parallelism` | `1` | Candidate enrichment batch size |
| `verifier_advisory_parallelism` | `1` | Verifier advisory batch size |
| `dedupe_identical_prompts` | `true` | SHA-256 dedup of identical concurrent requests |

---

## Hex Neighbor Mesh (v3.5.4+)

Honeycomb topology for domain-aware cooperative resolution. Queries route: local cell → ring-1 neighbor assist → global/swarm → LLM. No new endpoints — metrics exposed through existing `/api/status` and `/api/ops`.

**Feature flag:** `hex_mesh.enabled` in `configs/settings.yaml` (default: `false`).

When enabled, `/api/status` and `/api/ops` include a `hex_mesh` section:

```json
{
  "hex_mesh": {
    "enabled": true,
    "cells_loaded": 7,
    "origin_cell_resolutions": 0,
    "local_only_resolutions": 0,
    "neighbor_assist_resolutions": 0,
    "global_escalations": 0,
    "llm_last_resolutions": 0,
    "completed_hex_neighbor_batches": 0,
    "neighbors_consulted_total": 0,
    "avg_neighbors_per_assist": 0.0,
    "quarantined_cells": 0,
    "self_heal_events": 0,
    "magma_traces_written": 0,
    "ttl_exhaustions": 0
  }
}
```

When disabled: `{"hex_mesh": {"enabled": false, "cells_loaded": 7, ...}}` (counters stay 0).

**Configuration keys** (in `configs/settings.yaml` under `hex_mesh`):

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Master switch |
| `cell_config_path` | `configs/hex_cells.yaml` | Topology definition file |
| `local_confidence_threshold` | `0.72` | Min confidence for local-only resolution |
| `neighbor_confidence_threshold` | `0.82` | Min confidence for neighbor-merged resolution |
| `global_escalation_threshold` | `0.90` | Min confidence to avoid global escalation |
| `ttl_default` | `2` | Hop limit to prevent loops |
| `max_neighbors_per_hop` | `2` | Max ring-1 neighbors consulted per hop |
| `parallel_neighbor_assist` | `true` | Use ParallelLLMDispatcher for neighbor queries |
| `self_heal_probe_enabled` | `true` | Probe quarantined cells for recovery |
| `magma_trace_enabled` | `true` | Record MAGMA events for hex resolution |
| `neighbor_merge_policy` | `weighted_confidence` | Merge policy for neighbor responses |

**Topology** (defined in `configs/hex_cells.yaml`):

7 cells in axial coordinates: hub (0,0), bee_ops (1,0), environment (0,-1), home_comfort (-1,0), safety_security (-1,1), production (0,1), logistics (1,-1).

---

## Analytics

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/analytics/trends` | GET | 7-day performance trends (halluc, cache, RT) |
| `GET /api/analytics/routes` | GET | Route breakdown (cache/memory/LLM) |
| `GET /api/analytics/models` | GET | Model usage percentages |
| `GET /api/analytics/facts` | GET | Fact growth timeline |

---

## Round Table

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/round-table/recent` | GET | Latest Round Table discussions with transcripts |
| `GET /api/round-table/stats` | GET | Aggregate stats, most active agents |

---

## Sensors & IoT

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/sensors` | GET | Sensor hub overview (all subsystems) |
| `GET /api/sensors/home` | GET | Home Assistant entity states |
| `GET /api/sensors/camera/events` | GET | Frigate NVR camera events |
| `GET /api/sensors/audio` | GET | Audio monitor status + recent events |
| `GET /api/sensors/audio/analysis` | GET | Audio analysis (anomaly detection, pattern recognition) |

---

## Voice Interface

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/voice/status` | GET | Voice interface status (STT/TTS availability) |
| `POST /api/voice/text` | POST | Send text for TTS synthesis |
| `POST /api/voice/audio` | POST | Send audio for STT transcription |

```json
// POST /api/voice/text
// Request
{"text": "Tervetuloa järjestelmään"}

// POST /api/voice/audio
// Request
{"audio_base64": "<base64 WAV data>"}
```

---

## Data Feeds

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/feeds` | GET | Data feed status (weather, electricity, RSS) |
| `POST /api/feeds/{feed_name}/refresh` | POST | Force refresh a specific feed |

---

## Settings

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/settings` | GET | Feature toggles from settings.yaml |
| `POST /api/settings/toggle` | POST | Toggle a feature on/off |

```json
// POST /api/settings/toggle
{"feature": "voice", "enabled": true}
```

---

## MAGMA Memory Architecture

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/magma/stats` | GET | Audit log + replay store statistics |
| `GET /api/magma/audit` | GET | Last 24h audit entries |
| `GET /api/magma/audit/agent/{agent_id}` | GET | Audit entries for specific agent |
| `GET /api/magma/overlays` | GET | Active memory overlays |
| `GET /api/magma/branches` | GET | Overlay branches and status |
| `POST /api/magma/branches/{name}/activate` | POST | Activate an overlay branch |
| `POST /api/magma/branches/deactivate` | POST | Deactivate current branch |

---

## Trust & Reputation

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/trust/ranking` | GET | All agents ranked by reputation score |
| `GET /api/trust/agent/{agent_id}` | GET | Full reputation breakdown for agent |
| `GET /api/trust/domain/{domain}` | GET | Top agents for a domain |
| `GET /api/trust/signals/{agent_id}` | GET | Raw trust signal history |

---

## Cross-Agent Memory

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/cross/channels` | GET | Agent communication channels |
| `GET /api/cross/channels/{name}/history` | GET | Channel message history |
| `GET /api/cross/provenance/{fact_id}` | GET | Fact provenance chain |
| `GET /api/cross/agent/{agent_id}/contributions` | GET | Agent's memory contributions |
| `GET /api/cross/consensus` | GET | Consensus records |

---

## Cognitive Graph

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/graph/node/{node_id}` | GET | Node details + edges |
| `GET /api/graph/path/{source}/{target}` | GET | Shortest path between nodes |
| `GET /api/graph/stats` | GET | Graph node/edge counts |

---

## Reports & Code Review

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/meta_report` | GET | Latest weekly meta-learning report |
| `GET /api/code_suggestions` | GET | Code self-review suggestions |
| `POST /api/code_suggestions/{index}/accept` | POST | Accept a suggestion |
| `POST /api/code_suggestions/{index}/reject` | POST | Reject a suggestion |
| `GET /api/code-review` | GET | Code review status |
| `GET /api/code-review/suggestions` | GET | Full suggestions list |

---

## Dashboard Features

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/models` | GET | Ollama model status — loaded models, VRAM usage, role mapping |
| `GET /api/history` | GET | Conversation history list |
| `GET /api/history/recent/messages` | GET | Recent chat messages across all conversations |
| `GET /api/history/{conversation_id}` | GET | Full conversation by ID |
| `POST /api/feedback` | POST | User feedback on AI response (thumbs up/down) |

```json
// GET /api/models
{
  "models": [
    {"name": "phi4-mini:latest", "role": "chat", "vram_mb": 2400, "loaded": true}
  ],
  "total_vram_used_mb": 4200,
  "total_vram_available_mb": 8192
}

// POST /api/feedback
// Request
{"message_id": "msg_abc123", "rating": "up", "comment": "Good answer"}
```

---

## Profiles

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/profile` | GET | Current deployment profile |
| `POST /api/profile` | POST | Switch deployment profile |
| `GET /api/monitor/history` | GET | Performance monitoring history |

---

## WebSocket

```
ws://localhost:8000/ws
```

Real-time event stream. Messages are JSON with `type` field:

| Type | Description |
|------|-------------|
| `heartbeat` | Agent activity update |
| `chat_response` | Streaming chat response |
| `round_table_start` | Round Table debate begins |
| `round_table_turn` | Agent contribution in debate |
| `round_table_end` | Debate conclusion + consensus |
| `night_learning` | Night mode learning event |
| `alert` | Sensor/system alert |
| `ops_decision` | OpsAgent scaling decision |

```json
// Example heartbeat message
{
  "type": "heartbeat",
  "agent": "disease_monitor",
  "action": "proactive_think",
  "result": "Checked scheduled maintenance timing",
  "timestamp": "2026-03-07T14:30:00"
}
```
