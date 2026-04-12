# UI Fidelity Baseline — Phase B

- **Date:** 2026-04-12
- **Server:** Port 8002 (dedicated gauntlet, full non-stub mode)
- **Browser:** Playwright 1.58.0 + Chromium (headless)
- **Base:** v3.5.7 @ `ce282af` on `hardening/post-v3.5.7-ui-gauntlet`

## Summary

**33/33 checks PASS across all 11 tabs x 3 viewports.**
- 0 console errors
- 0 failed network requests
- All tabs render content, all selectors visible

## Viewport Results

| Viewport | Tabs Passed | Console Errors | Failed Requests |
|---|---|---|---|
| 1280x720 | 11/11 | 0 | 0 |
| 1536x864 | 11/11 | 0 | 0 |
| 1920x1080 | 11/11 | 0 | 0 |

## Tab-by-Tab Details

### Bootstrap Flow
- `/hologram` without token: loads page, auth=false
- `/hologram?token=<key>`: 303 redirect, `Set-Cookie: waggle_session` (HttpOnly, 1h TTL)
- After bootstrap: `/api/auth/check` returns `{authenticated: true}`
- Chat input enables after auth
- Token not visible in URL after redirect

### Overview Tab
- 3D hologram brain renders (32 nodes visible)
- Node ring visible in all viewports
- No content clipping at 1280x720

### Memory Tab
- Memory facts panel renders
- Content length adequate (344+ chars at 1280x720)

### Reasoning Tab
- Reasoning chain panel visible
- Content renders across all viewports

### Micromodels Tab
- Model status panel visible
- Largest content area (495+ chars at 1280x720)

### Learning Tab
- Learning metrics visible
- No layout issues

### Feeds Tab (Special Attention)
- **source_count visible**: Yes (5 sources shown)
- **items_count visible**: Yes
- **State dots visible**: Yes (green/yellow indicators)
- **latest_items/latest_value visible**: Yes
- **rss_ha_blog situation**: Shows honestly as degraded state, does not break rest of UI
- Content length: 1356+ chars at 1280x720 (richest panel)
- No clipping at smallest viewport

### Ops Tab
- Operations dashboard renders
- All viewports OK

### Mesh Tab
- Mesh topology visible
- Node connections render

### Trace Tab
- Trace panel renders
- Event log visible

### MAGMA Tab
- MAGMA status panel visible
- Rendering OK across viewports

### Chat Tab (Special Attention)
- **Disabled state without session**: Confirmed (input absent before auth)
- **Enabled state after session**: Confirmed (input visible, not disabled)
- **Send button visible**: Yes
- **Input placeholder**: "Type a message..."
- **Message ordering**: Correct (user message appears, then response)
- **WebSocket**: Connected after auth, does not go dead after first message

## Screenshots

36 screenshots captured in `screenshots/`:
- `A_01_no_auth.png` — pre-auth state
- `A_02_authed.png` — post-auth state
- `A_03_chat_tab.png` — chat panel active
- `B_{viewport}_{tab}.png` — all 33 tab/viewport combos

## Gates (Phase B)

| Gate | Status |
|---|---|
| UI renders in all 3 viewports | PASS |
| 0 console errors in baseline flow | PASS |
| 0 failed requests in baseline flow | PASS |
| 0 auth bypass (wrong token tested) | PASS |
| 0 query-token leak after redirect | PASS |
| Chat disabled before auth | PASS |
| Chat enabled after auth | PASS |
| WebSocket connected after auth | PASS |

## Conclusion

The hologram dashboard UI is fully functional across all tested viewports with zero errors. All 11 tabs render correctly, the auth flow works as designed, and the chat panel enables/disables appropriately based on session state.
