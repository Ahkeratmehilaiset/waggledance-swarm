# UI Gauntlet Plan — Post-v3.5.7 Hardening

- **Date:** 2026-04-12
- **Branch:** `hardening/post-v3.5.7-ui-gauntlet`
- **Base:** `main` @ `ce282af` (v3.5.7 Honest Hologram Release)
- **Server:** Port 8002 (dedicated gauntlet instance, full non-stub mode)
- **API key:** Ephemeral in `%TEMP%\waggle_gauntlet_8002.key`
- **Automation:** Playwright 1.58.0 + Chromium (headless)

## Phases

### Phase A (0h-2h): Baseline freeze + harness scaffold
- Verify repo state (tag, branch, main status)
- Start dedicated server on port 8002
- Verify all baseline endpoints
- Build Playwright harness (`tests/e2e/ui_gauntlet_harness.py`)
- Generate query corpus (500+ queries across 7 buckets)

### Phase B (2h-6h): UI fidelity audit
- Test all 11 tabs across 3 viewports (1280x720, 1536x864, 1920x1080)
- Screenshot each tab in each viewport
- Capture console errors, failed network requests
- Check DOM content, selector visibility
- Special attention: feeds panel (source_count, items_count, state dots)
- Special attention: chat panel (disabled/enabled state, message ordering)

### Phase C (6h-14h): 500-query chat gauntlet via UI
- 7 buckets: normal(120+), ambiguous(60+), structured(60+),
  multilingual(60+), adversarial(80+), edge_case(40+), burst(40+)
- All queries via hologram chat UI, not direct API
- Track: send success, response visibility, latency, console errors,
  failed requests, DOM integrity, XSS detection, session state
- Security assertions on adversarial bucket

### Phase D (14h-20h): Fault drills
- Wrong token, expired session, no-auth POST
- Invalid body, oversized input
- Server restart mid-session
- Ollama stop/start
- DNS-blocked feed monitoring

### Phase E (20h-28h): Mixed long-run UI soak
- 8-10h browser + backend monitoring
- Periodic chat bursts, feed polling, websocket checks
- Memory/CPU tracking, error accumulation

### Phase F (28h-30h): Analysis + report
- Issue table by severity
- Findings classification (works/flaky/broken/security)
- Fix small high-ROI bugs
- Final summary report

## Harness location
- `tests/e2e/ui_gauntlet_harness.py` — main harness
- `tests/e2e/conftest.py` — shared config
- `docs/runs/ui_gauntlet_20260412/` — all artifacts

## Installed tooling
- `playwright` 1.58.0 (already in venv, Chromium browser installed)
