# 400h Campaign Plan

- **Campaign ID:** `ui_gauntlet_400h_20260413_092800`
- **Start:** 2026-04-13

## Target Hours

| Mode | Target | Purpose |
|---|---|---|
| HOT | 80 h | Stress chat, auth, XSS, DOM, session, query routing |
| WARM | 120 h | Long-duration UI stability |
| COLD | 200 h | Backend truth spine |
| **Total** | **400 h** | |

## Segment Model

- Default segment: 8 h
- 50 segments nominal (4h / 8h / 12h flexible)
- Each segment writes: `segment_report_NNN.md`, `segment_metrics_NNN.json`, updated `campaign_state.json`

## Pre-Campaign Gates

1. **Phase 0 — Truth intake** (this session)
2. **Phase 1 — Harness hardening** (mandatory before long runs)
   - Remove all `networkidle` from hologram routes
   - Create reusable `open_authenticated_hologram()` helper
   - Add readiness helpers: `wait_for_auth_ready`, `ensure_tab_selected`, `wait_for_chat_ready`
   - Resilient context recycling with backend health checks
   - Query resume from JSONL (no double-counting)
   - Separate tab-switch failures from chat failures
   - Real controlled server restart drill
   - Incident logging to JSONL
   - Dry-run harness validation
3. **Phase 2 — Baseline validation**
   - Server bring-up on 8002
   - UI fidelity 33/33
   - HOT mini-run ≥200 queries
   - Fault drills 7/7 (with real restart drill)
   - Warm mini-soak ≥2h
   - CI baseline check

## Campaign Execution (Phases 3-6)

Resumable segments. Each segment:
1. Determine mode from campaign_state.json
2. Run mode-specific checks
3. Write segment report + metrics
4. Update campaign_state.json
5. Commit checkpoint if green

## Exit Criteria (Phase 7)

Cumulative green hours ≥ 400 across all modes:
- HOT ≥ 80h, WARM ≥ 120h, COLD ≥ 200h
- Write final findings, summary, reliability, incident matrix

## Post-Campaign (Phases 8-12)

- Docs/GitHub narrative sync
- Release decision (PATH = DOC_SYNC_ONLY if PRODUCT diff empty)
- CI/workflow truth verification
- Final stdout summary
