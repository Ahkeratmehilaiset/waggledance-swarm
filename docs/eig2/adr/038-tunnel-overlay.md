# ADR-038 — Tunnel overlay as sparse audited graph

* Status: **substrate implementation landing** (contract + ADR pinned; in-memory registry implemented; router/mining wiring deferred)
* Date: 2026-05-12
* Related: PR #282 (L1 solver-signal YAML registry, makes signals composable), ADR-021 (progressive replay)

## Context

Today's routing is **hex-cell-only**: `HexTopologyRegistry.select_origin_cell()` picks one cell based on query → solver_router dispatches. There is no shortcut path. A "kitchen heat regulation" query that semantically belongs to BOTH `home_comfort` cell and `thermal` solver lives in only ONE cell at routing time.

The 50-leaps menu (L2) calls for a **tunnel overlay**: a sparse audited graph of cross-cell shortcuts. Tunnels are learned from solver co-fire telemetry (L3 future) but the SHAPE of the registry is pinned now so consumers can subscribe.

## Decision

A new `TunnelRegistry` (lives at `waggledance/core/reasoning/tunnel_registry.py`) holds tunnels:

```python
@dataclass(frozen=True, slots=True)
class Tunnel:
    tunnel_id: str            # uuid4
    from_cell: str            # hex cell id
    to_solver: str            # solver_id OR capability_id
    trust_score: float        # 0.0-1.0, mined from co-fire stats
    provenance_event_id: str  # the event that justifies this tunnel
    added_at_utc: str         # ISO8601
    last_validated_utc: str   # last time the tunnel was re-validated
    direction: str            # "forward" or "negative" (negative = "do NOT route here")
```

Tunnels are **sparse**: only HIGH-CONFIDENCE shortcuts are added. Default threshold `trust_score >= 0.70`.

Routing path:

1. `select_origin_cell()` returns the cell as today.
2. NEW: `tunnel_registry.lookup_tunnels(from_cell, query)` returns 0..K candidate tunnels.
3. Each tunnel is a candidate route alongside the cell's native solvers.
4. Final routing picks weighted-best (cell-native vs tunnel candidates).

## Consequences

### Routing intelligence

* Multi-hop knowledge becomes 1-hop: "kitchen heat regulation" routes from `home_comfort` directly to `thermal` via a learned tunnel.
* Sparse-by-design: tunnels are auditable + few (10s, not 1000s). Operator can inspect the registry.

### Storage

* TunnelRegistry persisted as YAML at `configs/tunnel_overlay.yaml`. Operator-editable.
* Telemetry-mined tunnels appended atomically. Operator can hand-edit to add/remove specific tunnels.

### Operational

* TunnelRegistry MUST be queryable in <5 µs per lookup (matching the hot-path budget per ADR-027 + perf-budget L34).
* Tunnel additions are auditable via `provenance_event_id` (the co-fire event that justified the addition).

## Invariants

Pinned in `docs/eig2/contracts/tunnel_overlay.json` and verified by `tests/contracts/test_tunnel_overlay.py`.

1. **Sparse threshold.** Default `min_trust_score=0.70` to add a tunnel. Operator-tunable.
2. **Auditable.** Every tunnel has `provenance_event_id` pointing to the co-fire event that justified it. NULL provenance forbidden.
3. **Bidirectional kinds.** `direction` is `"forward"` (route this way) or `"negative"` (do NOT route this way). Negative tunnels (L5) extend the registry, do not create a separate registry.
4. **YAML persistence.** Lives at `configs/tunnel_overlay.yaml`. Loadable via yaml.CSafeLoader.
5. **Hot-path lookup budget.** `tunnel_registry.lookup_tunnels()` must be < 5 µs per call. Test this against the L34 hot-path budget contract.
6. **Slots dataclass.** Tunnel record uses `@dataclass(frozen=True, slots=True)` per L60-NEW pattern.
7. **Validation freshness.** A tunnel is REVALIDATED periodically (default 30 days). Tunnels not revalidated for > 90 days auto-archive (kept in YAML but `active: false`).

## Out of scope (this ADR)

* L3 tunnel mining (Hebbian co-fire learning) — covered by Codex queue L3.
* L4 multi-cell portfolio routing — covered by Codex L4.
* L5 negative tunnels — direction enum here, mining covered by Codex L5 (or this if I take it).
* Router wiring — tunnel lookups are not yet part of live route selection.

## References

* PR #282 (L1 solver-signal YAML registry, signal-composition substrate)
* ADR-027 (L15 risk-tiered L3 budget, routing budget framework)
* PR #290 (L34 hot-path budget contract, lookup_tunnels must stay within)
* PR #288 (L60-NEW slots=True pattern, frozen+slots dataclass)
* 50-leaps menu: L2 (this), L3 (Hebbian mining), L4 (portfolio), L5 (negative)
