# 2026-04-27: Hologram / Reality View truthfulness verdict

Performed under WD `Claude_Code_unified_release_finalization_prompt_v3.md` Phase 4.

## Verdict: ✅ Truthful — no correction required

## Inspection results

### Implementation reality

| Question | Reality |
|---|---|
| Real Reality View implementation or just scaffolded data classes? | **Real implementation** — `waggledance/ui/hologram/reality_view.py` has frozen dataclass `RealityPanel`, real `build_panels_from_state(...)` that constructs panels from optional inputs, `_unavailable_panel(...)` helper for missing data, and a real `to_dict()`/`to_json()` serializer |
| How many panels exist structurally? | **11** — the `PANELS` tuple at line 67 lists: `mission_queue_top`, `attention_focus`, `top_tensions`, `top_blind_spots`, `self_model_summary`, `world_model_summary`, `dream_queue`, `meta_proposal_queue`, `vector_tier_heat`, `cell_topology`, `builder_lane_status` |
| How many populated from real current data vs placeholders? | Depends on inputs supplied. `build_panels_from_state(...)` accepts 6 optional inputs (`mission_queue`, `self_model`, `world_model_snapshot`, `dream_curriculum`, `hive_review_bundle`, `vector_graph`); panels with their input absent return `_unavailable_panel(...)` with structured `rationale_if_unavailable`. Real evidence render at `docs/runs/phase9_reality_view_render.json` shows 5/11 populated (against Session B `self_model_snapshot`), 6/11 honestly unavailable |
| Is `cell_topology` actually implemented, and with how many cells? | Yes — Phase K `waggledance/core/hex_topology/cell_runtime.py` has `LIVE_STATES` (4: `active`, `shadow_only`, `observing`, `retired`-equivalent) and `SUBDIVISION_STATES` (4). Solver hex cells (separate concept) at `waggledance/core/hex_cell_topology.py` are **8 cells** (`general`, `thermal`, `energy`, `safety`, `seasonal`, `math`, `system`, `learning`) per CLAUDE.md and `schemas/solver_proposal.schema.json` enforcement |
| Is there an actual browser-visible UI page? | Yes — `web/hologram-brain-v6.html` (and v5 archive) served by `waggledance/adapters/http/routes/hologram.py` at `GET /hologram` and `GET /api/hologram/state` |
| Is the old hologram still the actual default UI anywhere? | The `/hologram` route serves the Reality View. The "old hologram" v5 HTML still exists alongside v6 but v6 is the served version per the Dockerfile comment ("the live UI is now served as a static HTML file (web/hologram-brain-v6.html) rendered by waggledance/adapters/http/routes/hologram.py") |

### README claims vs reality

The current `README.md` (post-v3.6.0) makes these Reality View claims:

> "Reality View. A 11-panel structured view of the system's current state — never fabricates values; missing data shows up as `available=false` with a structured rationale, not as zero or as a guess."

✅ TRUE. PANELS tuple has exactly 11 entries. `_unavailable_panel(...)` is the explicit non-fabrication path. No placeholder-zero pattern in the source.

> "Phase P | `ui/hologram/reality_view.py` | 11-panel Reality View (never-fabricate invariant)"

✅ TRUE. Matches the source.

> "The `/hologram` page renders an 11-panel structured operator view. Each panel is one of: available=true with real items, OR available=false with a structured rationale_if_unavailable string."

✅ TRUE. Matches the implementation contract.

> "A real evidence render against Session B `self_model_snapshot` is committed at `docs/runs/phase9_reality_view_render.json` — 5/11 panels populated, 6/11 honestly unavailable."

✅ TRUE. The committed JSON file shows exactly that distribution.

### Tests

`tests/test_phase9_reality_view.py` collected 21 tests. They include:
- panel construction from real artifacts
- never-fabricate-when-input-absent invariant
- no secrets in snapshot serialization
- no absolute paths in snapshot
- source safety (no forbidden imports)

All 21 pass within the targeted Phase 9 suite (657/657 in 7.45s).

### Hex cell topology — 8 cells, NOT inflated

The README's hex topology mention ("8 cells `general`, `thermal`, `energy`, `safety`, `seasonal`, `math`, `system`, `learning`") matches:
1. CLAUDE.md "Repo conventions worth knowing" line
2. `schemas/solver_proposal.schema.json` cell enum
3. `waggledance/core/hex_cell_topology.py` runtime tagging

This is the **current scaling unit** — not a future-only larger runtime topology. The README does not claim more than 8 cells anywhere.

### What is NOT claimed

The README and CHANGELOG explicitly DO NOT claim:
- That Phase 9 hex topology has been scaled beyond 8 cells
- That the live runtime path uses the Phase 9 fabric (atomic flip is explicitly deferred)
- That all panels are always populated (5/11 evidence render is honestly disclosed)
- That production-grade UI hardening has happened (the v3.5.7 "Honest Hologram Release" line is in CHANGELOG history but `Updated:` field of CURRENT_STATUS.md was about to be corrected to v3.6.0 in this same PR)

## Strategy A compliance

- ✅ No code change to Reality View
- ✅ No README change for Reality View (already truthful)
- ✅ No CURRENT_STATUS change about Reality View beyond noting Phase 9 panel structure honestly
- ✅ No fabricated or overstated claims introduced
- ✅ Domain-neutral terms preserved (Reality View, panel, cell — not bee/hive metaphors)

## Conclusion

**Hologram / Reality View claims in README.md, CHANGELOG.md, CURRENT_STATUS.md, and source comments are all truthful as of `feature/post-v3.6.0-truthfulness` branch.** No correction was required for this surface in this session.
