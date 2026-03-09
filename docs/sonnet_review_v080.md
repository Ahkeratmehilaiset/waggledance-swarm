# WaggleDance v0.8.0 Codebase Review
*Reviewer: Claude Sonnet 4.6 | Date: 2026-03-09 | Version: 0.8.0*

---

## Scope Note

This review focuses on issues that are **new or still present** in v0.8.0 and were **not previously fixed**. Items from `docs/sonnet_review.md` that are confirmed fixed are listed briefly in the Confirmed Fixes section at the bottom.

---

## Executive Summary

v0.8.0 has fixed several of the highest-severity issues from the previous review: `asyncio.get_event_loop()` in `start()` now uses `asyncio.get_running_loop()`, the atomic write pattern for `learning_progress.json` is applied, `update_task` column injection is blocked by an allowlist, and `knowledge` / `knowledge_loader` now share one instance. The whisper cycle no longer directly mutates agent prompts.

However, four new or unresolved issues of high severity remain:

- **API key still injected into root HTML** — the core security issue from S1 in the previous review is unchanged. The key appears as a plaintext JavaScript string visible in browser devtools.
- **`_write_lock` is a class variable, not an instance variable** in both `AuditLog` and `TrustEngine`. This means all instances share one lock, which accidentally prevents some deadlocks but silently serializes reads from the MQTT thread with concurrent async writes — and would break entirely if two `AuditLog` instances are ever created in the same process.
- **`/api/voice/audio` decodes arbitrary base64 from the network without size validation**, creating a potential memory exhaustion path.
- **`_pending` concurrency counter is not protected by a lock** in the heartbeat loop. Since `_night_learning_cycle` and `_maybe_weekly_report` are dispatched with `_track_task` and bypass `_guarded`, their completion does not decrement `_pending`, so the gate reads a stale count.

---

## Critical Issues

### C1. API key still injected into root HTML page source (unchanged from v0.7.0)
**File:** `web/dashboard.py:95`
**Severity:** Critical

```python
html = f"""...<script>localStorage.setItem("WAGGLE_API_KEY","{_api_key}")</script>..."""
```

The bearer token is inserted literally into the HTML returned by `GET /`. The `/api/auth/token` endpoint returns the key to any caller from `127.0.0.1` or `::1` (line 892–898), which covers any process on the local machine, not just the legitimate dashboard. While mitigated by the localhost-only deployment assumption, this is still listed as Critical because:

1. Any browser extension that reads page content (ad blocker, password manager, devtools extension) sees the key on page load.
2. The key appears in browser history and HTTP proxy/debug logs.
3. The `/api/auth/token` endpoint is a zero-authentication endpoint that any local process can call.

**Suggested fix:** Remove the `localStorage.setItem` injection from HTML. Instead, serve the key via a short-lived HttpOnly session cookie at the `GET /api/auth/token` endpoint (restricted to localhost), and let the dashboard JS read it via a dedicated `GET /api/auth/init` call that requires the cookie.

---

### C2. `_write_lock` is a class variable shared across all instances
**Files:** `core/audit_log.py:20`, `core/trust_engine.py:111`
**Severity:** Critical

```python
class AuditLog:
    _write_lock = threading.Lock()   # class-level — shared by ALL instances

class TrustEngine:
    _write_lock = threading.Lock()   # class-level — shared by ALL instances
```

Both classes define `_write_lock` as a **class attribute**, not an instance attribute. This means if two `AuditLog` instances exist in the same process (or two `TrustEngine` instances), they share one lock. Currently only one instance of each is created, so this does not cause bugs today — but it is a time-bomb:

- If a future refactor creates a second `AuditLog` (e.g., for testing or for a separate audit trail), writes to both would be serialized through one lock, but reads would still race against writes because `query_by_agent`, `query_by_time_range`, etc., do not hold the lock.
- The concurrent-read hazard is real today: the MQTT handler thread can call `AuditLog.record()` (which acquires `_write_lock`) while an async task calls `AuditLog.count()` (which does NOT acquire the lock) from the event loop. With SQLite WAL mode these race conditions are safe for data integrity but not safe for cursor state on the shared connection object.

**Suggested fix:** Move the lock to `__init__` as an instance attribute: `self._write_lock = threading.Lock()`. Also protect read paths (`query_*`, `count`) with a `self._read_lock = threading.RLock()` or switch to `aiosqlite` for the async call paths.

---

### C3. `/api/voice/audio` decodes unbounded base64 payload without size check
**File:** `web/dashboard.py:1090–1105`
**Severity:** Critical

```python
@app.post("/api/voice/audio")
async def voice_audio(request: Request):
    ...
    body = await request.json()
    audio_b64 = body.get("audio_base64", "")
    if not audio_b64:
        return JSONResponse({"error": "No audio data"}, status_code=400)
    audio_bytes = base64.b64decode(audio_b64)   # no size check
    ...
    result = await hivemind.voice_interface.process_audio(audio_bytes, sample_rate)
```

There is no size limit on the `audio_base64` field. An attacker (or a misconfigured client) can send a very large base64-encoded string, causing `base64.b64decode` to allocate a correspondingly large byte array in memory. Since the endpoint is protected by the Bearer auth middleware, this requires a valid API key — but recall that the key is visible in the HTML source (C1). A 100 MB base64 payload decodes to ~75 MB of bytes, and with concurrent requests this can exhaust system RAM.

Compare with `POST /api/chat`, which correctly checks `len(body) > 100_000` before processing. The voice endpoint has no equivalent guard.

**Suggested fix:**
```python
body = await request.body()
if len(body) > 10_000_000:   # 10 MB raw body limit for audio
    return JSONResponse({"error": "Audio too large"}, status_code=413)
data = json.loads(body)
audio_b64 = data.get("audio_base64", "")
```

---

### C4. `_pending` gate is bypassed by `night_learning_cycle` and `_maybe_weekly_report`
**File:** `hivemind.py:2075–2088`
**Severity:** High (concurrency)

```python
# Night learn bypasses _guarded — it's the whole point
if self._night_mode_active and self._heartbeat_count % 2 == 0:
    _actions.append("night_learn")
    self._track_task(self._night_learning_cycle())   # NOT wrapped in _guarded

if self._heartbeat_count % 50 == 0:
    self._track_task(self._maybe_weekly_report())    # NOT wrapped in _guarded
```

Both of these tasks are created via `_track_task()` directly, bypassing `_guarded()`. The `_guarded()` function is the only place that increments and decrements `_pending`. This means:

1. `_pending` never accounts for running `_night_learning_cycle` or `_maybe_weekly_report` tasks.
2. The `if _pending >= _MAX_PENDING: skip` gate at lines 1916–1917 and 2001–2002 can allow more concurrent LLM calls than intended, since up to N night-learning tasks run invisibly outside the gate.
3. During intensive night mode (every 2 heartbeats), up to `(_heartbeat_count / 2)` night-learning coroutines can accumulate if they take longer than the interval.

The comment says "bypasses _guarded — it's the whole point of night mode". The intent is correct — night_learn should not be starved by the gate. But it should still track itself in `_pending` to prevent runaway accumulation.

**Suggested fix:** Inside `_night_learning_cycle`, guard against re-entrancy with a dedicated `_night_learn_running: bool` flag that is checked before starting and cleared on completion. Alternatively, limit the burst: only dispatch if `_pending < _MAX_PENDING + 1` (one extra slot for the night learner).

---

## High Priority Issues

### H1. `data/finetune_live.jsonl` opened and closed on every heartbeat agent cycle — unbounded growth
**File:** `hivemind.py:2298–2301`
**Severity:** High

```python
finetune_path = Path("data/finetune_live.jsonl")
finetune_path.parent.mkdir(exist_ok=True)
with open(finetune_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

This is called from `_log_finetune`, which is called from both `_agent_proactive_think` and `_idle_research`. In a full-agent heartbeat loop, this appends to `finetune_live.jsonl` many times per minute. There is no rotation, no size cap, and no eviction. A system running for 24 hours of night mode (every 10 seconds = 8640 cycles) will produce a JSONL file that can easily reach several hundred MB. There is no counterpart to the `StructuredLogger._maybe_rotate()` logic here.

Additionally, the file is opened and closed on every call. Under high load this is O(n) file handle operations per hour.

**Suggested fix:** Apply the same file rotation logic as `StructuredLogger`: check the file size every N calls and rotate when it exceeds a threshold (e.g., 50 MB). Or add a size check in `_log_finetune`.

---

### H2. `recall()` in `SharedMemory` passes unsanitized `query` directly to LIKE pattern
**File:** `memory/shared_memory.py:119, 125`
**Severity:** High

```python
(agent_id, f"%{query[:50]}%", limit)
```

The `query` string is truncated to 50 characters and then embedded into a SQL `LIKE` pattern as `%{query}%`. If `query` contains `%` or `_` characters (which are LIKE wildcards in SQLite), they will be treated as wildcards rather than literal characters. For example, a query of `"bee%hive"` would match any memory content that contains "bee" followed by any characters followed by "hive". This is not injection (the value is passed as a parameter), but it is a semantics bug: the search will return unexpected results for queries that naturally contain percent signs, underscores, or backslashes.

The fix used in `AuditLog.query_spawn_tree()` (line 101) is the correct approach: escape the wildcards before embedding.

**Suggested fix:**
```python
safe_query = query[:50].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
# Then use: f"%{safe_query}%"  with ESCAPE '\\'
```

---

### H3. `POST /api/profile` writes settings.yaml non-atomically
**File:** `web/dashboard.py:776–785`
**Severity:** High

```python
with open(settings_path, encoding="utf-8") as f:
    content = f.read()
lines = content.split("\n")
for i, line in enumerate(lines):
    if line.startswith("profile:"):
        lines[i] = f"profile: {new_profile}  # gadget | cottage | home | factory"
        break
with open(settings_path, "w", encoding="utf-8") as f:  # non-atomic!
    f.write("\n".join(lines))
```

This is the same issue documented as S4 in the previous review. The `backend/routes/settings.py` correctly uses `tempfile.mkstemp + os.replace + fsync` for atomic writes (lines 88–94), but the production `web/dashboard.py` profile endpoint was **not updated** and still writes directly. A process crash or power loss mid-write will leave `configs/settings.yaml` truncated and unreadable, requiring manual repair.

**Suggested fix:** Apply the same pattern used in `backend/routes/settings.py:88–94`.

---

### H4. `json.loads(body)` in `/api/chat` still raises 500 on malformed JSON
**File:** `web/dashboard.py:588`
**Severity:** High

```python
body = await request.body()
if len(body) > 100_000:
    return JSONResponse({"error": "Message too large"}, status_code=413)
data = json.loads(body)   # JSONDecodeError -> 500 Internal Server Error
```

This was documented as S5 in the previous review and remains unaddressed. A `curl` call with a malformed JSON body (e.g., `curl -d "not-json"`) returns HTTP 500 instead of 400. FastAPI's default exception handler will convert `json.JSONDecodeError` to a 500 response rather than the expected 400.

**Suggested fix:**
```python
try:
    data = json.loads(body)
except json.JSONDecodeError:
    return JSONResponse({"error": "Invalid JSON"}, status_code=400)
```

---

### H5. MAGMA route registration failures silently swallowed (unchanged from v0.7.0)
**File:** `web/dashboard.py:1327–1353`
**Severity:** High

```python
try:
    from backend.routes.magma import register_magma_routes
    register_magma_routes(app, hivemind)
except Exception:
    pass
```

All four MAGMA route registrations use `except Exception: pass`. If a route module has a syntax error or an import fails (e.g., missing `networkx` for the graph routes), the routes are silently omitted with no log message. API consumers and monitoring will receive 404 errors instead of the expected 200, with no server-side indication of why.

This was documented as issue 23 in the previous review and remains unaddressed in v0.8.0.

**Suggested fix:** Change each `except Exception: pass` to `except Exception as e: log.warning(f"Route not loaded: {e}")`.

---

### H6. `int(request.query_params.get(...))` raises 500 on non-integer input
**File:** `web/dashboard.py:619–620`
**Severity:** High

```python
limit = int(request.query_params.get("limit", "20"))
offset = int(request.query_params.get("offset", "0"))
```

If a client sends `GET /api/history?limit=abc`, `int("abc")` raises `ValueError`, which FastAPI converts to a 500 Internal Server Error. This should return 400.

**Suggested fix:**
```python
try:
    limit = int(request.query_params.get("limit", "20"))
    offset = int(request.query_params.get("offset", "0"))
except ValueError:
    return JSONResponse({"error": "limit and offset must be integers"}, status_code=400)
```

---

### H7. `_capture_heartbeat` callback never unregistered — callback list grows on reconnect
**File:** `web/dashboard.py:1225`
**Severity:** High (resource leak)

```python
hivemind.register_ws_callback(_capture_heartbeat)
```

`_capture_heartbeat` is registered once at `create_app()` time and is never unregistered. Since `create_app()` is designed to be called once, this is technically harmless in production. However, `register_ws_callback` now has a built-in overflow guard at line 3405:

```python
def register_ws_callback(self, callback):
    if len(self._ws_callbacks) >= 50:
        self._ws_callbacks = self._ws_callbacks[-25:]
```

This overflow guard silently removes the oldest callbacks when the list reaches 50. If many WebSocket clients connect and disconnect (each connection registers `ws_callback` and `monitor_ws_callback`, which amounts to 2 registrations per connect), the list can grow to 50 and then the overflow guard will evict `_capture_heartbeat` — the polling buffer will silently stop updating, and the React dashboard's `/api/heartbeat` feed will go stale. The overflow guard is treating a symptom of the bug in issue 9 (from the previous review: `monitor_ws_callback` and `ops_agent` callback not unregistered on disconnect).

**Suggested fix:** Fix the root cause in the WebSocket disconnect handler: unregister all three callbacks (ws_callback, monitor_ws_callback, ops_agent callback). The current code at line 1321–1325 correctly unregisters `monitor_ws_callback` and `ops_agent` callback, so this appears partially fixed — but the overflow guard at line 3405 is still needed only if unregistration is incomplete.

**Verification needed:** Confirm that `hivemind.ops_agent.unregister_decision_callback(ws_callback)` at line 1325 correctly removes the callback. If `OpsAgent` stores callbacks by identity and `ws_callback` is a closure (re-created on each WebSocket connect), the unregister may not find the original registration.

---

## Medium Priority Issues

### M1. `AuditLog` and `TrustEngine` reads have no lock — concurrent write+read is unsafe
**File:** `core/audit_log.py:79–130`, `core/trust_engine.py:153–238`
**Severity:** Medium

The `_write_lock` correctly protects `record()` and `_create_table()`. However, all read operations (`query_by_agent`, `query_by_time_range`, `compute_reputation`, `get_all_reputations`) use `self._conn` without any lock. In SQLite WAL mode, concurrent reads from the same connection object are not safe if a write is simultaneously modifying the connection's cursor state. The MQTT thread calls `record()` (which acquires the lock and commits), while the async event loop simultaneously calls `count()` or `get_all_reputations()` (no lock). These share the same `sqlite3.Connection` object with `check_same_thread=False`, which suppresses the thread guard but does not make the connection thread-safe.

**Suggested fix:** Protect all reads with `self._read_lock` (a `threading.RLock` allows re-entrant reads), or use one connection per thread via `threading.local()`.

---

### M2. `_notify_ws` iterates `_ws_callbacks` without snapshot copy — mutation mid-iteration
**File:** `hivemind.py:3412–3417`
**Severity:** Medium

```python
async def _notify_ws(self, event_type: str, data: dict):
    for callback in self._ws_callbacks:   # iterating the live list
        try:
            await callback({"type": event_type, "data": data})
        except Exception:
            pass
```

This issue was documented as issue 22 in the previous review and remains. During iteration, a WebSocket connect/disconnect event can call `register_ws_callback` or `unregister_ws_callback`, which creates a new list object (`[cb for cb in ...]` in `unregister`) or appends to the existing one. If the list object is replaced mid-iteration (via `unregister`), Python's `for` loop will continue over the old list object — which is actually safe in this specific case. However, `register_ws_callback` with the overflow guard at line 3405 replaces the list with `self._ws_callbacks[-25:]`, which means the loop continues over the old list, missing newly registered callbacks and potentially calling already-discarded callbacks.

**Suggested fix:** Take a snapshot at the start of `_notify_ws`:
```python
async def _notify_ws(self, event_type: str, data: dict):
    for callback in list(self._ws_callbacks):
        ...
```

---

### M3. `TrustEngine.get_all_reputations()` issues N+1 queries — called on every `/api/status` poll
**File:** `core/trust_engine.py:194–213`
**Severity:** Medium (unchanged from previous review)

```python
def get_all_reputations(self) -> Dict[str, float]:
    agents = self._conn.execute("SELECT DISTINCT agent_id FROM trust_signals").fetchall()
    for (aid,) in agents:
        rep = self.compute_reputation(aid)   # each calls another SELECT
```

`compute_reputation` issues a `SELECT ... LIMIT 1200` per agent. With 75 agents, this is 75 DB round-trips on each call to `get_all_reputations()`. This is called via `get_ranking()` from `get_status()` (line 1801), which is polled every ~12 seconds by the dashboard. The previous review flagged this as issue 15; it remains unresolved in v0.8.0.

**Suggested fix:** Add a 60-second TTL cache for `get_ranking()` results or compute all reputations in a single query using a CTE.

---

### M4. `data/finetune_live.jsonl` opened without any concurrent-write protection
**File:** `hivemind.py:2300–2301`
**Severity:** Medium

```python
with open(finetune_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
```

`_log_finetune` is called from `_agent_proactive_think` and `_idle_research`, both of which can run concurrently (they are spawned as separate tasks via `_track_task`). On Windows (the target platform), concurrent writes to an append-mode file from different tasks can interleave, producing truncated or merged JSON lines. Python's GIL prevents true bytewise interleaving for CPython, but two `f.write()` calls from separate asyncio tasks can still interleave if either task calls `await` between the `open()` and `write()`. In the current code there is no `await` between them, so the GIL protects the operation — but this is a subtle invariant that could break if the code is ever refactored to be async.

**Suggested fix:** Use the `StructuredLogger` pattern (which opens/closes per call and is therefore safe) and add the file rotation logic there, or use `asyncio.Lock` around the open/write sequence.

---

### M5. `replay_engine.get_manifest()` with `session_id` still loads all entries
**File:** `core/replay_engine.py:29–33`
**Severity:** Medium (unchanged from previous review)

```python
if session_id:
    all_entries = self.audit.query_by_time_range(0, float("inf"), agent_id=agent_id)
    entries = [e for e in all_entries if e["session_id"] == session_id]
```

This loads all audit entries (potentially thousands) into memory and then filters in Python by `session_id`. The `audit` table has an index on `session_id`. This was flagged as issue 16 in the previous review and is unresolved in v0.8.0.

**Suggested fix:** Add a `query_by_session` method to `AuditLog` that queries directly: `SELECT * FROM audit WHERE session_id=? ORDER BY timestamp`.

---

### M6. `CognitiveGraph.find_dependents` and `find_ancestors` use O(n) `list.pop(0)`
**File:** `core/cognitive_graph.py:152–166, 176`
**Severity:** Medium (unchanged from previous review)

This was flagged as issue 17 in the previous review and is unresolved. Both BFS methods use `list.pop(0)` which is O(n) per pop, making the BFS O(n²) on large graphs.

**Suggested fix:** Use `collections.deque` with `popleft()`.

---

### M7. `subprocess.run(["nvidia-smi", ...])` blocking call inside async handler
**File:** `web/dashboard.py:1264–1270`
**Severity:** Medium

```python
import subprocess
nv = subprocess.run(
    ["nvidia-smi", "--query-gpu=...", "--format=csv,noheader,nounits"],
    capture_output=True, text=True, timeout=2
)
```

This is inside `async def hardware_stats()`, which is an asyncio handler. `subprocess.run()` is a blocking call that will block the entire event loop for up to 2 seconds (the timeout) on every poll of `GET /api/hardware`. The dashboard polls hardware stats regularly (every 3 seconds based on `setInterval(loadSys, 3000)` visible in the HTML). This means the event loop can be stalled for up to 2 seconds every 3 seconds, preventing all other API calls from being handled.

Note: the `/api/system` endpoint correctly uses `await asyncio.create_subprocess_exec(...)` for its nvidia-smi call. The `/api/hardware` endpoint and `/api/models` endpoint do not.

**Suggested fix:** Replace the blocking `subprocess.run()` with `await asyncio.create_subprocess_exec(...)` and `await proc.communicate()`, the same pattern used in `/api/system` (lines 921–932).

---

### M8. `PriorityLock.chat_enters()` sets cooldown before the lock is actually cleared
**File:** `core/hive_support.py:51–53`
**Severity:** Medium (subtle concurrency bug)

```python
async def chat_enters(self, cooldown_s: float = 3.0):
    self._event.clear()  # Block workers
    self._cooldown_until = time.monotonic() + cooldown_s
```

The cooldown timer is set at `chat_enters()` time — when the chat starts — not at `chat_exits()` time. This means the 3-second cooldown begins as soon as the chat starts, not when it ends. If a chat response takes 5 seconds (a slow LLM call), the cooldown will have already expired before the chat finishes. Workers that call `wait_if_chat()` will be unblocked by `chat_exits()` at the correct time, but `should_skip` (checked in the heartbeat) will have returned `False` during the last 2+ seconds of the chat while it was still in progress. The heartbeat can then re-enter and spawn new background tasks that compete with the active chat for the LLM semaphore.

**Suggested fix:** Set the cooldown in `chat_exits()`, not `chat_enters()`:
```python
async def chat_enters(self, cooldown_s: float = 3.0):
    self._cooldown_until = 0.0   # no cooldown yet
    self._event.clear()

async def chat_exits(self, cooldown_s: float = 3.0):
    self._cooldown_until = time.monotonic() + cooldown_s
    self._event.set()
```
This requires passing `cooldown_s` to `chat_exits()` instead of `chat_enters()`.

---

### M9. `_delegate_to_agent` mutates `agent.system_prompt` directly after the `_enriched_prompt` block
**File:** `hivemind.py:1371–1378, 1442–1443`
**Severity:** Medium

```python
# Before the enriched_prompt block:
if use_en_prompts:
    _orig_agent_sys = agent.system_prompt
    agent.system_prompt = f"Date: {_dt.now():%Y-%m-%d %H:%M}. " + AGENT_EN_PROMPTS[_atype]

with self._enriched_prompt(agent, ...) as _enriched_agent:
    response = await _enriched_agent.think(...)

# After the enriched_prompt block:
if _orig_agent_sys is not None:
    agent.system_prompt = _orig_agent_sys   # direct mutation to restore
```

The `_enriched_prompt` context manager was introduced specifically to avoid direct mutation of `agent.system_prompt`. However, `_delegate_to_agent` still directly mutates the original agent's `system_prompt` (not the enriched copy's) at lines 1377 and 1443. The enriched copy correctly insulates the LLM call, but the surrounding mutation (inject English prompt → call → restore Finnish prompt) happens on the live agent object. If two concurrent `_delegate_to_agent` calls target the same agent type (e.g., two simultaneous user requests both route to `beekeeper`), they can race on `agent.system_prompt`: one call overwrites the prompt that the other just set.

**Suggested fix:** Move the English prompt injection into the `_enriched_prompt` block or create the agent copy before setting the English prompt, so the original `agent.system_prompt` is never touched.

---

### M10. `_get_knowledge()` in `base_agent.py` creates a new `KnowledgeLoader` per agent instance
**File:** `agents/base_agent.py:73–80`
**Severity:** Medium

```python
if self._knowledge_loader is None:
    from core.knowledge_loader import KnowledgeLoader
    self._knowledge_loader = KnowledgeLoader("knowledge")
```

Each agent instance creates its own `KnowledgeLoader` on first use. With 200+ active agents, this means 200+ `KnowledgeLoader` instances, each loading the same YAML files from disk. At 5-minute cache expiry (`(now - self._knowledge_loaded_at).seconds > 300`), these are all refreshed from disk simultaneously.

Note: The previous review flagged a related issue (two duplicate `KnowledgeLoader` instances in `hivemind.py`) and it was fixed — `self.knowledge_loader = self.knowledge`. But the per-agent loader creation was not addressed. `hivemind.py` correctly injects knowledge via `_enriched_prompt` using `self.knowledge_loader.get_knowledge_summary()`, which is the shared instance. However, `base_agent.Agent.think()` also calls `self._get_knowledge()` independently (line 112 in `base_agent.py`), which triggers the per-agent loader creation.

**Suggested fix:** Inject the shared `KnowledgeLoader` into the agent at construction time (add `knowledge_loader` parameter to `Agent.__init__`) and remove the lazy-creation in `_get_knowledge`.

---

## Low Priority Issues

### L1. `spawner.py` imports `YAMLBridge` twice
**File:** `agents/spawner.py:15–16`
**Severity:** Low

```python
from core.yaml_bridge import YAMLBridge
from core.yaml_bridge import YAMLBridge
```

Duplicate import on consecutive lines. Python caches imports so this is not a runtime error, but it indicates a copy-paste error. Should be a single import.

---

### L2. `_CORRECTION_WORDS` and `_CORRECTION_PHRASES` rebuilt on every `_do_chat` call
**File:** `hivemind.py:879–881`
**Severity:** Low (unchanged from previous review)

```python
_CORRECTION_WORDS = {"ei", "väärin", "wrong", ...}
_CORRECTION_PHRASES = {"ei vaan", "oikea vastaus", ...}
```

These are constant sets rebuilt as local variables on every chat invocation. They should be module-level constants. This was documented as issue 24 in the previous review and is unchanged.

---

### L3. Version string in `hivemind.py` module docstring is `v0.0.2`, project is at `v0.8.0`
**File:** `hivemind.py:3–5`
**Severity:** Low (unchanged from previous review)

The file header still says `v0.0.2` and `Built: 2026-02-22`. This was issue 25 in the previous review and is unchanged.

---

### L4. `StructuredLogger` opens and closes the metrics file on every log call
**File:** `core/hive_support.py:118–122, 138–140`
**Severity:** Low (unchanged from previous review, issue 28)

Each `log_chat()` and `log_learning()` call opens the `.jsonl` file, writes, and closes it. Under high chat load this is O(n) file open/close syscalls. This was documented as issue 28 in the previous review and is unchanged.

---

### L5. `_fix_mojibake` called on every `get_status()` — polled every 12 seconds
**File:** `hivemind.py:1651–1655`
**Severity:** Low (unchanged from previous review, issue 26)

```python
for ag in agents:
    if "name" in ag and isinstance(ag["name"], str):
        ag["name"] = self._fix_mojibake(ag["name"])
```

Called on every `GET /api/status` poll (every 12 seconds). The fix should apply once at agent spawn time, not on every status read. This was documented as issue 26 and is unchanged.

---

### L6. `confusion_memory.json` uses non-atomic rename on Windows (TOCTOU)
**File:** `backend/routes/chat.py:700–703`
**Severity:** Low

```python
if _CONFUSION_MEMORY_PATH.exists():
    _CONFUSION_MEMORY_PATH.unlink()
tmp_path.rename(_CONFUSION_MEMORY_PATH)
```

The comment acknowledges this: "On Windows, need to remove target first if it exists". However, the pattern `unlink() + rename()` is not atomic. If the process crashes between `unlink()` and `rename()`, the `confusion_memory.json` file is permanently deleted. `os.replace()` on Python 3.3+ is atomic on Windows (it calls `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING`) and should be used instead:

```python
import os
os.replace(tmp_path, _CONFUSION_MEMORY_PATH)
```

This eliminates the TOCTOU window and avoids the separate `unlink()` call.

---

### L7. `GET /api/models` hardcodes Ollama URL `http://localhost:11434`
**File:** `web/dashboard.py:814–828`
**Severity:** Low

```python
req = urllib.request.Request("http://localhost:11434/api/ps")
req2 = urllib.request.Request("http://localhost:11434/api/tags")
```

The Ollama URL is read from config in `hivemind.py` at startup (`self.config.get('ollama', {}).get('base_url', 'http://localhost:11434')`), but `GET /api/models` and `GET /api/models` (in `/api/hardware`) always hardcode `localhost:11434`. This was flagged as issue 29 in the previous review for the readiness check (which was fixed with `asyncio.to_thread`), but the dashboard model status endpoint was not updated. If a user configures a non-standard Ollama port, `/api/models` will always show no models loaded.

**Suggested fix:** Pass the configured Ollama URL to `create_app()` and use it in the model status endpoint.

---

## Security Observations

### S1. WebSocket endpoint allows any `token` query parameter without rate limiting
**File:** `backend/auth.py:86–93`
**Severity:** Medium

```python
if path == "/ws":
    token = request.query_params.get("token", "")
    if token != self.api_key:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
```

The WebSocket token check does not rate-limit failed attempts. An attacker on the local network (if the server is reachable) can brute-force the token. Since the token is `secrets.token_urlsafe(32)` (256 bits of entropy), brute force is computationally infeasible in practice — but there is no failed-attempt counter or IP-based lockout. If the key space were ever reduced (e.g., config change), this would become exploitable.

---

### S2. `GET /api/auth/token` does not validate `Host` header — DNS rebinding risk
**File:** `web/dashboard.py:892–898`
**Severity:** Medium

```python
@app.get("/api/auth/token")
async def auth_token(request: Request):
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1"):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    return {"token": _api_key}
```

The check validates only the client IP, not the `Host` header. A DNS rebinding attack can make a malicious website (hosted at an attacker-controlled domain) instruct the browser to send requests to `127.0.0.1:8000`. The browser enforces the Same-Origin Policy for JavaScript, but a DNS rebinding attack can bypass this by rebinding the attacker's domain to `127.0.0.1`. The server sees `client.host = "127.0.0.1"` and returns the API key.

This is a known pattern for attacking localhost services. Mitigate by also checking that the `Host` header is `localhost` or `127.0.0.1`:

```python
host_header = request.headers.get("host", "")
if client_host not in ("127.0.0.1", "::1") or not host_header.startswith(("localhost", "127.0.0.1")):
    return JSONResponse({"error": "Forbidden"}, status_code=403)
```

---

### S3. `GET /api/status` is in `PUBLIC_PATHS` — exposes system internals without auth
**File:** `backend/auth.py:22–28`
**Severity:** Low

```python
PUBLIC_PATHS = frozenset({
    "/health",
    "/ready",
    "/api/health",
    "/api/status",   # No auth required
    "/api/auth/token",
})
```

`/api/status` returns the full agent list, memory statistics, consciousness stats, MAGMA audit entry counts, embedding status, and night mode details without authentication. While this is intentional for the dashboard status display, it means any process on the network (if the server is not firewall-restricted) can enumerate the full system state without a token. The previous review did not flag this specifically. For localhost deployments this is low risk, but if the server is ever exposed on a LAN or VPN, this becomes an information disclosure issue.

---

## Architecture Observations

### A1. `_night_learning_cycle` and `_maybe_weekly_report` not tracked in `_pending`

Already documented as C4 above. Architecturally, the two-tier task management (some tasks in `_guarded`/`_pending`, others in `_track_task` only) creates an inconsistency. All background LLM-calling tasks should go through a unified concurrency gate.

### A2. `hivemind.py` is 3418 lines — no progress on decomposition

The previous review recommended extracting `core/chat_handler.py`, `core/night_mode.py`, `core/round_table.py`, and `core/heartbeat.py`. No extraction occurred between v0.7.0 and v0.8.0. The file remains the single largest maintainability risk. The comments in `hivemind.py:3316` (`_whisper_cycle`) and the new `_round_table_v2` method added in v0.8.0 (lines 2621–2804) have made the file larger, not smaller.

### A3. `ChatHistory` opens a new SQLite connection per call

`core/chat_history.py:20–24` calls `self._get_conn()` at the start of every method, which creates a new `sqlite3.Connection` object each time. The connection is closed in the `finally` block. With the `threading.Lock()` guard, this is thread-safe and correct. However, creating a new connection on every call has overhead (connection setup, `PRAGMA journal_mode=WAL` execution, row factory setup). A persistent connection pool or a single persistent connection would be more efficient.

This contrasts with `AuditLog` and `TrustEngine` which use a single persistent connection. Neither approach is ideal — `ChatHistory`'s pattern is safe but slow; the MAGMA classes' pattern is fast but fragile under concurrency.

---

## Testing Gaps (New in v0.8.0)

1. **`ChatHistory` concurrent write safety** — `ChatHistory` uses a `threading.Lock()` and opens a new connection per call. There are no tests that verify concurrent calls from multiple threads do not produce duplicate conversation IDs or lost messages.

2. **`/api/voice/audio` size limit** — No test verifies that a large payload returns 413 (it does not currently — it will exhaust memory).

3. **`PriorityLock` cooldown timing** — No test verifies that `should_skip` returns `False` after the cooldown expires, or that the cooldown starts at the correct time (currently it starts at `chat_enters()`, not `chat_exits()`).

4. **`_delegate_to_agent` concurrent prompt mutation** — No test verifies that two concurrent delegations to the same agent type do not produce a mixed system prompt.

5. **`_notify_ws` mid-iteration mutation** — No test verifies the race between callback registration and `_notify_ws` iteration.

---

## Confirmed Fixes from v0.7.0 Review

The following issues from `docs/sonnet_review.md` are confirmed fixed in v0.8.0:

- **Issue 1 (Blocking HTTP in readiness check):** Fixed. `_readiness_check` now wraps `requests.get()` with `await asyncio.to_thread()` (lines 254, 271).
- **Issue 4 (`asyncio.get_event_loop()` in `start()`):** Fixed. Uses `asyncio.get_running_loop()` at line 649.
- **Issue 5 (SQL injection in `update_task`):** Fixed. `ALLOWED_TASK_COLUMNS` allowlist enforced at `memory/shared_memory.py:13–16`.
- **Issue 7 (Two `KnowledgeLoader` instances):** Fixed. Line 372: `self.knowledge_loader = self.knowledge` — single instance.
- **Issue 8 (`_whisper_cycle` legacy mutation):** Fixed. Now creates enriched copies instead of mutating originals (lines 3324–3334).
- **Issue 9 (WebSocket disconnect leaves callbacks):** Partially fixed. Lines 1322–1325 now unregister `monitor_ws_callback` and the ops_agent callback. The overflow guard at line 3405 is a residual defense.
- **Issue 10 (`learning_progress.json` atomic write):** Fixed. Atomic `tmp + os.replace` pattern at lines 3199–3202.
- **Issue 12 (`asyncio.get_event_loop()` in `SensorHub` init):** Fixed. Uses `asyncio.get_running_loop()` at line 649.
- **Issue 19 (duplicate `import requests`):** Both imports still present at lines 253 and 270, but this is cosmetic only and harmless.
- **Issue 22 (`_ws_callbacks` mutation during `_notify_ws`):** The overflow guard at line 3405 partially mitigates this but does not fix the root race.

---

*Review complete. Total new/remaining findings: 4 Critical, 7 High, 10 Medium, 7 Low.*
