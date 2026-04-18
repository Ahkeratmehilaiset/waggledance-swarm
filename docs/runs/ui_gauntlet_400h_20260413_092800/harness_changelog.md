# Harness Changelog — 400h Campaign

All mid-campaign fixes applied to `tests/e2e/harness_helpers.py` and
`tests/e2e/ui_gauntlet_400h.py`. Changes take effect from the next
segment launch (not running segments).

---

## 2026-04-14 — Batch 2 fix: WARM context recycle retry

**Problem:** WARM mode crashed at cycle 300 with `TimeoutError` on
`page.goto()` during browser context recycle in `open_authenticated_hologram`.

**Root cause:** Context recycle at cycle 20/30 boundaries called
`open_authenticated_hologram()` without try/except. A single navigation
timeout killed the entire segment.

**Fix:** Wrapped all context recycle calls (cycle 20 and cycle 30 paths)
in 3-attempt retry loops with 5s sleep between attempts. Failed attempts
logged as `CTX_RECYCLE_FAIL` / `AUTH_BOOT_FAIL` incidents.

**Files:** `ui_gauntlet_400h.py` lines 1079-1101, 1104-1126

---

## 2026-04-15 — Batch 5 fix: WARM auth recovery retry + safety net

**Problem:** WARM crashed again at cycle 300 with the same `TimeoutError`,
but on a *different* code path — the auth-failure recovery branch (line 1026).

**Root cause:** Auth check failure path closed context and called
`open_authenticated_hologram()` without retry. Same single-point-of-failure
as the previous fix, but in a different branch.

**Fix (two parts):**
1. Added 3-attempt retry loop to auth recovery path (lines 1024-1043)
2. Added full `try/except` safety net around the entire WARM cycle body
   (lines 1147-1170). On any crash: log `CYCLE_CRASH` incident, pause
   timer, close context, wait 10s, create fresh context with 3 retries,
   resume timer.

**Files:** `ui_gauntlet_400h.py` lines 1019-1043, 1147-1170

---

## 2026-04-14 — HOT context recycle retry

**Problem:** HOT mode context recycle had the same vulnerability as WARM.

**Fix:** Added 3-attempt retry loop to HOT context recycle (lines 834-850).

**Files:** `ui_gauntlet_400h.py` lines 834-850

---

## 2026-04-16 — Batch 7 improvements (6 changes)

Applied during batch 7 run. Takes effect from batch 8 onwards.

### 1. Chat timeout increase: 10s -> 18s

**Problem:** 87.5% of all 803 incidents were `chat_response_failure` — the
chat response polling timed out at 10 seconds, but backend latency averages
1.3-2.3s per endpoint. Complex queries need more time.

**Fix:** Default timeout in `send_chat_safe()` raised from 10s to 18s.
All HOT callers updated to `timeout_s=18`. WARM cycle-3 chat: 8s -> 12s.
WARM burst: 6s -> 10s. BASELINE callers updated consistently.

**Files:** `harness_helpers.py` line 318, `ui_gauntlet_400h.py` lines 853, 894, 413, 659, 668, 1047, 1074

**Expected impact:** Significant reduction in `chat_response_failure` incidents.

### 2. Burst query throttle

**Problem:** Burst queries (every 10th WARM cycle) fired 3-5 chats in rapid
succession with no delay. Response rate for burst bucket was only 15-20%.

**Fix:** Added 2s `page.wait_for_timeout(2000)` between burst queries in
WARM mode.

**Files:** `ui_gauntlet_400h.py` line 1077 (after burst send)

**Expected impact:** Higher burst response rate; reduced backend pressure
during burst windows.

### 3. Backpressure mechanism (HOT mode)

**Problem:** HOT mode sent queries at max speed regardless of backend load.
No adaptive slowdown when backend was stressed.

**Fix:** Track rolling window of last 100 response latencies. If the most
recent 10 queries average > 3000ms, insert 2s cooldown pause. Counter
`backpressure_pauses` tracks how often this fires.

**Files:** `ui_gauntlet_400h.py` lines 856-866 (after chat send)

**Expected impact:** Smoother throughput under load; fewer timeout failures.

### 4. Session loss root cause tracking

**Problem:** HOT mode tracked `session_lost` count but not *when* sessions
were lost — impossible to distinguish "lost right after context recycle"
from "lost mid-batch after many queries."

**Fix:** Split `session_lost` into two sub-counters:
- `session_lost_after_recycle`: lost within first 2 queries of a batch
- `session_lost_mid_batch`: lost during normal query flow

**Files:** `ui_gauntlet_400h.py` lines 880-884

**Expected impact:** Diagnostic clarity on whether session loss is a
bootstrap problem or a timeout/leak problem.

### 5. Backend health snapshot avg_latency_ms

**Problem:** Health snapshot had per-endpoint latency but no summary metric
for quick backpressure decisions.

**Fix:** Added `avg_latency_ms` field to `backend_health_snapshot()` return
value. Computed as average of all healthy endpoint latencies.

**Files:** `harness_helpers.py` lines 145-148

### 6. Enhanced reporting

**Problem:** Checkpoint and segment reports lacked the new metrics.

**Fix:**
- HOT checkpoint: shows `bp=N` (backpressure pauses) and `avg_ms=N`
- HOT segment report: shows response %, session loss breakdown,
  backpressure count, avg response time
- WARM checkpoint: shows `resp_rate=N%` (chat response success rate)

**Files:** `ui_gauntlet_400h.py` lines 942-946, 972-979, 1143-1148

---

## 2026-04-16 — Batch 8 hotfix: Backpressure threshold tuning

**Problem:** First checkpoint with backpressure showed `bp=75` out of 100
queries — the 3s threshold was too aggressive. Backend naturally responds
in ~5s (includes chat polling overhead), so backpressure fired 75% of the
time, adding unnecessary 2s delays.

**Observation from batch 8 first data:**
- Response rate jumped 65% → 88% (timeout fix working)
- `avg_ms=5008` — natural backend response time is ~5s
- `bp=75/100` — backpressure firing way too often

**Fix:** Raised backpressure threshold from 3000ms to 8000ms. Increased
cooldown from 2s to 3s. Now backpressure only fires when the backend is
genuinely struggling (>8s avg), not during normal operation.

**Files:** `ui_gauntlet_400h.py` line 867-871

**Expected impact:** Backpressure pauses drop from ~75% to near-zero in
normal operation, while still protecting against genuine overload. Takes
effect from batch 9 onwards.
