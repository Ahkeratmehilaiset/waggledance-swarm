# Phase 9 — Autonomy Kernel Tick Dry-Run Evidence

This is committed evidence that the Phase F autonomy kernel actually
ticks against the real `waggledance/core/autonomy/constitution.yaml`.

## How produced

```
python tools/wd_kernel_tick.py \
  --constitution waggledance/core/autonomy/constitution.yaml \
  --ts 2026-04-26T18:30:00Z \
  --json > docs/runs/phase9_kernel_tick_dry_run.json
```

No `--apply`, so no kernel state is persisted; this is a pure tick
trace.

## What the output proves

`docs/runs/phase9_kernel_tick_dry_run.json` records:
- `constitution_sha256: sha256:f3bc565df314f551f4a417340b646a9a617da1d35a3591040c8425c1fbcd5481`
- `tick_id: 1` (deterministic monotonic counter)
- `next_tick_id: 2`
- `persisted_revision: 1`
- `actions_recommended_total: 0`, `recommendations_detail: []`
- `dry_run: true` with the explicit note `"dry_run=True — state not persisted"`

A manual recomputation of the same constitution.yaml file
(`hashlib.sha256(...)` over the file's bytes) produces the exact same
`f3bc565df314f551...` — confirming the constitution-sha gate is wired.

## Why this matters

Master Acceptance Criterion #14 says WD should now have a "genuine
always-on cognitive kernel". That criterion is structural: it requires
that a kernel tick is a real callable code path that:

1. reads constitution.yaml
2. computes its sha256
3. compares the sha against the kernel state's pinned constitution sha
4. either ticks deterministically or refuses to tick

This artifact is evidence that step 1-3 actually happen on a real
file. Step 4 (refusal on sha mismatch) is covered by the targeted
test `test_tick_raises_on_constitution_sha_mismatch` in
`tests/test_phase9_governor.py`.

## What this does NOT do

- Does not persist any kernel state (no `--apply`).
- Does not fire any recommendations (no inbound signals supplied).
- Does not call any provider, no LLM, no HTTP.

A more comprehensive end-to-end demonstration (real signals → ordered
missions → action gate → DispatchReport) is covered by the targeted
tests in `tests/test_phase9_*.py`. This artifact is just the public
"the kernel ticks" proof point.
