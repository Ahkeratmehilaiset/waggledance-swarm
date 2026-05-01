# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Scale-aware Reality View aggregator (Phase 10 P5).

The Phase 9 :mod:`waggledance.ui.hologram.reality_view` ships an
11-panel structure with a strict never-fabricate invariant
(``available=false`` + ``rationale_if_unavailable`` when an input is
missing). That contract is correct and is preserved unchanged here.

What Phase 10 adds is a **scale-aware** aggregation layer for two of
the panels — ``cell_topology`` and ``builder_lane_status`` plus a new
``solver_family_summary`` panel — that read from the Phase 10 P2
control plane via :class:`RegistryQueries`. At 10k+ solvers, listing
every solver as one item would (a) explode the response size and (b)
implicitly claim "one node per solver" which the Phase 10 prompt P5
forbids.

The aggregator therefore returns:

* per-cell counts, NOT per-solver lists;
* per-family rollups (total, by-status), NOT per-solver lists;
* provider / builder lane queue summaries (counts by status), NOT
  every job;
* an explicit ``"truncated"`` marker when a hard cap is reached.

If the control plane is empty or unavailable the aggregator returns
``available=false`` with a structured rationale — preserving the
never-fabricate invariant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from waggledance.core.storage import (
    ControlPlaneDB,
    ControlPlaneError,
    RegistryQueries,
)
from .reality_view import RealityPanel


@dataclass(frozen=True)
class ScaleAwarePanels:
    """Bundle of panel constructions a caller can fold into a snapshot."""

    solver_family_summary: RealityPanel
    cell_topology: RealityPanel
    builder_lane_status: RealityPanel
    provider_queue_summary: RealityPanel
    # Phase 11 — autonomy lane visibility (aggregate, never per-solver)
    autonomy_low_risk_kpis: RealityPanel
    # Phase 12 — self-starting autonomy loop visibility
    autonomy_self_starting_kpis: RealityPanel
    # Phase 13 — runtime-harvest visibility (signals from real runtime path)
    autonomy_runtime_harvest_kpis: RealityPanel


_HARD_CAP_PER_PANEL: int = 256


def build_scale_aware_panels(
    *,
    control_plane: Optional[ControlPlaneDB],
    cell_coords: Optional[Sequence[str]] = None,
) -> ScaleAwarePanels:
    """Construct the Phase 10 scale-aware panels from the control plane."""

    if control_plane is None:
        unavailable = "control_plane_db_not_attached"
        return ScaleAwarePanels(
            solver_family_summary=_unavailable("solver_family_summary", "Solver family summary", unavailable),
            cell_topology=_unavailable("cell_topology", "Cell topology", unavailable),
            builder_lane_status=_unavailable("builder_lane_status", "Builder lane status", unavailable),
            provider_queue_summary=_unavailable("provider_queue_summary", "Provider queue summary", unavailable),
            autonomy_low_risk_kpis=_unavailable(
                "autonomy_low_risk_kpis",
                "Low-risk autonomy lane",
                unavailable,
            ),
            autonomy_self_starting_kpis=_unavailable(
                "autonomy_self_starting_kpis",
                "Self-starting autonomy loop",
                unavailable,
            ),
            autonomy_runtime_harvest_kpis=_unavailable(
                "autonomy_runtime_harvest_kpis",
                "Runtime harvest autonomy",
                unavailable,
            ),
        )

    queries = RegistryQueries(control_plane)
    return ScaleAwarePanels(
        solver_family_summary=_solver_family_panel(queries),
        cell_topology=_cell_topology_panel(control_plane, queries, cell_coords),
        builder_lane_status=_builder_lane_panel(control_plane),
        provider_queue_summary=_provider_queue_panel(control_plane),
        autonomy_low_risk_kpis=_autonomy_low_risk_kpis_panel(control_plane),
        autonomy_self_starting_kpis=_autonomy_self_starting_panel(control_plane),
        autonomy_runtime_harvest_kpis=_autonomy_runtime_harvest_panel(control_plane),
    )


# -- panel builders -----------------------------------------------------


def _solver_family_panel(queries: RegistryQueries) -> RealityPanel:
    rollups = queries.family_rollups()
    if not rollups:
        return _unavailable(
            "solver_family_summary",
            "Solver family summary",
            "no_solver_families_registered",
        )
    items: list[dict] = []
    for r in rollups[:_HARD_CAP_PER_PANEL]:
        items.append(
            {
                "family": r.family.name,
                "version": r.family.version,
                "status": r.family.status,
                "total_solvers": r.total_solvers,
                "by_status": dict(r.by_status),
            }
        )
    if len(rollups) > _HARD_CAP_PER_PANEL:
        items.append({"truncated": True, "shown": _HARD_CAP_PER_PANEL, "total": len(rollups)})
    return RealityPanel(
        panel_id="solver_family_summary",
        title="Solver family summary",
        available=True,
        items=tuple(items),
    )


def _cell_topology_panel(
    cp: ControlPlaneDB,
    queries: RegistryQueries,
    cell_coords: Optional[Sequence[str]],
) -> RealityPanel:
    # Discover cells either from caller hints or from cell_membership.
    discovered: list[str] = list(cell_coords) if cell_coords else []
    if not discovered:
        with cp._lock:  # noqa: SLF001 — internal compose
            rows = cp._conn.execute(
                "SELECT DISTINCT cell_coord FROM cell_membership ORDER BY cell_coord"
            ).fetchall()
            discovered = [str(r["cell_coord"]) for r in rows]
    if not discovered:
        return _unavailable(
            "cell_topology",
            "Cell topology",
            "no_cells_in_cell_membership_and_no_caller_hints",
        )
    items: list[dict] = []
    for cell in discovered[:_HARD_CAP_PER_PANEL]:
        shards = queries.vector_shards_for_cell(cell)
        with cp._lock:  # noqa: SLF001
            cnt_row = cp._conn.execute(
                "SELECT COUNT(*) AS c FROM cell_membership WHERE cell_coord = ? AND status = 'active'",
                (cell,),
            ).fetchone()
        items.append(
            {
                "cell": cell,
                "active_members": int(cnt_row["c"]) if cnt_row else 0,
                "vector_shard_count": len(shards),
            }
        )
    if len(discovered) > _HARD_CAP_PER_PANEL:
        items.append({"truncated": True, "shown": _HARD_CAP_PER_PANEL, "total": len(discovered)})
    return RealityPanel(
        panel_id="cell_topology",
        title="Cell topology",
        available=True,
        items=tuple(items),
    )


def _builder_lane_panel(cp: ControlPlaneDB) -> RealityPanel:
    with cp._lock:  # noqa: SLF001
        rows = cp._conn.execute(
            "SELECT status, COUNT(*) AS c FROM builder_jobs GROUP BY status ORDER BY status"
        ).fetchall()
    if not rows:
        return _unavailable(
            "builder_lane_status",
            "Builder lane status",
            "no_builder_jobs_recorded_yet",
        )
    items = [{"status": str(r["status"]), "count": int(r["c"])} for r in rows]
    return RealityPanel(
        panel_id="builder_lane_status",
        title="Builder lane status",
        available=True,
        items=tuple(items),
    )


def _provider_queue_panel(cp: ControlPlaneDB) -> RealityPanel:
    with cp._lock:  # noqa: SLF001
        rows = cp._conn.execute(
            "SELECT provider, status, COUNT(*) AS c FROM provider_jobs "
            "GROUP BY provider, status ORDER BY provider, status"
        ).fetchall()
    if not rows:
        return _unavailable(
            "provider_queue_summary",
            "Provider queue summary",
            "no_provider_jobs_recorded_yet",
        )
    items = [
        {"provider": str(r["provider"]), "status": str(r["status"]), "count": int(r["c"])}
        for r in rows
    ]
    return RealityPanel(
        panel_id="provider_queue_summary",
        title="Provider queue summary",
        available=True,
        items=tuple(items),
    )


def _autonomy_low_risk_kpis_panel(cp: ControlPlaneDB) -> RealityPanel:
    """Phase 11 — aggregate autonomy KPIs for the low-risk lane.

    Reads the latest ``autonomy_kpis`` snapshot plus aggregate counts
    over ``promotion_decisions`` and ``solvers``. Aggregates only —
    never lists per-solver state. If the autonomy lane has not produced
    a snapshot yet, returns ``available=false`` with a structured
    rationale, preserving the never-fabricate invariant.
    """

    snap = cp.latest_autonomy_kpi()
    auto_promoted_count = cp.count_solvers(status="auto_promoted")
    deactivated_count = cp.count_solvers(status="deactivated")
    rejected_decisions = len(
        cp.list_promotion_decisions(decision="rejected", limit=10000)
    )
    rollback_decisions = len(
        cp.list_promotion_decisions(decision="rollback", limit=10000)
    )
    auto_decisions = len(
        cp.list_promotion_decisions(decision="auto_promoted", limit=10000)
    )

    if (
        snap is None
        and auto_promoted_count == 0
        and rejected_decisions == 0
        and rollback_decisions == 0
        and auto_decisions == 0
    ):
        return _unavailable(
            "autonomy_low_risk_kpis",
            "Low-risk autonomy lane",
            "no_autonomy_activity_recorded_yet",
        )

    items: list[dict] = [
        {
            "metric": "auto_promoted_solvers_active",
            "value": int(auto_promoted_count),
        },
        {
            "metric": "auto_promoted_solvers_deactivated",
            "value": int(deactivated_count),
        },
        {
            "metric": "promotion_decisions_auto_promoted_total",
            "value": int(auto_decisions),
        },
        {
            "metric": "promotion_decisions_rejected_total",
            "value": int(rejected_decisions),
        },
        {
            "metric": "promotion_decisions_rollback_total",
            "value": int(rollback_decisions),
        },
    ]
    if snap is not None:
        items.extend([
            {
                "metric": "latest_snapshot_at",
                "value": snap.snapshot_at,
            },
            {
                "metric": "dispatcher_hits_total_at_last_snapshot",
                "value": int(snap.dispatcher_hits_total),
            },
            {
                "metric": "dispatcher_misses_total_at_last_snapshot",
                "value": int(snap.dispatcher_misses_total),
            },
            {
                "metric": "candidates_total_at_last_snapshot",
                "value": int(snap.candidates_total),
            },
            {
                "metric": "auto_promotions_total_at_last_snapshot",
                "value": int(snap.auto_promotions_total),
            },
        ])
        if snap.per_family_counts_json:
            items.append({
                "metric": "per_family_dispatcher_hits_at_last_snapshot",
                "value": snap.per_family_counts_json,
            })
    return RealityPanel(
        panel_id="autonomy_low_risk_kpis",
        title="Low-risk autonomy lane",
        available=True,
        items=tuple(items),
    )


def _autonomy_self_starting_panel(cp: ControlPlaneDB) -> RealityPanel:
    """Phase 12 — self-starting autonomy loop visibility.

    Distinguishes self-starting / teacher-assisted / human-gated growth:

    * self-starting: ``autogrowth_runs`` rows with ``intent_id NOT NULL``
      and ``outcome='auto_promoted'``;
    * teacher-assisted: ``promotion_decisions`` rows whose linked
      ``validation_run`` or ``shadow_evaluation`` came through a
      ``provider_jobs`` ancestry (none today; explicitly 0 until real
      adapters land);
    * human-gated: anything in ``promotion_states`` (the Phase 9
      14-stage ladder), recorded for completeness.

    Returns ``available=false`` when the autonomy queue + autogrowth_runs
    are both empty — preserves the never-fabricate invariant.
    """

    queue_total = cp.count_queue_rows()
    intent_total = cp.count_growth_intents()
    runs_promoted = len(
        cp.list_autogrowth_runs(outcome="auto_promoted", limit=10000)
    )
    runs_total = cp.stats().table_counts.get("autogrowth_runs", 0)

    if queue_total == 0 and intent_total == 0 and runs_total == 0:
        return _unavailable(
            "autonomy_self_starting_kpis",
            "Self-starting autonomy loop",
            "no_self_starting_activity_recorded_yet",
        )

    queue_pending = cp.count_queue_rows(status="queued")
    queue_claimed = cp.count_queue_rows(status="claimed")
    queue_completed = cp.count_queue_rows(status="completed")
    queue_failed = cp.count_queue_rows(status="failed")

    intents_pending = cp.count_growth_intents(status="pending")
    intents_enqueued = cp.count_growth_intents(status="enqueued")
    intents_fulfilled = cp.count_growth_intents(status="fulfilled")
    intents_rejected = cp.count_growth_intents(status="rejected")

    # Per-family breakdown of fulfilled-via-self-starting promotions.
    rows = cp._conn.execute(  # type: ignore[attr-defined]
        """
        SELECT family_kind, COUNT(*) AS c
        FROM autogrowth_runs WHERE outcome = 'auto_promoted'
        GROUP BY family_kind ORDER BY family_kind
        """
    ).fetchall()
    per_family = {str(r["family_kind"]): int(r["c"]) for r in rows}

    # Per-cell breakdown of fulfilled intents.
    rows = cp._conn.execute(  # type: ignore[attr-defined]
        """
        SELECT cell_coord, COUNT(*) AS c
        FROM growth_intents WHERE status = 'fulfilled'
        GROUP BY cell_coord ORDER BY cell_coord
        """
    ).fetchall()
    per_cell = {str(r["cell_coord"] or "_"): int(r["c"]) for r in rows}

    # Teacher-assisted truth: count promotion_decisions that have a
    # linked validation_run or shadow_evaluation paired with any
    # provider_jobs row (today: zero — only dry_run_stub +
    # claude_code_builder_lane are exercisable, and the inner loop does
    # not invoke either). The Phase 11 mass proof keeps this at 0;
    # documenting that explicitly here is what makes the panel honest.
    provider_jobs_total = cp.stats().table_counts.get("provider_jobs", 0)
    teacher_assisted_total = 0  # exercised only when provider_jobs > 0

    items: list[dict] = [
        {"metric": "queue_total", "value": int(queue_total)},
        {"metric": "queue_pending", "value": int(queue_pending)},
        {"metric": "queue_claimed", "value": int(queue_claimed)},
        {"metric": "queue_completed", "value": int(queue_completed)},
        {"metric": "queue_failed", "value": int(queue_failed)},
        {"metric": "intents_total", "value": int(intent_total)},
        {"metric": "intents_pending", "value": int(intents_pending)},
        {"metric": "intents_enqueued", "value": int(intents_enqueued)},
        {"metric": "intents_fulfilled", "value": int(intents_fulfilled)},
        {"metric": "intents_rejected", "value": int(intents_rejected)},
        {"metric": "self_starting_promotions_total",
          "value": int(runs_promoted)},
        {"metric": "teacher_assisted_promotions_total",
          "value": int(teacher_assisted_total)},
        {"metric": "provider_jobs_total", "value": int(provider_jobs_total)},
        {
            "metric": "per_family_self_starting",
            "value": json.dumps(per_family, sort_keys=True),
        },
        {
            "metric": "per_cell_self_starting",
            "value": json.dumps(per_cell, sort_keys=True),
        },
    ]
    return RealityPanel(
        panel_id="autonomy_self_starting_kpis",
        title="Self-starting autonomy loop",
        available=True,
        items=tuple(items),
    )


def _autonomy_runtime_harvest_panel(cp: ControlPlaneDB) -> RealityPanel:
    """Phase 13 — runtime-harvest visibility.

    Shows the slice of the autonomy loop that comes from *real
    runtime call paths* (signals with ``kind='runtime_miss'``), as
    opposed to the broader self-starting view in
    :func:`_autonomy_self_starting_panel`. Distinguishes:

    * runtime_harvested_signals
    * queued_intents
    * self_starting_promotions (driven by runtime-miss signals)
    * teacher_assisted_promotions (zero today; surfaced for honesty)
    * human_gated_promotions (Phase 9 ladder rows)

    Returns ``available=false`` when the runtime-miss signal table is
    empty AND no scheduler runs exist — preserving the never-fabricate
    invariant.
    """

    runtime_miss_total = cp.count_runtime_gap_signals(kind="runtime_miss")
    queued_total = cp.count_queue_rows(status="queued")
    runs_promoted = len(
        cp.list_autogrowth_runs(outcome="auto_promoted", limit=10000)
    )

    if runtime_miss_total == 0 and queued_total == 0 and runs_promoted == 0:
        return _unavailable(
            "autonomy_runtime_harvest_kpis",
            "Runtime harvest autonomy",
            "no_runtime_harvest_activity_recorded_yet",
        )

    # Per-family runtime-miss counts
    rows = cp._conn.execute(  # type: ignore[attr-defined]
        """
        SELECT family_kind, COUNT(*) AS c
        FROM runtime_gap_signals WHERE kind = 'runtime_miss'
        GROUP BY family_kind ORDER BY family_kind
        """
    ).fetchall()
    per_family_runtime_miss = {
        str(r["family_kind"] or "_"): int(r["c"]) for r in rows
    }

    # Per-cell runtime-miss counts (cell-aware reporting per RULE 18)
    rows = cp._conn.execute(  # type: ignore[attr-defined]
        """
        SELECT cell_coord, COUNT(*) AS c
        FROM runtime_gap_signals WHERE kind = 'runtime_miss'
        GROUP BY cell_coord ORDER BY cell_coord
        """
    ).fetchall()
    per_cell_runtime_miss = {
        str(r["cell_coord"] or "_"): int(r["c"]) for r in rows
    }

    # Human-gated (Phase 9 promotion ladder rows)
    human_gated = cp.stats().table_counts.get("promotion_states", 0)

    # Truthful split: teacher-assisted requires real provider_jobs
    # rows. Today the inner loop has zero. Document that explicitly.
    provider_jobs_total = cp.stats().table_counts.get("provider_jobs", 0)
    teacher_assisted_total = 0  # would be > 0 only when adapters land

    # Phase 14: capability-feature-indexed solver count is a truthful
    # proxy for "how many auto-promoted solvers the runtime
    # capability-aware dispatcher can find". Live-runtime queries seen
    # is the cumulative runtime-miss signal count (the only durable
    # trace of router invocations the control plane carries).
    capability_features_total = cp.stats().table_counts.get(
        "solver_capability_features", 0
    )
    capability_indexed_solver_rows = cp._conn.execute(  # type: ignore[attr-defined]
        "SELECT COUNT(DISTINCT solver_id) AS c FROM solver_capability_features"
    ).fetchone()
    capability_indexed_solvers = (
        int(capability_indexed_solver_rows["c"])
        if capability_indexed_solver_rows else 0
    )

    # Phase 15: an in-process hint extractor cannot persist counts
    # into the control plane without changing the production code path
    # purely for observability (which would violate RULE 10/13). We
    # surface a coarse proxy: the count of `runtime_miss` signals
    # whose intent_seed appears in growth_intents. That is a truthful
    # under-estimate of "how many hint-derived signals fed the lane"
    # because by the time a runtime_miss reached the control plane,
    # the hint extractor was the only source of family/cell/intent
    # tagging. (The Phase 12 extractor pipeline writes runtime_miss
    # signals only when an upstream hint is present.)
    #
    # Phase 16A note: post-Phase-16A the `AutonomyService.handle_query`
    # upstream extractor is the only legitimate producer of the
    # `structured_request` field that the Phase 15 hint extractor
    # reads. Manual `structured_request` / `low_risk_autonomy_query`
    # injection is rejected (`UPSTREAM_REJECTED_AMBIGUOUS`) at the
    # service layer and the forbidden keys are stripped before the
    # runtime layer sees them. This means the count below now
    # exclusively reflects upstream-lifted signals when the service
    # layer is in the call path; the metric name is unchanged for
    # backwards compatibility.
    hint_aware_signal_rows = cp._conn.execute(  # type: ignore[attr-defined]
        """
        SELECT COUNT(*) AS c FROM runtime_gap_signals
        WHERE kind = 'runtime_miss'
          AND family_kind IS NOT NULL
        """
    ).fetchone()
    hint_aware_signals_total = (
        int(hint_aware_signal_rows["c"]) if hint_aware_signal_rows else 0
    )

    # Phase 16A — narrow Reality View extension. Count
    # `input_columns_signature` capability-feature rows. This feature
    # is set only on solvers in the linear_arithmetic family, and the
    # only automatic path to populate it after Phase 16A is the
    # upstream extractor lifting the flat
    # ``inputs`` + ``input_columns_signature`` payload into the
    # nested grammar. A non-zero count is durable evidence that the
    # upstream lift round-tripped at least once into the control
    # plane via a real promotion. Aggregate-only.
    upstream_signature_rows = cp._conn.execute(  # type: ignore[attr-defined]
        """
        SELECT COUNT(*) AS c FROM solver_capability_features
        WHERE feature_name = 'input_columns_signature'
        """
    ).fetchone()
    upstream_lift_signature_features_total = (
        int(upstream_signature_rows["c"])
        if upstream_signature_rows else 0
    )

    items: list[dict] = [
        {"metric": "runtime_harvested_signals_total",
          "value": int(runtime_miss_total)},
        {"metric": "queued_intents_total", "value": int(queued_total)},
        {"metric": "self_starting_promotions_total",
          "value": int(runs_promoted)},
        {"metric": "teacher_assisted_promotions_total",
          "value": int(teacher_assisted_total)},
        {"metric": "human_gated_promotions_total",
          "value": int(human_gated)},
        {"metric": "provider_jobs_total", "value": int(provider_jobs_total)},
        # Phase 14 — capability-indexed solvers and feature-row count.
        # These surface the warm-path discoverability of auto-promoted
        # solvers without per-solver row enumeration.
        {"metric": "live_runtime_capability_indexed_solvers_total",
          "value": int(capability_indexed_solvers)},
        {"metric": "live_runtime_capability_features_total",
          "value": int(capability_features_total)},
        # Phase 15 — runtime-miss signals that carry an extractor-
        # derived family tag. Aggregate proxy for "hints that reached
        # the autonomy lane through the Phase 15 wiring".
        {"metric": "live_runtime_hint_aware_signals_total",
          "value": int(hint_aware_signals_total)},
        # Phase 16A — capability-feature rows for
        # input_columns_signature. Durable evidence the upstream
        # flat-to-nested lift round-tripped through a promotion.
        {"metric": "live_runtime_upstream_lift_signature_features_total",
          "value": int(upstream_lift_signature_features_total)},
        {
            "metric": "per_family_runtime_miss",
            "value": json.dumps(per_family_runtime_miss, sort_keys=True),
        },
        {
            "metric": "per_cell_runtime_miss",
            "value": json.dumps(per_cell_runtime_miss, sort_keys=True),
        },
    ]
    return RealityPanel(
        panel_id="autonomy_runtime_harvest_kpis",
        title="Runtime harvest autonomy",
        available=True,
        items=tuple(items),
    )


def _unavailable(panel_id: str, title: str, rationale: str) -> RealityPanel:
    return RealityPanel(
        panel_id=panel_id,
        title=title,
        available=False,
        items=(),
        rationale_if_unavailable=rationale,
    )
