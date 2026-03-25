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
{"message": "Miten optimoin energiankulutusta?"}

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

---

## Autonomy Runtime (v3.3)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /api/autonomy/status` | GET | Full autonomy runtime status, KPIs, resource kernel |
| `GET /api/autonomy/kpis` | GET | 13 autonomy KPIs with targets and current values |
| `POST /api/autonomy/learning/run` | POST | Trigger night learning cycle (optional day_cases, legacy_records) |
| `GET /api/autonomy/learning/status` | GET | Night learning pipeline status and history |
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
| `GET /api/hologram/state` | GET | Hologram brain state: 32 nodes (4 rings), node_meta (state/device/freshness/source_class/quality), edges, events |
| `GET /hologram` | GET | Hologram brain v6 HTML page (32 nodes, docked panels, FI/EN i18n) |
| `GET /api/profile/impact` | GET | Profile impact: target environment, enabled/disabled capabilities, risk mode, learning permissions |
| `GET /api/capabilities/state` | GET | Per-family capability state: state/device/quality/source_class (shared derivation with hologram) |
| `GET /api/learning/state-machine` | GET | Current learning lifecycle state (awake/replay/consolidation/dream/training/canary/morning_report) |
| `GET /api/swarm/scores` | GET | SwarmScheduler agent scores |
| `GET /api/learning` | GET | LearningEngine status + leaderboard |
| `GET /api/ops` | GET | OpsAgent status + model recommendations |
| `GET /api/micro_model` | GET | MicroModel V1/V2 status and promotion stats |

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
