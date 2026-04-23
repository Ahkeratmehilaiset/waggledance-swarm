# 400h Campaign — Reliability Analysis (MID-CAMPAIGN (TBD on completion))

**Campaign:** `ui_gauntlet_400h_20260413_092800`
**Generated:** 2026-04-23T12:40:49+00:00

## Query-level reliability

- Active queries sent: 35350
- Response rate: 70.49%

## Latency distribution

| Bucket | Count | % |
|---|---|---|
| <1s | 0 | 0.0% |
| 1-2.5s | 16724 | 67.1% |
| 2.5-5s | 4309 | 17.3% |
| 5-10s | 2253 | 9.0% |
| 10-20s | 1630 | 6.5% |
| >20s | 1 | 0.0% |

## Backend health over time

- Per x.txt: health pass rate target ≥ 99%
- Actual: 661/681

## Restart recovery

- Controlled restart drill: from `fault_drills.md` (Phase 2 baseline).
- Uncontrolled restart events in campaign: see incident log category `backend_unhealthy`.
- Watchdog automated-recovery events since 2026-04-22: 0
