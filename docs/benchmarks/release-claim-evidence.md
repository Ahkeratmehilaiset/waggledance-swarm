# Release claim evidence

Candidate source: `95643d57b7fb2e53044e19a3ff603e5a6f564ee9` (Train 4 plus C1, measured locally on 2026-11-27). This source includes the merges for [#2198](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/2198), [#2206](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/2206) and [#2203](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/2203).

| Candidate measurement | Workload and observed result | Evidence SHA-256 |
| --- | --- | --- |
| First solver lookup pass after cache attach | 10,000 synthetic descriptors, 1,000 queries, 1,000 hits, zero misses and zero FIFO fallbacks. p50 2.0009 ms, p95 4.5955 ms, p99 6.8742 ms. Entries warm during this pass. | `845FA4E6DA6CC1466DB18741B4167AF55623F4C0FE0DAE9821ED795E6C984F38` |
| Warm solver lookup pass | The same descriptors and 1,000 queries, 1,000 hits, zero misses and zero FIFO fallbacks. p50 0.0092 ms, p95 0.0104 ms, p99 0.0204 ms. | `845FA4E6DA6CC1466DB18741B4167AF55623F4C0FE0DAE9821ED795E6C984F38` |
| 120-second local agent object spawn | 1745 in-window completions, 1746 starts, 1 late, zero failures. p50 66.5277 ms, p99 101.2399 ms. One worker, no model calls, local SQLite, cap 2,000 and peak 1746 active objects. | `A5B6EDC8344116A6911BBB0BC6A531304F59ACCB994D23CD2476C310AD1FA1C9` |

Lookup run: 2026-11-27T21:07:41.2112782+00:00 to 2026-11-27T21:07:47.3225721+00:00. Spawn run: 2026-11-27T20:52:13.1568889+00:00 to 2026-11-27T20:54:16.0757212+00:00. Keep these results attached to this exact source and workload. The spawn result measures local object creation.

## Reproduction

Run from a clean checkout of the named candidate. Use separate fresh paths in the lane audit directory for SQLite and output. Pin the spawn runner to SHA-256 `0D8DA5D9B79380AD9E5B8024F1C3FE1CE6DA395BB48B8DE35157AB6011BF6C96`. The result metadata records the interpreter and host used.

```powershell
python -I -B -X utf8 tools/run_solver_scale_proof.py --source-commit $candidate --descriptors 10000 --lookup-pass-count 1000 --db $lookupDb --out-dir $lookupOut
python -I -B -X utf8 $spawnRunner --source-commit $candidate --out-dir $spawnOut --cap 2000
```

The lookup proof records six source file hashes and the first and warm pass counts separately. The spawn proof retains all attempt timestamps, counts and SQLite totals. Keep the first and warm results paired with their sampling configuration. [Lookup implementation](https://github.com/Ahkeratmehilaiset/waggledance-swarm/blob/95643d57b7fb2e53044e19a3ff603e5a6f564ee9/tools/run_solver_scale_proof.py#L509-L528)

## Earlier measurements

- On 2026-11-27 at `beaab1a29698a2ec5c608430f6d0fc271fe115c2`, before C1 merged, the 10,000-descriptor lookup measured p99 5.0563 ms on the first pass and 0.0181 ms warm. The final-candidate table above uses the direct rerun at `95643d57`.
- The 2026-05-10 audit recorded 10,000-descriptor p99 28.7341 ms first pass and 0.0497 ms warm. At 50,000 descriptors it recorded p99 354.8117 ms first pass and 0.2198 ms warm, with 2,000/2,000 hits. Keep the 50,000-descriptor claim dated. [Historical lookup audit](https://github.com/Ahkeratmehilaiset/waggledance-swarm/blob/27e92e3bd8ac4e69bba1e04f3eb51d5e7c26f4b1/iterations/EVOLUTION_INDEX.md#L837-L870)
- The same day's audit recorded 1,004 local object spawns in 120 seconds (p50 88.8 ms, p99 1.43 s). The original runner, configuration and raw samples remain unverified. The current record is a separately specified run. [Historical spawn audit](https://github.com/Ahkeratmehilaiset/waggledance-swarm/blob/27e92e3bd8ac4e69bba1e04f3eb51d5e7c26f4b1/iterations/EVOLUTION_INDEX.md#L879-L919)

## Release claims still to reconcile

| Claim | Verified source evidence | Required release evidence |
| --- | --- | --- |
| Axis B routing quality | 0.5000 to 0.7619 is +26.19 percentage points, or +52.38% relative to baseline. README currently says +52.4 pp. [Evaluation](https://github.com/Ahkeratmehilaiset/waggledance-swarm/blob/6b72aab66951667db610cc74ef56cb704d1ee809/iterations/codex_scout_tasks/r22_2_hex_aligned_eval_2026_05_10.json#L189-L207) · [README](https://github.com/Ahkeratmehilaiset/waggledance-swarm/blob/6b72aab66951667db610cc74ef56cb704d1ee809/README.md#L28) | Correct the arithmetic in release copy and retain the measured corpus context. |
| CI totals and version | The v3.13.0 tag resolves to `d99bcf4646305efe2b06f6bac127e5a201223467`. Its release records 34,646 passed and 2,984 skipped on Linux/Python 3.13 across both scopes. `pyproject.toml` reports 3.12.0 at the evidence base. [Release](https://github.com/Ahkeratmehilaiset/waggledance-swarm/releases/tag/v3.13.0) · [Version field](https://github.com/Ahkeratmehilaiset/waggledance-swarm/blob/6b72aab66951667db610cc74ef56cb704d1ee809/pyproject.toml#L7) | Cite candidate totals only after CI runs at the exact tag SHA. Reconcile the version in the release PR. |
| Mind refresh peak and installed read | PR #2189 reports first-start measurement and an open refresh measurement on the installed head before the log reaches 256 MiB, estimated around 2026-12-25. [Release condition](https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/2189) | Record the installed SHA, source size, peak private commit and peak/source ratio during refresh. Use the condition in #2189 to assess the factor if the ratio exceeds 8x. Record an operator installation and a nonempty lane read before describing Train 4 features as shipped. |
