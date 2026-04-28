# Reality View — Truthfulness and Scale Posture

**Status:** Phase 10 P5. Reaffirms the Phase 9 truthfulness verdict (`docs/journal/2026-04-27_hologram_truthfulness_verdict.md`) and adds a scale-aware aggregator on top of it.

## Summary

The Phase 9 Reality View is **truthful** today: 11 panels, never-fabricate invariant, missing inputs render as `available=false` with a structured rationale string. That is unchanged.

What Phase 10 P5 adds: a **scale-aware aggregation layer** that reads from the control plane (P2) and produces aggregated panel data that scales to 10k+ solvers without claiming "one node per solver". Three panels (`cell_topology`, `builder_lane_status`, plus a new `solver_family_summary`) and a `provider_queue_summary` panel are now driven by the control plane through `RegistryQueries`. When the control plane is empty or unattached the aggregator returns `available=false` with an explicit rationale — the never-fabricate invariant is preserved.

## What was already truthful (Phase 9)

Per the 2026-04-27 verdict (carried forward unchanged):

* `waggledance/ui/hologram/reality_view.py` ships 11 panels: `mission_queue_top`, `attention_focus`, `top_tensions`, `top_blind_spots`, `self_model_summary`, `world_model_summary`, `dream_queue`, `meta_proposal_queue`, `vector_tier_heat`, `cell_topology`, `builder_lane_status`.
* The `_unavailable_panel(...)` helper is the explicit non-fabrication path. There is no placeholder-zero pattern in the source.
* The existing render at `docs/runs/phase9_reality_view_render.json` shows 5/11 panels populated against Session B `self_model_snapshot`, 6/11 honestly unavailable.
* The browser-visible page `web/hologram-brain-v6.html` is the served version; the Phase 9 HTTP route `waggledance/adapters/http/routes/hologram.py` serves it at `GET /hologram` and `GET /api/hologram/state`.
* Hex cell topology is **8 cells** (`general`, `thermal`, `energy`, `safety`, `seasonal`, `math`, `system`, `learning`), confirmed against `CLAUDE.md`, `schemas/solver_proposal.schema.json`, and `waggledance/core/hex_cell_topology.py`.
* 21 dedicated tests in `tests/test_phase9_reality_view.py` enforce: panel construction from real artifacts, never-fabricate-when-input-absent invariant, no-secrets-in-snapshot, no-absolute-paths-in-snapshot, source safety.

## What scale changed

At Phase 9 the panels were populated from in-memory Session B/C/D bundles. At Phase 10 — once the control plane is populated — there can be:

* tens of thousands of solvers across the 10 family kinds,
* thousands of capabilities with a non-trivial dependency DAG,
* hundreds of vector shards with their indexes,
* a continuous stream of provider / builder jobs.

A naive "list every solver as one item" approach would (a) blow up the response size, (b) ship an implicit lie that the UI represents per-solver state, and (c) expose internal IDs that have no operator value. Phase 10 P5 instead returns aggregates.

## Scale-aware aggregator

`waggledance/ui/hologram/scale_aware_aggregator.py` exports `build_scale_aware_panels(...)` which produces a `ScaleAwarePanels` bundle:

| Panel | Aggregation |
|---|---|
| `solver_family_summary` | One item per registered family: `{family, version, status, total_solvers, by_status: {draft: N, shadow: M, ...}}`. Capped at 256 items; truncation marker added if exceeded. |
| `cell_topology` | One item per discovered cell: `{cell, active_members, vector_shard_count}`. Cells discovered from `cell_membership` rows or caller hints. Capped at 256. |
| `builder_lane_status` | One item per status group: `{status, count}`. Computed via `SELECT status, COUNT(*) FROM builder_jobs GROUP BY status`. |
| `provider_queue_summary` | One item per `(provider, status)` pair: `{provider, status, count}`. |

If the control plane is `None` all four panels return `available=false` with rationale `control_plane_db_not_attached`. If a panel's source table is empty, that single panel returns `available=false` with the specific rationale (`no_solver_families_registered`, `no_builder_jobs_recorded_yet`, etc.) while others may still be available.

## Truth invariants (carried forward + extended)

1. **Never fabricate.** A panel without input data returns `available=false`; aggregation never invents counts or fills in zero-as-placeholder.
2. **No "one node per solver" implication.** The `solver_family_summary` and `cell_topology` panels report rollups, not solver lists. A future 3D rendering pass that wants per-solver visualisation MUST opt in explicitly via a separate detail endpoint (not implemented in Phase 10).
3. **Truncation is explicit.** When a hard cap (256 items per panel) is reached, a final synthetic item `{"truncated": true, "shown": N, "total": M}` is appended. Operators see immediately that the panel is not exhaustive.
4. **Aggregations are read-only.** The aggregator never writes to the control plane. It is safe to call from the HTTP route handler.
5. **Cell count is not hard-coded.** The aggregator discovers cells from `cell_membership` or accepts a caller-supplied list. Removing the 8-cell assumption from any future expansion is a no-op for this layer; the legacy 8-cell `general`/`thermal`/… set remains the canonical default at the solver-proposal schema level.

## What this does NOT change

* The Phase 9 11-panel surface. `reality_view.PANELS` still lists exactly 11. The aggregator output is consumed by the existing builder; it does not redefine the panel set.
* The HTTP route `/hologram` and `/api/hologram/state`. The route still composes the existing snapshot.
* The 3D rendering. There is no rich 3D UI in this repo; the browser-visible UI is `web/hologram-brain-v6.html`. No Phase 10 commit fakes a 3D rendering.
* The solver proposal cell enum (8 cells). That schema is unchanged.

## Cross-references

* `docs/journal/2026-04-27_hologram_truthfulness_verdict.md` — the audit that established Phase 9 truthfulness.
* `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` — substrate (P2) the aggregator reads from.
* `waggledance/ui/hologram/reality_view.py` — Phase 9 panel structure (unchanged).
* `tests/test_phase9_reality_view.py` — 21 invariant tests (Phase 9, still passing).
* `tests/ui_hologram/test_scale_aware_aggregator.py` — Phase 10 P5 aggregator tests.
