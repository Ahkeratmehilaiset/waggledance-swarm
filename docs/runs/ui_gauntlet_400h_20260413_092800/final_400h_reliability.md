# 400h Campaign — Reliability Analysis (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-24T10:06:57+00:00

## Query-level reliability

- Active queries sent: 42906
- Response rate: 75.17%

## Latency distribution

| Bucket | Count | % |
|---|---|---|
| <1s | 0 | 0.0% |
| 1-2.5s | 18306 | 56.8% |
| 2.5-5s | 9974 | 30.9% |
| 5-10s | 2321 | 7.2% |
| 10-20s | 1649 | 5.1% |
| >20s | 1 | 0.0% |

## Backend health over time

- Per x.txt: health pass rate target ≥ 99%
- Actual: 975/1033

## Restart recovery

- Controlled restart drill: from `fault_drills.md` (Phase 2 baseline).
- Uncontrolled restart events in campaign: see incident log category `backend_unhealthy`.
- Watchdog automated-recovery events since 2026-04-22: 0
