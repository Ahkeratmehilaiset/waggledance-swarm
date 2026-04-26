# 400h Campaign — Reliability Analysis (FINAL)

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-26T09:14:12+00:00

## Query-level reliability

- Active queries sent: 59441
- Response rate: 81.07%

## Latency distribution

| Bucket | Count | % |
|---|---|---|
| <1s | 0 | 0.0% |
| 1-2.5s | 20173 | 41.9% |
| 2.5-5s | 23972 | 49.7% |
| 5-10s | 2378 | 4.9% |
| 10-20s | 1664 | 3.5% |
| >20s | 1 | 0.0% |

## Backend health over time

- Per x.txt: health pass rate target ≥ 99%
- Actual: 2295/2431

## Restart recovery

- Controlled restart drill: from `fault_drills.md` (Phase 2 baseline).
- Uncontrolled restart events in campaign: see incident log category `backend_unhealthy`.
- Watchdog automated-recovery events since 2026-04-22: 0
