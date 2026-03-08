# Dashboard Improvements Plan

## Section 1: Profile System — Current State vs Target State

### Current State

The profile system (`gadget | cottage | home | factory`) is **partially functional**:

| Aspect | Works? | Details |
|--------|--------|---------|
| Agent filtering | YES | `core/yaml_bridge.py:567-570` filters agents by `profiles:` in each agent's `core.yaml` |
| Weather feed locations | YES | `configs/settings.yaml:159-168` has per-profile locations |
| Model selection | NO | Same `phi4-mini` / `llama3.2:1b` for all profiles |
| Round Table composition | NO | Fixed `agent_count: 6` for all profiles |
| Routing behavior | NO | `WEIGHTED_ROUTING` is profile-agnostic |
| Dashboard profile selector | NO | Backend API exists (`GET/POST /api/profile`), frontend doesn't call it |
| Domain tabs in UI | COSMETIC | `App.jsx:1105` switches demo data, not actual profile |

### Target State

Each profile should represent a meaningfully different deployment:

#### GADGET (ESP32 / RPi Zero, 5W)
- **Agents**: 2-3 (beekeeper, core_dispatcher, meteorologist)
- **Primary responder**: beekeeper (single-purpose device)
- **Models**: qwen3:0.6b or TinyML (CPU-only)
- **Round Table**: Disabled (too few agents)
- **Use case**: Single-hive monitoring sensor

#### COTTAGE (Intel NUC / ZBook, 28W)
- **Agents**: 8-15 (bee monitoring + property + weather)
- **Primary responder**: core_dispatcher (routes to specialists)
- **Models**: phi4-mini (chat), llama3.2:1b (background), nomic-embed-text
- **Round Table**: 3-4 agents, every 20 heartbeats
- **Use case**: Hobby beekeeper's summer cottage

#### HOME (Mac Mini Pro / RTX workstation, 30W)
- **Agents**: 20-25 (full specialist roster)
- **Primary responder**: core_dispatcher with embedding-based routing
- **Models**: phi4:14b (chat), llama3.3:8b (background), whisper + Piper (voice)
- **Round Table**: 6 agents, every 10 heartbeats
- **Use case**: Smart home with beekeeping + property management

#### FACTORY (NVIDIA DGX, 14.4kW)
- **Agents**: 40-50 (all agents, multiple instances)
- **Primary responder**: core_dispatcher with full embedding + centroid routing
- **Models**: llama3.3:70b (chat), vision models, 50 micro-models
- **Round Table**: 8-10 agents, every 5 heartbeats
- **Use case**: Commercial apiary / research facility

### Gap Analysis

```
GAP 1: Model selection is hardcoded in settings.yaml, not profile-dependent
  → Need profile-specific model defaults in settings.yaml or profile configs
  → Files: configs/settings.yaml, hivemind.py (LLM init)

GAP 2: Round Table config is fixed for all profiles
  → Need profile-specific agent_count, min_agents, every_n_heartbeat
  → Files: configs/settings.yaml, hivemind.py (round table init)

GAP 3: Dashboard domain tabs are cosmetic, not wired to backend profile API
  → Frontend sw() function only changes visual theme
  → Need to wire domain tabs to POST /api/profile or display actual profile
  → Files: dashboard/src/App.jsx, dashboard/src/hooks/useApi.js

GAP 4: No profile-specific routing weights
  → GADGET should not route to 46 agents it can't spawn
  → Files: core/hive_routing.py, hivemind.py
```

---

## Section 2: Missing Features — Priority Order

### Priority 1: Profile switching changes agent behavior (L) — IMPLEMENTED (v0.7.0)

**Status**: Implemented. Dashboard domain tabs wired to `POST /api/profile`, Round Table respects active profile agent count.

**Files changed**: `dashboard/src/App.jsx`, `dashboard/src/hooks/useApi.js`, `web/dashboard.py`, `hivemind.py`

### Priority 2: Model status display (M) — IMPLEMENTED (v0.7.0)

**Status**: Implemented. `GET /api/models` queries Ollama API for loaded models, VRAM usage, role mapping. Dashboard shows ModelStatus panel with real-time utilization.

**Files changed**: `web/dashboard.py`, `backend/routes/models.py`, `dashboard/src/App.jsx`, `dashboard/src/hooks/useApi.js`

### Priority 3: Language toggle FI/EN (DONE)

**Status**: Already implemented and working.
- Toggle button in `App.jsx:1108-1111`
- Backend sync via `POST /api/language`
- Full bilingual UI with `L.en` / `L.fi` text objects

### Priority 4: Chat history and feedback (M) — IMPLEMENTED (v0.7.0)

**Status**: Implemented. `core/chat_history.py` provides SQLite storage. Endpoints: `GET /api/history`, `GET /api/history/recent/messages`, `GET /api/history/{id}`, `POST /api/feedback`. Dashboard persists conversations across page refresh, thumbs up/down on every AI response feeds into corrections memory.

**Files changed**: `core/chat_history.py` (new), `web/dashboard.py`, `dashboard/src/App.jsx`, `dashboard/src/hooks/useApi.js`

---

## Section 3: Backend API Endpoints Needed

### Existing Endpoints (already implemented)

```
GET  /api/profile          → {active_profile, profiles[], agent_counts{}}
POST /api/profile          → {profile, message}  (updates settings.yaml)
GET  /api/language          → {language: "auto"|"fi"|"en"}
POST /api/language          → {language: mode}
POST /api/chat              → {response: str}
GET  /api/hardware          → {cpu, gpu, vram, vram_total, ram_gb, ...}
GET  /api/consciousness     → {facts, corrections_count, ...}
```

### New Endpoints Needed

#### `GET /api/models` — Loaded models and roles

```json
{
  "models": [
    {
      "name": "phi4-mini:latest",
      "role": "chat",
      "size_gb": 2.4,
      "vram_mb": 2400,
      "quantization": "Q4_K_M",
      "loaded": true,
      "last_used": "2026-03-08T11:29:00Z"
    },
    {
      "name": "nomic-embed-text:latest",
      "role": "embedding",
      "size_gb": 0.3,
      "vram_mb": 280,
      "quantization": "F16",
      "loaded": true,
      "last_used": "2026-03-08T11:28:45Z"
    }
  ],
  "total_vram_used_mb": 4200,
  "total_vram_available_mb": 8192
}
```

**Implementation**: Query `http://localhost:11434/api/ps` (Ollama loaded models) + cross-reference with `settings.yaml` role assignments.

#### `GET /api/history` — Conversation history

```json
{
  "conversations": [
    {
      "id": "conv_001",
      "timestamp": "2026-03-08T11:29:00Z",
      "messages": [
        {"role": "user", "text": "miten varroa-punkkia torjutaan?", "timestamp": "..."},
        {"role": "assistant", "text": "[Beekeeper] Varroa-torjunta...", "agent": "beekeeper", "timestamp": "..."}
      ]
    }
  ],
  "total": 42,
  "page": 1,
  "per_page": 20
}
```

**Implementation**: New SQLite table `chat_history` in `waggle_dance.db`. Store each request/response pair with agent attribution.

#### `POST /api/feedback` — User feedback on responses

```json
// Request
{
  "message_id": "msg_abc123",
  "rating": "up",
  "correction": {
    "wrong_agent": "meteorologist",
    "correct_agent": "beekeeper"
  },
  "comment": "This should have gone to beekeeper"
}

// Response
{
  "status": "recorded",
  "message_id": "msg_abc123"
}
```

**Implementation**: Extends existing confusion_memory system. Stores feedback in `data/user_feedback.jsonl`. Optionally updates routing weights.

#### `GET /api/profile/config` — Profile-specific configuration

```json
{
  "profile": "cottage",
  "config": {
    "max_agents": 15,
    "models": {
      "chat": "phi4-mini",
      "heartbeat": "llama3.2:1b",
      "embedding": "nomic-embed-text"
    },
    "round_table": {
      "agent_count": 4,
      "every_n_heartbeat": 20
    },
    "features": {
      "voice": false,
      "night_learning": true,
      "feeds": true
    }
  }
}
```

**Implementation**: Read from new `configs/profiles/` directory or profile-specific sections in `settings.yaml`.
