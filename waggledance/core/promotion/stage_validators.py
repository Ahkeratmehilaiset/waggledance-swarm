# SPDX-License-Identifier: BUSL-1.1
"""Stage validators — Phase 9 §M.

Pure deterministic validators per stage transition. Each validator
checks a named criterion; the ladder runs ALL required criteria for
the target stage and refuses the transition unless all pass.
"""
from __future__ import annotations

from typing import Callable

from . import STAGES


# ── Validator registry ────────────────────────────────────────────

_VALIDATORS: dict[str, Callable[[dict], tuple[bool, str]]] = {}


def register(name: str):
    def deco(fn: Callable[[dict], tuple[bool, str]]) -> Callable:
        _VALIDATORS[name] = fn
        return fn
    return deco


def run_criterion(name: str, ctx: dict) -> tuple[bool, str]:
    fn = _VALIDATORS.get(name)
    if fn is None:
        return False, f"unknown criterion: {name!r}"
    try:
        return fn(ctx)
    except Exception as exc:   # noqa: BLE001
        return False, f"validator {name!r} raised: {exc}"


def run_all(criteria: tuple[str, ...], ctx: dict
              ) -> tuple[list[str], list[str]]:
    """Return (satisfied, failed) lists in order of input criteria."""
    satisfied: list[str] = []
    failed: list[str] = []
    for c in criteria:
        ok, _ = run_criterion(c, ctx)
        if ok:
            satisfied.append(c)
        else:
            failed.append(c)
    return satisfied, failed


# ── Concrete validators ───────────────────────────────────────────

@register("from_stage_is_curiosity")
def _from_curiosity(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "curiosity"
    return ok, "" if ok else "from_stage must be curiosity"


@register("from_stage_is_tension")
def _from_tension(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "tension"
    return ok, "" if ok else "from_stage must be tension"


@register("from_stage_is_dream_target")
def _from_dream_target(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "dream_target"
    return ok, "" if ok else "from_stage must be dream_target"


@register("from_stage_is_stochastic")
def _from_stochastic(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "stochastic_external_proposal"
    return ok, "" if ok else "from_stage must be stochastic_external_proposal"


@register("from_stage_is_deterministic_collapse")
def _from_collapse(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "deterministic_collapse"
    return ok, "" if ok else "from_stage must be deterministic_collapse"


@register("from_stage_is_shadow_graph")
def _from_shadow_graph(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "shadow_graph"
    return ok, "" if ok else "from_stage must be shadow_graph"


@register("from_stage_is_replay")
def _from_replay(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "replay"
    return ok, "" if ok else "from_stage must be replay"


@register("from_stage_is_meta_proposal")
def _from_meta_proposal(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "meta_proposal"
    return ok, "" if ok else "from_stage must be meta_proposal"


@register("from_stage_is_human_review")
def _from_human_review(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "human_review"
    return ok, "" if ok else "from_stage must be human_review"


@register("from_stage_is_post_campaign_runtime_candidate")
def _from_post_campaign(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "post_campaign_runtime_candidate"
    return ok, "" if ok else (
        "from_stage must be post_campaign_runtime_candidate"
    )


@register("from_stage_is_canary_cell")
def _from_canary(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "canary_cell"
    return ok, "" if ok else "from_stage must be canary_cell"


@register("from_stage_is_limited_runtime")
def _from_limited(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("from_stage") == "limited_runtime"
    return ok, "" if ok else "from_stage must be limited_runtime"


@register("tension_resolution_path_deferred_to_dream")
def _resolution_deferred(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("tension_resolution_path") == "deferred_to_dream"
    return ok, "" if ok else (
        "tension.resolution_path must be deferred_to_dream"
    )


@register("passes_proposal_gate")
def _passes_gate(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("collapse_verdict") == "ACCEPT_CANDIDATE"
    return ok, "" if ok else "collapse_verdict must be ACCEPT_CANDIDATE"


@register("shadow_only_admit")
def _shadow_only(ctx: dict) -> tuple[bool, str]:
    ok = bool(ctx.get("shadow_only", False))
    return ok, "" if ok else "shadow_only must be True"


@register("replay_methodology_acknowledged")
def _replay_methodology(ctx: dict) -> tuple[bool, str]:
    ok = ctx.get("replay_methodology") == "structural_proxy_v0.1"
    return ok, "" if ok else (
        "replay_methodology must be structural_proxy_v0.1"
    )


@register("structurally_promising")
def _structurally_promising(ctx: dict) -> tuple[bool, str]:
    ok = bool(ctx.get("structurally_promising", False))
    return ok, "" if ok else "structurally_promising must be True"


@register("human_approval_id_present")
def _human_approval(ctx: dict) -> tuple[bool, str]:
    ok = bool(ctx.get("human_approval_id"))
    return ok, "" if ok else "human_approval_id must be present"


@register("campaign_finished_or_frozen")
def _campaign_finished(ctx: dict) -> tuple[bool, str]:
    ok = bool(ctx.get("campaign_finished_or_frozen", False))
    return ok, "" if ok else (
        "campaign_finished_or_frozen must be True"
    )


@register("canary_observation_window_passed")
def _canary_window(ctx: dict) -> tuple[bool, str]:
    ok = bool(ctx.get("canary_observation_window_passed", False))
    return ok, "" if ok else "canary observation window not yet passed"


@register("limited_runtime_observation_window_passed")
def _limited_window(ctx: dict) -> tuple[bool, str]:
    ok = bool(ctx.get("limited_runtime_observation_window_passed",
                       False))
    return ok, "" if ok else (
        "limited_runtime observation window not yet passed"
    )


@register("no_critical_regressions")
def _no_regressions(ctx: dict) -> tuple[bool, str]:
    ok = int(ctx.get("critical_regressions", 0)) == 0
    return ok, "" if ok else (
        f"critical_regressions={ctx.get('critical_regressions')} must be 0"
    )
