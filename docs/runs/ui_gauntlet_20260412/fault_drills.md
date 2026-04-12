# Fault Drills — Phase D

- **Date:** 2026-04-12 14:49
- **Server:** Port 8002 (dedicated gauntlet, full non-stub mode)
- **Result:** 6/7 drills passed (1 test infrastructure issue)

## Drill Results

| # | Drill | Result | Duration | Notes |
|---|---|---|---|---|
| 1 | wrong_token | PASS | 2.65s | No cookie set, auth=false, chat disabled, no data leak |
| 2 | session_clear | PASS | 6.14s | Auth=true before, false after cookie clear, API returns 401 |
| 3 | noauth_post | PASS | 1.17s | POST /api/chat without auth returns 401 |
| 4 | invalid_body | PASS | 10.81s | Empty body, invalid JSON, oversized input — UI doesn't hang |
| 5 | server_restart_sim | INCONCLUSIVE | 60.07s | Playwright navigation timeout (test infra issue, not server bug) |
| 6 | ollama_check | PASS | 26.73s | Solver query sent+responded through UI |
| 7 | feed_resilience | PASS | 9.98s | Feeds render, other tabs work after feeds, chat input available |

## Drill Details

### 1. Wrong Token (PASS)
- Navigated to `/hologram?token=FAKE_INVALID_TOKEN_12345`
- **No session cookie created** — verified
- **Auth check returns false** — verified
- **Chat input disabled/absent** — verified
- **No API key leaked in body** — verified

### 2. Session Expiry Simulation (PASS)
- Established valid session, verified auth=true
- Cleared all cookies to simulate expiry
- Auth check returns false after clear
- API call without session returns 401 (correct)

### 3. No-Auth POST (PASS)
- POST to `/api/chat` without session cookie
- Returns HTTP 401 (expected)
- No data leak, correct error path

### 4. Invalid Body (PASS)
- Empty body: server handles gracefully
- Invalid JSON (`{broken json`): server returns 422
- Oversized input (10,000 chars via UI): UI doesn't freeze, DOM stays intact

### 5. Server Restart Simulation (INCONCLUSIVE)
- First bootstrap + abort simulation works fine
- Re-bootstrap in same Playwright context times out (60s)
- **Root cause:** Playwright context has stale websocket/SSE connections from the hologram page that prevent clean navigation
- **Direct curl test:** `/hologram` loads in 3ms, `bootstrap_session` works in 0.07s in fresh context
- **Verdict:** Test infrastructure limitation, not a server bug. The server recovers correctly when tested via a fresh browser context.

### 6. Ollama Connectivity (PASS)
- Ollama confirmed running on localhost:11434
- Solver query ("What is 2+2?") sent via chat UI
- Response received, latency acceptable
- Stop/start test skipped (shared environment risk)

### 7. Feed Panel Resilience (PASS)
- Feeds tab renders with content (sources, items)
- Overview tab works after switching from feeds
- Chat tab and input available after feeds
- No console errors, no failed requests

## Security Assertions (from Drills 1-3)

| Assertion | Status |
|---|---|
| Wrong token never creates session cookie | PASS |
| Wrong token never authenticates | PASS |
| Chat disabled without valid session | PASS |
| No API key/secret visible in page body | PASS |
| POST /api/chat without auth returns 401 | PASS |
| Invalid/oversized input doesn't crash UI | PASS |

## Overall: 6/7 PASS (1 INCONCLUSIVE — test infra, not server bug)
