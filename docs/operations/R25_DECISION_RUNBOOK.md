# R25 decision runbook — production traffic histogram

**Audience**: operator deciding whether to commit 4–8 weeks of engineering to R25 (3D hex per-cell DB sharding).

**Authored**: 2026-05-11 by Claude as part of the post-Option-B execution per joint Claude+Codex recommendation. R25 sharding is **deferred** until production traffic data confirms the N=4-cell concurrent-writer knee is actually crossed in real use.

**Tool**: `tools/runtime_gap_signal_concurrency_histogram.py` (landed in PR #224). Reads any `ControlPlaneDB` SQLite snapshot read-only.

---

## TL;DR — one command

```powershell
# Run against a 24h production snapshot once Option B has been in
# production at least one full day.
python -m tools.runtime_gap_signal_concurrency_histogram `
  --db C:\path\to\production\control_plane.db `
  --window-seconds 1 `
  --out-json r25_decision_2026_MM_DD.json
```

Output ends with a verdict line:

```
R25 decision signal: <insufficient-data | r25-not-needed | r25-defer | r25-consider | r25-strongly-recommended>
  <rationale>
```

That verdict is the decision.

---

## Prerequisite checklist

Run the tool only when ALL of these are true:

- [ ] Option B (PRs #223 + #227 + #229 + #230 + #231) has been on `main` and in production at least **1 day** (preferably 1 week — gives the workload time to spread across the day's natural cycles).
- [ ] Production ControlPlaneDB is **populated with real `runtime_gap_signal` writes** — not a fresh DB, not a synthetic one.
- [ ] You can take a **read-only snapshot** (operator decision: live DB read OR cold snapshot copy; the tool sets `PRAGMA query_only = ON` so live read is safe, but snapshot is mentally cleaner).

Without this, the verdict is meaningless ("insufficient-data" or "r25-not-needed" by accident).

---

## How to take the production snapshot

```powershell
# Option A — cold snapshot (recommended for clean measurement)
# Stop the WaggleDance container or pause writes:
docker stop waggledance-prod   # or whatever your container name is
Copy-Item C:\path\to\production\control_plane.db C:\snapshots\control_plane_2026_MM_DD.db
docker start waggledance-prod

# Option B — live read (also safe; tool uses query_only=ON)
# No setup; just point --db at the live path.
```

---

## Running the tool

```powershell
cd C:\Python\project2-master

python -m tools.runtime_gap_signal_concurrency_histogram `
  --db C:\snapshots\control_plane_2026_MM_DD.db `
  --window-seconds 1 `
  --out-json C:\snapshots\r25_decision_2026_MM_DD.json
```

Options:

- `--window-seconds <N>`: how granular the concurrency window is. Default `1` second. Use `5` or `10` for noisier traffic if 1-second windows give all-zeros most of the time.
- `--out-json <path>`: optional JSON dump for the full histogram + percentiles + per-cell counts. Recommended — keeps the data for later re-analysis without re-running.

---

## Reading the output

### Stdout summary

```
# runtime_gap_signal concurrency histogram
# db_path: C:\snapshots\control_plane_2026_MM_DD.db
# window: 1 s
# span: 86400 s (12345 rows)
# distinct cells: 7
# total observation windows: 86401
# active windows (>=1 write): 12345

Per-cell write count:
  hub                       4521
  bee_ops                   2103
  ...

Concurrency-per-window stats (active windows only):
  min=1  p50=2  p99=5  max=7  mean=2.13

SLA-threshold buckets (pct of TOTAL observation windows with >= N concurrent cells):
  N_ge_2:  8.234 %  (7115 of 86401 windows)
  N_ge_3:  3.451 %  (2982 of 86401 windows)
  N_ge_4:  1.234 %  (1066 of 86401 windows)
  N_ge_5:  0.412 %  (356 of 86401 windows)
  N_ge_6:  0.103 %  (89 of 86401 windows)
  N_ge_7:  0.012 %  (10 of 86401 windows)

R25 decision signal: r25-defer
  1.234 percent of windows have >=4 concurrent writer cells (knee
  region). R25 deferral is acceptable until any specific write-p99
  SLA miss is observed in production; re-run with a larger sample
  if uncertain.
```

### Verdict ladder

The thresholds below match the tool's actual logic in
`tools/runtime_gap_signal_concurrency_histogram.py` exactly:
- `pct_n2` is the percent of windows with ≥ 2 concurrent cells.
- `pct_n4` is the percent of windows with ≥ 4 concurrent cells.
- Comparisons are strict-greater-than in the tool, so the boundaries are inclusive on the lower side.

| Verdict | Condition (matches tool code) | Recommended action |
|---|---|---|
| `insufficient-data` | total observation windows < 60 | Collect a longer span. The histogram is statistically meaningless below ~1 minute of data. |
| `r25-not-needed` | `pct_n2 <= 5 %` (i.e. ≤ 5 % of windows have ≥ 2 concurrent cells) | **Close the R25 track formally.** Production is single-branch-dominant. Re-run after major workload changes. |
| `r25-defer` | `pct_n2 > 5 %` AND `pct_n4 <= 10 %` | **Keep R25 deferred.** Below the Run F knee at N=4. Re-check in 3 months or after major workload changes. |
| `r25-consider` | `pct_n4 > 10 %` AND `pct_n4 <= 50 %` | **Collect a larger sample (7d minimum; longer if known weekly / month-end / batch cycles).** Knee region — single 24 h sample may be unrepresentative. |
| `r25-strongly-recommended` | `pct_n4 > 50 %` | **Start the R25 RFC.** Production routinely crosses the knee; sharding will pay for itself. Codex's 12-document scout at `iterations/codex_scout_tasks/r25_*_codex_*.md` is the starting point. |

### What "knee at N=4" means

The Run F measurement (`iterations/EVOLUTION_INDEX.md` entry `r22-2d-branch-isolation-stress-inflection`) showed that write p99 latency jumps **65 % between N=3 and N=4 concurrent writer cells**:

- N=3 concurrent: ~87 ms p99
- N=4 concurrent: ~145 ms p99 (knee crossed)
- N=5–6 concurrent: ~167–197 ms p99 (plateau)

If your production routinely has 4+ cells writing in the same 1-second window, you are **on the wrong side of the knee**. R25 sharding solves this by giving each cell its own DB so writes don't contend for the global ControlPlaneDB lock.

If your production rarely has even 2 concurrent cells, you are **on the safe side**. R25 is over-engineering for that traffic pattern.

---

## What to do with the verdict

### `r25-not-needed`

1. Save the JSON output under `iterations/codex_scout_tasks/r25_decision_<date>.json` for the historical record.
2. Emit a bridge `decision/closed` event on task `r25-3d-hex-sharding-codex-2026-05-10` referencing the JSON.
3. **Close the R25 track formally.** Codex's 12-document scout becomes reference material; no implementation.

### `r25-defer`

1. Save JSON.
2. Set a calendar reminder for 3 months out (or after the next major workload shift) to re-run.
3. Keep Codex's R25 scout on disk; no implementation.

### `r25-consider`

1. Save JSON.
2. Collect a **7-day** sample using a longer-span snapshot.
3. Re-run with `--window-seconds 1` and also `--window-seconds 5` for sanity. If both verdicts come back ≥ `r25-consider`, escalate to `r25-strongly-recommended` mentally.
4. Defer R25 implementation pending the larger sample.

### `r25-strongly-recommended`

1. Save JSON.
2. Read Codex's full R25 scout pack:
   - `iterations/codex_scout_tasks/r25_3d_hex_sharding_codex_verdict_2026_05_10.md` (Codex's pre-Option-B verdict — note that the framing has shifted post-Option-B; R25 now only addresses write contention)
   - `iterations/codex_scout_tasks/r25_db_sharding_hashing_codex_2026_05_10.md` (sharding design)
   - `iterations/codex_scout_tasks/r25_branch_routing_codex_2026_05_10.md` (routing layer)
   - `iterations/codex_scout_tasks/r25_implementation_roadmap_codex_2026_05_10.md` (phasing)
3. Operator decides: start R25 RFC (formal tracked doc), or escalate the timeline.

---

## Anti-claims

- **The tool measures ONE table** (`runtime_gap_signals`). If your production has contention on a different table (e.g., `solver_artifacts` or `vector_events`), this tool will miss it. The Run A–F measurement series specifically targeted `runtime_gap_signals` because that was the operationally-painful regime; if a different table emerges as the bottleneck, a per-table histogram is the right tool, not this one.
- **24 hours may not be enough** for a representative sample. If your production has a weekly cycle (e.g., batch-jobs on Saturday), use a 7-day sample.
- **A `r25-not-needed` verdict today does not mean R25 will never be needed.** Workload patterns shift. Re-run periodically.
- **R25 is one option for the write-contention regime.** Alternatives in increasing order of cost:
  (a) **Table-level partitioning** of `runtime_gap_signals` (split the single table into per-cell tables within the same DB; smaller migration than full per-cell DB sharding).
  (b) **Batch-coalescing** of `runtime_gap_signals` writes at the application layer (group multiple writes from the same cell into a single transaction, reducing lock-acquisition frequency).
  (c) **Lock-relaxation for writes** (similar shape to Option B for reads but harder because writes need atomicity; would need a per-cell write queue with serialized flush).
  (d) **Full R25 per-cell DB sharding** (the heavy option in the Codex scout pack).
  Verdict `r25-consider` could be addressed by (a) or (b) before committing to (d).

---

## Related artifacts

- **Tool**: `tools/runtime_gap_signal_concurrency_histogram.py` (PR #224, merged 2026-05-11)
- **Run F measurement**: `iterations/EVOLUTION_INDEX.md` entry `r22-2d-branch-isolation-stress-inflection`
- **Codex R25 scout pack**: 12 files under `iterations/codex_scout_tasks/r25_*_codex_*.md`
- **Option B that eliminated the read-side regime**: PRs #223 + #227 + #229 + #230 + #231 (26/27 ControlPlaneDB read methods covered)
- **This runbook**: `docs/operations/R25_DECISION_RUNBOOK.md`

---

*Last updated 2026-05-11 — Claude + Codex joint output. Cross-review round 1 requested via bridge.*
