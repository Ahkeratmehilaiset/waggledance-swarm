# R22.1a hot-path benchmark realism

- timestamp: 2026-05-10T08:36:00Z
- owner: Codex
- branch: waggledance/r22-1a-hotpath-benchmark
- task: r22-1a-codex-hotpath-benchmark-2026-05-10

## Problem

The R21.5/R22 scale proof measured `RuntimeQueryRouter` without the
production `HotPathCache` attached. That made lookup latency numbers describe a
benchmark-only no-cache path rather than the live hot-path wiring used by
`tools/run_live_runtime_hotpath_proof.py`.

## Change

`tools/run_solver_scale_proof.py` now attaches `RuntimeGapDetector` and
`HotPathCache`, samples the lookup descriptors once, runs one cold/warmup pass,
then runs a second warm pass. The legacy top-level `lookup_p50_ms`,
`lookup_p95_ms`, `lookup_p99_ms`, and `lookup_mean_ms` fields now report the
steady warm-pass latency. The cold pass is preserved under
`lookup_cold_after_attach`, and `hot_path_cache_stats` records warm hits, cold
hits warmed, misses, and cache sizes.

## Measurements

Same local machine, same synthetic generator, no product runtime source change.

### Small reproducibility run

- command: `.venv/Scripts/python.exe tools/run_solver_scale_proof.py --out-dir .codex-audit/r22_1a_post_small --descriptors 240 --lookup-pass-count 120 --db .codex-audit/r22_1a_post_small.db`
- pre-change no-cache p50/p95/p99: `0.1987 / 0.4407 / 0.8853 ms`
- post-change cold p50/p95/p99: `0.2934 / 0.6614 / 0.8444 ms`
- post-change warm p50/p95/p99: `0.0162 / 0.0283 / 0.0393 ms`
- routing: `120/120` capability hits, `0` FIFO fallback, `0` misses

### 10k scale run

- command: `.venv/Scripts/python.exe tools/run_solver_scale_proof.py --out-dir .codex-audit/r22_1a_10k --descriptors 10000 --lookup-pass-count 1000 --db .codex-audit/r22_1a_10k.db`
- build time: `2.8847 s`
- build rate: `3466.5 descriptors/s`
- cold p50/p95/p99: `8.9993 / 21.8086 / 26.1322 ms`
- warm p50/p95/p99: `0.0333 / 0.0477 / 0.1376 ms`
- routing: `1000/1000` capability hits, `0` FIFO fallback, `0` misses

## Verification

- `.venv/Scripts/python.exe -m pytest tests/autonomy_growth/test_solver_scale_proof.py -q --basetemp=.codex-audit/pytest-r22-1a-scale`
- result: `22 passed in 5.67s`

## Follow-up

Do not implement R22.1b unless `HotPathCache.flush_signals()` is separately
measured as the real production bottleneck. R22.1a shows that the high lookup
p99 was primarily a measurement-shape problem, not a synchronous production
lookup-path problem.
