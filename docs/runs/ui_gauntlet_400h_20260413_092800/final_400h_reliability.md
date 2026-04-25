# 400h Campaign — Reliability Analysis (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-25T17:12:16+00:00

## Query-level reliability

- Active queries sent: 53769
- Response rate: 79.45%

## Latency distribution

| Bucket | Count | % |
|---|---|---|
| <1s | 0 | 0.0% |
| 1-2.5s | 19503 | 45.7% |
| 2.5-5s | 19201 | 44.9% |
| 5-10s | 2359 | 5.5% |
| 10-20s | 1655 | 3.9% |
| >20s | 1 | 0.0% |

## Backend health over time

- Per x.txt: health pass rate target ≥ 99%
- Actual: 1853/1960

## Restart recovery

- Controlled restart drill: from `fault_drills.md` (Phase 2 baseline).
- Uncontrolled restart events in campaign: see incident log category `backend_unhealthy`.
- Watchdog automated-recovery events since 2026-04-22: 0
