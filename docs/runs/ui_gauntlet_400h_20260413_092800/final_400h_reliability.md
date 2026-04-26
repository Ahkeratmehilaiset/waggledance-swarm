# 400h Campaign — Reliability Analysis (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-26T03:13:29+00:00

## Query-level reliability

- Active queries sent: 57321
- Response rate: 80.51%

## Latency distribution

| Bucket | Count | % |
|---|---|---|
| <1s | 0 | 0.0% |
| 1-2.5s | 19917 | 43.2% |
| 2.5-5s | 22195 | 48.1% |
| 5-10s | 2373 | 5.1% |
| 10-20s | 1661 | 3.6% |
| >20s | 1 | 0.0% |

## Backend health over time

- Per x.txt: health pass rate target ≥ 99%
- Actual: 2074/2195

## Restart recovery

- Controlled restart drill: from `fault_drills.md` (Phase 2 baseline).
- Uncontrolled restart events in campaign: see incident log category `backend_unhealthy`.
- Watchdog automated-recovery events since 2026-04-22: 0
