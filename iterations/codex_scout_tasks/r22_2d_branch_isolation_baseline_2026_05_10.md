# R22 2D branch-isolation baseline

- timestamp: 2026-05-10T12:51:13Z
- model: Codex GPT-5
- scope: measurement-only baseline for current 2D hex topology
- git head: `6cbe1d9c4bc5`
- output JSON: `iterations/codex_scout_tasks/r22_2d_branch_isolation_baseline_2026_05_10.json`

## Why this exists

R25/3D was paused until the 2D path is finished and measured. This
baseline measures whether the current 2D hex branches interfere with
each other through the shared global ControlPlaneDB write path.

No runtime behavior changed. No schema changed. No sharding or 3D
coordinate was added.

## Command

```powershell
C:\Python\project2-master\.venv\Scripts\python.exe tools\run_branch_isolation_benchmark.py `
  --db .codex-audit\r22_branch_isolation.sqlite `
  --out-json .codex-audit\r22_branch_isolation_baseline_2026_05_10.json `
  --repeats 3 `
  --probe-events 120 `
  --hot-events 800 `
  --uniform-events-per-branch 60 `
  --cold-flood-events-per-branch 80
```

The generated JSON was copied to:

```text
iterations/codex_scout_tasks/r22_2d_branch_isolation_baseline_2026_05_10.json
```

## Result summary

Current 2D topology uses the 7 configured cells from `configs/hex_cells.yaml`.
The benchmark probes `hub` while other branches write runtime gap signals
through the same global ControlPlaneDB.

| Profile | p99 mean |
|---|---:|
| idle `hub` probe | 12.9381 ms |
| `hub` probe while `bee_ops` is hot | 31.0522 ms |
| `hub` probe during adversarial flood from all other branches | 167.8618 ms |

Derived ratios:

- single-hot degradation: `2.4001x`
- adversarial degradation: `12.9743x`
- uniform multi-branch p99 CV: `0.5505`
- branch touch count target for hit cases: `1.0`

## Interpretation

The result shows real cross-branch interference in the current 2D/global-DB
model. This does not prove that 3D is needed. It proves that the next 2D
release work should keep branch-local write pressure visible and should not
claim branch isolation without a before/after metric.

The likely near-term fix, if we optimize this later, is still 2D:

- batch or buffer runtime-gap writes more aggressively
- reduce synchronous global ControlPlaneDB writes from hot paths
- consider a feature-flagged per-cell event shard only after the R22.5 stable path

## Verification

```powershell
C:\Python\project2-master\.venv\Scripts\python.exe -m pytest tests\test_r22_branch_isolation_benchmark.py -q --basetemp=.codex-audit\pytest-r22-branch-isolation
```

Result:

```text
1 passed in 1.22s
```

## Release impact

This is safe for the 2D release path because it is a tooling/test/report
change only. It gives R22.5 a concrete baseline for the branch-isolation
question and keeps R25 deferred until the operator explicitly reopens it.
