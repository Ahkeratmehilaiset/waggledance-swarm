# WaggleDance Codebase Review
*Reviewer: Claude Sonnet 4.6 | Date: 2026-03-09 | Version: 0.7.0*

---

## Executive Summary

- **hivemind.py is critically oversized at 3410 lines** — it contains orchestration, chat routing, learning cycles, night mode, round table, heartbeat scheduling, and WebSocket handling that belong in separate modules. This is the single largest maintainability and testability risk in the codebase.
- **Blocking synchronous HTTP calls inside async coroutines** exist throughout the startup path (`_readiness_check`, `EmbeddingEngine._check_available`, `EmbeddingEngine._raw_embed`) — these will stall the event loop for up to 30 seconds during Ollama timeouts.
- **The API key is embedded in the root HTML page as a plaintext JavaScript string** (`localStorage.setItem("WAGGLE_API_KEY", "{_api_key}")`), meaning any user who views page source or any browser extension that reads the DOM will capture the token. The public `/api/auth/token` endpoint returns the key with zero authentication — anyone who can reach the server gets admin access.
- **AuditLog and TrustEngine share one SQLite connection per instance with no write mutex**, used from both asyncio tasks and threads (`check_same_thread=False` suppresses the error but does not prevent corruption under concurrent writes).
- **The legacy `_inject_knowledge` / `_restore_prompt` pair is still called in `_whisper_cycle`** alongside the new `_enriched_prompt` context manager, creating two separate mutation patterns for the same agent state that can race in the heartbeat loop.

---

## Critical Issues (fix immediately)

### 1. Blocking HTTP inside async coroutines — event loop stall
**Files:** `hivemind.py:254–290`, `core/memory_engine.py:189–229`, `core/memory_engine.py:388–423`, `core/memory_engine.py:440–457`, `core/memory_engine.py:504–520`

`_readiness_check` is declared `async` but calls `requests.get(...)` and `requests.post(...)` synchronously. `EmbeddingEngine._raw_embed` and `_check_available` also call `requests.post` synchronously. These are called from the asyncio event loop. A 30-second Ollama timeout will block all other coroutines for the full timeout duration, hanging the heartbeat loop, WebSocket pings, and the chat API simultaneously.

The fix is `asyncio.to_thread()` (Python 3.9+) or `loop.run_in_executor(None, ...)`. At minimum, the readiness check should use `asyncio.to_thread`. The embedding engine is more complex because it's also called from sync context, but the primary call paths from async code need wrapping.

### 2. API key exposed in HTML page source
**File:** `web/dashboard.py:95`

```python
html = f"""...<script>localStorage.setItem("WAGGLE_API_KEY","{_api_key}")</script>..."""
```

The bearer token is injected literally into the HTML returned by `GET /`. The `/api/auth/token` endpoint (`web/dashboard.py:892–895`, `backend/main.py:60–63`) also returns the key to any unauthenticated caller. The rationale is "localhost only", but the CORS policy allows `http://localhost:3000` and `http://localhost:5173`, meaning any tab on the developer's machine with JavaScript code can silently exfiltrate the key. If the machine is on a network, any local service can reach port 8000.

The safer approach is to serve the key only via a short-lived session cookie (HttpOnly, SameSite=Strict) rather than injecting it into page source.

### 3. SQLite connections used concurrently without write lock
**Files:** `core/audit_log.py:22`, `core/trust_engine.py:114`

Both `AuditLog` and `TrustEngine` open a single `sqlite3.connect(..., check_same_thread=False)` connection and call `self._conn.execute(...); self._conn.commit()` from multiple asyncio tasks and from the MQTT handler thread (which runs in a `threading.Thread`). SQLite's WAL mode makes concurrent reads safe, but concurrent writes from multiple threads sharing one connection object can corrupt the WAL and cause `OperationalError: database is locked` under load. The fix is a `threading.Lock()` around write operations, or using a connection pool (`aiosqlite` for async paths).

### 4. `asyncio.get_event_loop()` deprecated usage across integrations
**Files:** `hivemind.py:647`, `hivemind.py:2838–2844`, `integrations/mqtt_hub.py:216`, `integrations/piper_tts.py:74,105`, `integrations/rss_feed.py:56`, `integrations/whisper_stt.py:120`

`asyncio.get_event_loop()` is deprecated since Python 3.10 and raises `DeprecationWarning` when called outside a running loop in Python 3.12+. In Python 3.14 it will raise `RuntimeError`. `hivemind.py:2838` calls `loop.run_until_complete()` inside `_emit_morning_report`, which is called from the heartbeat loop (already inside a running loop) — this will raise `RuntimeError: This event loop is already running`. The pattern at `hivemind.py:2843` (`loop.run_until_complete(self._notify_ws(...))`) inside a `try/except RuntimeError: pass` silently swallows the failure: morning reports never send WebSocket notifications.

Use `asyncio.get_running_loop()` when inside async context, and `asyncio.create_task()` for fire-and-forget from within the running loop.

### 5. SQL injection risk in `update_task`
**File:** `memory/shared_memory.py:209–212`

```python
async def update_task(self, task_id, **kwargs):
    for key, value in kwargs.items():
        await self._db.execute(
            f"UPDATE tasks SET {key} = ? WHERE id = ?", (value, task_id)
        )
```

The column name `key` is interpolated directly into the SQL string with an f-string. While `value` is parameterized, `key` is not. Any caller who controls the key names can inject arbitrary SQL. This pattern also appears in `tools/setup_runtime.py:713`. Internal callers currently pass known column names, but the interface is public-looking — a future caller could pass `key = "status=? WHERE 1=1 -- "` and execute unexpected updates.

Mitigation: maintain an explicit allowlist of valid column names and validate `key` against it before interpolation.

---

## High Priority (fix soon)

### 6. `_whisper_task` not awaited on disconnect — resource leak
**File:** `hivemind.py:725–731`

`_whisper_task` is cancelled in `stop()`, but `_whisper_task` is created with `asyncio.create_task(self._whisper_cycle(agents_dict))` at startup and runs exactly once — it is not restarted. The whisper cycle appears to be a one-shot initialization task, not a long-running loop, so cancelling it in stop() is harmless, but there is no `add_done_callback` to clean up from `_background_tasks`, meaning if it raises an exception it becomes a silently lost exception. Additionally, callbacks registered to `hivemind.monitor` and `hivemind.ops_agent` at the WebSocket endpoint (`web/dashboard.py:1305–1312`) are never unregistered on WebSocket disconnect — only `ws_callback` is unregistered; `monitor_ws_callback` remains in `LiveMonitor`'s callback list and will throw silently on the closed socket.

### 7. Two KnowledgeLoader instances created at startup
**File:** `hivemind.py:366–370`

```python
self.knowledge = KnowledgeLoader("knowledge")          # line 366
...
self.knowledge_loader = KnowledgeLoader()              # line 370
```

Two separate `KnowledgeLoader` instances are created — one with the `"knowledge"` path, one with the default path (which may be identical). Both load the same YAML files from disk. This doubles the memory footprint of YAML knowledge bases (potentially megabytes) and causes double disk reads at startup. Only `self.knowledge_loader` is used in `_enriched_prompt` and `_inject_knowledge`; `self.knowledge` is used only in `get_status()` for listing. Consolidate to one instance.

### 8. `_inject_knowledge` / `_restore_prompt` called in `_whisper_cycle` (legacy mutation pattern still active)
**File:** `hivemind.py:3316–3329`

```python
async def _whisper_cycle(self, agents):
    _originals = {}
    for _a in agent_list:
        _o = self._inject_knowledge(_a, max_chars=800)
        if _o is not None:
            _originals[id(_a)] = (_a, _o)
    try:
        await self.whisper.auto_whisper_cycle(agent_list, self.llm)
    finally:
        for _a, _o in _originals.values():
            self._restore_prompt(_a, _o)
```

This mutates agent `system_prompt` directly during `auto_whisper_cycle`. If any heartbeat-scheduled task (e.g., `_agent_proactive_think`) runs concurrently with `_whisper_cycle` and reads the same agent's `system_prompt`, it may get the injected (enriched) prompt or the restored (original) prompt depending on timing. The `_enriched_prompt` context manager was introduced to fix this exact race, but `_whisper_cycle` was not migrated. As both tasks are launched via `_track_task` and run concurrently, this is a real race condition on multi-agent setups.

### 9. WebSocket disconnect leaves monitor and ops callbacks registered
**File:** `web/dashboard.py:1296–1317`

When a WebSocket client disconnects, only `hivemind.unregister_ws_callback(ws_callback)` is called. The `monitor_ws_callback` registered at line 1310 and the `ws_callback` registered with `hivemind.ops_agent` at line 1312 are never unregistered. Over multiple browser reconnections, the callback lists in `LiveMonitor` and `OpsAgent` grow unboundedly. Every subsequent event triggers all stale callbacks, each of which will fail silently (the `try/except Exception: pass` in `ws_callback`) but still consumes CPU.

### 10. `data/learning_progress.json` written without atomic write
**File:** `hivemind.py:3200–3203`

```python
with open("data/learning_progress.json", "w", encoding="utf-8") as f:
    json.dump(progress, f, ensure_ascii=False, indent=2)
```

This overwrites the file in place. If the process is killed or power is lost mid-write, the file is left partially written or empty. On the next startup `_load_persisted_facts_count()` will silently return 0 (losing the counter). The `CognitiveGraph.save()` correctly uses `tempfile.mkstemp + os.replace` for atomic writes — apply the same pattern here and in `_save_learning_progress`.

### 11. Silent swallowing of broad exceptions hides bugs
**File:** `hivemind.py` (30+ occurrences of bare `except Exception: pass`)

Examples: lines 745, 777, 1057, 1500, 2312, 2422, 2458, 2484, 2508, 2519, 3185, 3203, 3300, 3393.

Bare `except Exception: pass` in `_log_finetune`, `_save_learning_progress`, and multiple heartbeat task bodies means exceptions from ChromaDB corruption, disk full errors, and LLM provider failures are silently swallowed. The pattern is appropriate in some places (e.g., WebSocket callbacks must not crash the server), but in learning and persistence code it means bugs are invisible until data is visibly wrong. At minimum, log the exception at `DEBUG` or `WARNING` level even when not re-raising.

### 12. `SensorHub` constructed with deprecated `asyncio.get_event_loop()`
**File:** `hivemind.py:647`

```python
self.sensor_hub = SensorHub(
    config=sensor_cfg,
    consciousness=self.consciousness,
    loop=asyncio.get_event_loop(),     # deprecated
)
```

This is inside `async def start()`, so `asyncio.get_running_loop()` should be used instead.

---

## Medium Priority (technical debt)

### 13. `_do_chat` is 400+ lines — needs decomposition
**File:** `hivemind.py:864–1266`

`_do_chat` handles: correction detection, user teaching detection, direct datetime answers, memory pre-filter, smart router (Tier 1), smart router (Tier 2), Finnish-to-English translation, memory storage, routing rule lookup, swarm routing, legacy routing, negative keyword delegate, master agent fallback, EN validation, EN-to-Finnish translation, hallucination check, conversation learning, hot cache population, WebSocket notification, metrics logging, and correction tracking. This is 17 distinct responsibilities in one method. It is very difficult to test individual paths without running the full method. Extract: `_handle_correction`, `_handle_user_teaching`, `_handle_datetime`, `_handle_prefilter`, `_route_and_respond`.

### 14. `start()` method is 360+ lines with deeply nested initialization
**File:** `hivemind.py:321–705`

The startup sequence is a single long method with 15+ nested try/except blocks. Adding a new subsystem requires editing this method. A phased init approach (each subsystem in its own `_init_*` method, called sequentially) would improve readability and testability, similar to what was done with `_init_learning_engines`.

### 15. `TrustEngine.get_all_reputations()` is O(n) DB queries
**File:** `core/trust_engine.py:189–208`

```python
agents = self._conn.execute("SELECT DISTINCT agent_id FROM trust_signals").fetchall()
for (aid,) in agents:
    rep = self.compute_reputation(aid)  # each calls another SELECT
```

`compute_reputation` issues a `SELECT ... LIMIT 1200` per agent. With 75 agents, this is 75 separate DB queries. This is called from `get_status()` (via `_trust_engine.get_ranking()`) on every `/api/status` poll (every 12 seconds). Add a short TTL cache (e.g., 60 seconds) on `get_ranking()` results.

### 16. `get_manifest` with `session_id` loads all audit entries
**File:** `core/replay_engine.py:29–33`

```python
all_entries = self.audit.query_by_time_range(0, float("inf"), agent_id=agent_id)
entries = [e for e in all_entries if e["session_id"] == session_id]
```

When filtering by `session_id`, the code first fetches all audit entries (potentially thousands) into memory, then filters in Python. The `audit` table already has a `session_id` column and an index on it. This should be a `WHERE session_id=?` query.

### 17. `CognitiveGraph.find_dependents` uses `list.pop(0)` — O(n) per pop
**File:** `core/cognitive_graph.py:152–166`

```python
queue = [(node_id, 0)]
while queue:
    current, depth = queue.pop(0)    # O(n) per call
```

`list.pop(0)` is O(n) on a Python list. For large graphs this makes the BFS O(n²). Use `collections.deque` with `popleft()` for O(1). The same issue exists in `find_ancestors` at line 176.

### 18. Dashboard `GET /` inlines the entire HTML+CSS+JS as a Python f-string
**File:** `web/dashboard.py:92–574`

The root endpoint constructs a 480-line HTML/CSS/JS string as a Python f-string. This makes the frontend code extremely hard to maintain, impossible to syntax-highlight or lint, and requires restarting the Python server to iterate on UI changes. A static file served via `StaticFiles` or a simple template would be far more maintainable.

### 19. `_readiness_check` imports `requests` inside the function body twice
**File:** `hivemind.py:253`, `hivemind.py:269`

`import requests` appears twice at lines 253 and 269 inside the same function. Python caches imports after the first one, so this is not a bug, but it is confusing and should be a single top-level or at least single-location import.

### 20. `knowledge` and `knowledge_loader` attributes diverge
**File:** `hivemind.py:100–101`, `366–370`

```python
self.knowledge: Optional[KnowledgeLoader] = None      # __init__
self.knowledge_loader: Optional[KnowledgeLoader] = None   # __init__
```

Two separate attributes for the same type, with documentation comments and usage patterns that overlap. The docs reference `get_knowledge()` as a backward-compat wrapper. Both attributes exist in `get_status()`. This is confusing: callers do not know which to use.

### 21. `MathSolver` safe eval uses `eval()` with a whitelist — still risky
**File:** `core/memory_engine.py:846–899`

```python
SAFE_NAMES = {"sqrt": math.sqrt, "abs": abs, ...}
```

The math solver uses `eval()` restricted by `SAFE_NAMES`. While the whitelist approach is standard, if any future code adds a callable to `SAFE_NAMES` that itself accepts arbitrary callables (e.g., `map`, `filter`), or if the expression language is extended, the sandbox can be bypassed. The current set looks safe, but the pattern is fragile. Consider using `ast.literal_eval` or a dedicated expression parser like `asteval` for long-term safety.

### 22. `_ws_callbacks` is a plain list mutated from multiple concurrent coroutines
**File:** `hivemind.py:125`, `hivemind.py:3398–3402`

`_notify_ws` iterates `self._ws_callbacks` while `register_ws_callback` and `unregister_ws_callback` can mutate it concurrently (e.g., WebSocket client connects while a heartbeat notification is being broadcast). Python list iteration is not atomic. The `unregister_ws_callback` creates a new list (`[cb for cb in ...]`) which is fine, but if a registration happens mid-iteration in `_notify_ws`, the behavior is undefined. A `copy()` at the start of `_notify_ws` would be sufficient protection.

### 23. MAGMA route registrations silently fail on import error
**File:** `web/dashboard.py:1319–1345`

```python
try:
    from backend.routes.magma import register_magma_routes
    register_magma_routes(app, hivemind)
except Exception:
    pass
```

All four MAGMA route registrations are wrapped in `except Exception: pass`. If the route module has a syntax error or missing dependency, the routes are silently not registered, with no log message. Downstream tests and monitoring assume those endpoints exist. Add at minimum `log.warning(f"MAGMA routes not loaded: {e}")`.

---

## Low Priority / Nice to Have

### 24. `_CORRECTION_WORDS` and `_CORRECTION_PHRASES` rebuilt every chat call
**File:** `hivemind.py:877–879`

These sets/frozensets are constructed inside `_do_chat` on every invocation:
```python
_CORRECTION_WORDS = {"ei", "väärin", "wrong", ...}
_CORRECTION_PHRASES = {"ei vaan", "oikea vastaus", ...}
```
Move them to module-level constants.

### 25. Version string in module docstring is frozen at `v0.0.2`
**File:** `hivemind.py:3–5`

The file header says `v0.0.2` and `Built: 2026-02-22` while the project is at v0.7.0. This is misleading and suggests the docstring has not been updated with the codebase.

### 26. `_fix_mojibake` called on every `get_status()` invocation for all agents
**File:** `hivemind.py:1650–1653`

`_fix_mojibake` is called in a loop over all agents in `get_status()`, which is polled every 12 seconds by the dashboard. The mojibake fix should happen once at agent creation time (in the spawner), not on every status read.

### 27. `update_task` in `shared_memory.py` commits once per key/value pair
**File:** `memory/shared_memory.py:208–213`

If `update_task` is called with 5 keyword arguments, it issues 5 separate UPDATE statements and 5 COMMITs. Batch them into a single transaction.

### 28. `hive_support.py` `StructuredLogger` opens and closes file on every log call
**File:** `core/hive_support.py:86–88`, `104–106`

Each `log_chat()` and `log_learning()` call opens the `.jsonl` file, writes one line, and closes it. Under high chat load this causes O(n) file open/close syscalls. A buffered writer or a background thread that drains a queue would be more efficient.

### 29. Hardcoded `http://localhost:11434` Ollama URL in readiness check
**File:** `hivemind.py:254`, `hivemind.py:270`

The readiness check hardcodes the Ollama URL even though it is already read from config at line 487: `self.config.get('ollama', {}).get('base_url', 'http://localhost:11434')`. The readiness check should use the configured URL.

### 30. Tests use `asyncio.get_event_loop().run_until_complete()` directly
**Files:** `tests/test_phase9.py` (13 occurrences), `tests/test_phase4ijk.py`, `tests/test_smart_home.py`, others

Modern pytest style with `pytest-asyncio` and `@pytest.mark.asyncio` or `asyncio.run()` is cleaner and avoids the `DeprecationWarning`. Some test files still use the deprecated `get_event_loop().run_until_complete()` pattern.

---

## Architecture Observations

### Overall Structure

The codebase demonstrates a well-considered layered architecture: integrations sit at the outer edge, the core domain logic lives in `core/`, agents in `agents/`, and `hivemind.py` orchestrates everything. The MAGMA memory architecture (audit → replay → cognitive graph → trust) is a genuinely sophisticated append-only audit trail with replay capability, and the quality gate + source manager pattern in `night_enricher.py` is clean and extensible. The circuit breaker pattern in `memory_engine.py` and the `PriorityLock` mechanism in `hive_support.py` show careful thinking about resource contention.

### hivemind.py Size

The most pressing structural issue is that `hivemind.py` at 3410 lines contains too many concerns. The Phase 5 refactor extracted `hive_routing.py` and `hive_support.py`, which was the right direction, but the main file remains overfull. Recommended extractions:

- **`core/chat_handler.py`** — `_do_chat`, `_delegate_to_agent`, `_swarm_route`, `_legacy_route`, `_is_valid_response`, `_populate_hot_cache` (~600 lines)
- **`core/night_mode.py`** — `_check_night_mode`, `_night_learning_cycle`, `_init_learning_engines`, `_maybe_weekly_report`, `_emit_morning_report`, `_get_night_mode_interval`, `_save_learning_progress`, `_load_persisted_facts_count` (~350 lines)
- **`core/round_table.py`** — `_round_table`, `_round_table_v2`, `_select_round_table_agents`, `_theater_stream_round_table` (~300 lines)
- **`core/heartbeat.py`** — `_heartbeat_loop`, `_agent_proactive_think`, `_master_generate_insight`, `_agent_reflect`, `_idle_research`, `_oracle_consultation_cycle`, `_oracle_research_cycle` (~400 lines)

This would leave `hivemind.py` at approximately 1500 lines, focused on initialization, lifecycle (`start`/`stop`), and public API delegation.

### Component Coupling

The `Consciousness` class in `memory_engine.py` is used directly by `hivemind.py` with deep attribute access (`self.consciousness.embed.cache_hit_rate`, `self.consciousness.memory.corrections.count()`). This tight coupling means any internal refactoring of `Consciousness` requires changes across both files. A well-defined `stats()` dict method (the `.stats` property already exists) should be the only interface exposed to `hivemind.py` for telemetry.

### Async/Sync Boundary

The codebase mixes sync and async code in ways that create subtle stalls. The embedding engine (`EmbeddingEngine`, `EvalEmbeddingEngine`) is synchronous throughout (using `requests`), but is called from async coroutines. This is acceptable for short calls, but with 30-second timeouts becomes dangerous. The MQTT handler runs in a dedicated `threading.Thread` and bridges to the asyncio loop via `loop.call_soon_threadsafe()` — this is the correct pattern and is handled well in `mqtt_hub.py`.

---

## Security Findings

### S1. API key in HTML page source (Critical)
As described in Critical Issue 2. The key is embedded in the HTML response to `GET /`, visible in browser devtools, browser history, HTTP proxy logs, and any browser extension. The `/api/auth/token` endpoint has no authentication requirement.

### S2. CORS allows `http://localhost:8000` as an origin
**File:** `web/dashboard.py:63–65`

```python
allow_origins=["http://localhost:5173", "http://localhost:3000",
               "http://localhost:8000", ...]
```

The server allows requests from `http://localhost:8000` — itself. This means any page served by this same server could make authenticated cross-origin requests to the API. While the attack surface on localhost is limited, this should be tightened to only the React dev server origins during development, and to `http://localhost:8000` only in production (same-origin, which doesn't need CORS at all).

### S3. `update_task` column name injection (High)
As described in Critical Issue 5.

### S4. Settings file written without race condition protection
**File:** `web/dashboard.py:776–785`

The `/api/profile` endpoint reads `configs/settings.yaml`, modifies it in memory, and writes it back. There is no locking. If two profile change requests arrive concurrently (unlikely but possible), one update will be lost. More importantly, if the process crashes mid-write, the settings file is truncated. Apply the same `tempfile.mkstemp + os.replace` pattern used in `backend/routes/settings.py:90`.

### S5. `json.loads(body)` in `/api/chat` without error handling
**File:** `web/dashboard.py:588`

```python
body = await request.body()
if len(body) > 100_000:
    return JSONResponse({"error": "Message too large"}, status_code=413)
data = json.loads(body)   # raises JSONDecodeError if body is not valid JSON
```

A malformed request body (not valid JSON) will raise `json.JSONDecodeError`, which FastAPI will convert to a 500 Internal Server Error rather than a 400 Bad Request. Wrap in `try/except json.JSONDecodeError`.

### S6. Ollama URL is trusted without validation
**File:** `hivemind.py:487`, `core/memory_engine.py:162–163`

The Ollama base URL is read from settings YAML. If an attacker can write to `configs/settings.yaml` (e.g., via the `/api/profile` endpoint or a future settings endpoint), they could redirect Ollama calls to an arbitrary host, enabling SSRF. Validate that the configured URL has scheme `http` or `https` and resolves to `127.0.0.1` or a trusted range before use.

---

## Testing Gaps

### Critical untested paths

1. **`HiveMind.chat()` end-to-end** — There is no test that instantiates `HiveMind`, calls `start()`, sends a chat message, and verifies the response. `test_b4_error_handling.py` only parses the AST and checks for the existence of try/except via string search. The main code path — the one users hit — is entirely untested at the integration level.

2. **`_heartbeat_loop` behavior** — No tests verify that the heartbeat correctly skips during chat activity, correctly triggers night mode, or correctly limits concurrent tasks. `test_all.py` has `test_heartbeat` but it appears to be a simple check, not a behavioral test.

3. **Authentication enforcement** — No tests verify that `/api/chat` returns 401 without a Bearer token, that `/api/status` returns 401, or that public endpoints (`/health`, `/ready`) return 200 without auth. This is the most critical security path and is untested.

4. **`AuditLog` concurrent write safety** — `test_memory_proxy.py` tests the `AuditLog` interface but does not test concurrent writes from multiple threads, which is the exact scenario that can cause corruption in production.

5. **`update_task` column injection** — No test covers the case where an unexpected column name is passed to `update_task`.

6. **Shutdown ordering** — No tests verify that `stop()` correctly drains background tasks before closing the DB, or that a crash mid-shutdown does not corrupt SQLite.

### Test quality observations

- **`test_b4_error_handling.py`** tests that try/except blocks exist by doing string searches on source code. This tests the implementation, not the behavior. A test that actually triggers an error condition and verifies graceful handling would be more valuable.

- **Multiple test files** use `asyncio.get_event_loop().run_until_complete()` which is deprecated and will fail on Python 3.14. Migrate to `asyncio.run()` or `pytest-asyncio`.

- **`tests/test_cognitive_graph.py`** is well-written — uses `tempfile.mkdtemp()` for isolation, covers add/get/remove for nodes and edges, tests persistence (save/load cycle), and tests BFS depth limits. This is the standard all test files should aim for.

- **Stub source classes** (`WebScrapeSource`, `ClaudeDistillSource`, `ChatHistorySource`, `RssFeedSource`) in `night_enricher.py` return empty lists and `is_available() = False`. They are registered in the `SourceManager` but effectively dead code. If these are planned for future implementation, they should at minimum raise `NotImplementedError` or have a clear `# TODO` marker explaining the timeline.

---

## Positive Observations

**Circuit breaker pattern is well-implemented.** `CircuitBreaker` in `memory_engine.py` (lines 40–118) correctly implements the three-state machine (CLOSED/OPEN/HALF_OPEN), prunes expired failures within the time window, and is wired to both `EmbeddingEngine` and `EvalEmbeddingEngine`. This is production-grade defensive coding.

**Atomic writes for critical data.** `CognitiveGraph.save()` (lines 57–70) correctly uses `tempfile.mkstemp + os.replace + fsync` to ensure the JSON file is never left in a partial state. The `backend/routes/settings.py` does the same for settings writes. This pattern is consistent and correct.

**`PriorityLock` design is sound.** The chat priority lock in `hive_support.py` uses `asyncio.Event` cleanly, has well-defined semantics (`chat_enters` / `chat_exits` / `wait_if_chat`), and the `should_skip` property correctly combines both the active-chat and cooldown states. The cooldown prevents thundering-herd re-entry of background tasks immediately after chat.

**`_enriched_prompt` context manager fixes the original bug correctly.** Using `copy.copy(agent)` to create an isolated copy, injecting knowledge and date prefix, and yielding the copy (rather than mutating the original) is the right approach. The docstring clearly explains the fix. The legacy `_inject_knowledge` / `_restore_prompt` pattern is clearly marked as deprecated.

**`NightEnricher` quality gate architecture is well-designed.** The 4-step pipeline (LLM validate → seasonal guard → contradiction check → novelty check), the `SourceMetrics` rolling window, the `SourceManager` registry, the "last source standing" protection against pausing all sources, and the `AdaptiveTuner` / `ConvergenceDetector` pairing represent a thoughtful autonomous learning architecture.

**Memory eviction is well-guarded.** `MemoryEviction` (lines 675–838) correctly implements TTL-based and count-based eviction, has a protected-sources set that prevents user corrections and conversation memories from being evicted, and processes in batches to avoid large single ChromaDB operations.

**MAGMA Layer tests are thorough.** `test_layer5_trust.py`, `test_cognitive_graph.py`, `test_memory_proxy.py`, and `test_replay_engine.py` all use temporary directories, test both success and failure paths, and test at the behavior level rather than the implementation level. The test coverage for these newer layers is noticeably better than for the original `hivemind.py` code.

**CORS and rate limiting are in place.** The codebase has CORS configured with an explicit origin allowlist (not `*`), has input size limits on the chat endpoint (`100_000` byte limit), and has rate limiting referenced in the MAGMA layer. These are basic security measures that many hobby projects omit.

**Settings validation at startup.** Loading settings via Pydantic (`core/settings_validator.py`) and failing fast with a clear error message before any other initialization is the correct approach. This prevents subtle misconfiguration bugs from manifesting hours into a run.

---

*Review complete. Total findings: 5 Critical, 7 High, 11 Medium, 7 Low.*
