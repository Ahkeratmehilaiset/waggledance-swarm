# 400h Campaign — Reliability Analysis (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T14:11:03+00:00

## Query-level reliability

- Active queries sent: 35952
- Response rate: 70.95%

## Latency distribution

| Bucket | Count | % |
|---|---|---|
| <1s | 0 | 0.0% |
| 1-2.5s | 17025 | 66.7% |
| 2.5-5s | 4597 | 18.0% |
| 5-10s | 2254 | 8.8% |
| 10-20s | 1630 | 6.4% |
| >20s | 1 | 0.0% |

## Backend health over time

- Per x.txt: health pass rate target ≥ 99%
- Actual: 661/681

## Restart recovery

- Controlled restart drill: from `fault_drills.md` (Phase 2 baseline).
- Uncontrolled restart events in campaign: see incident log category `backend_unhealthy`.
- Watchdog automated-recovery events since 2026-04-22: 0
