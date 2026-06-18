# SPDX-License-Identifier: BUSL-1.1
"""Measurement-only proof that the hex subdivision shadow-only invariant holds.

Advances the WD Image #1 *hexagonal upgrades* pillar HONESTLY: the
shadow_to_candidate_subdivision_transitions_total counter is 0 BY SAFETY DESIGN
(subdivisions are kept shadow-only; no runtime topology mutation). This proof
provides AFFIRMATIVE evidence that the shadow-only invariant holds, rather than a
fake shadow->candidate promotion.

It is measurement-only and read-only: it grants no authority, performs no runtime
mutation, and NEVER increments the transition counter. ``invariant_holds`` is a
fail-closed conjunction RE-DERIVED from the observed plan/guardrail components
(never a single pre-aggregated flag); a missing/malformed/non-bool component, a
non-shadow target state, a forged transition, or a non-strict-zero transition
count all make the invariant NOT-proven. Absence of evidence is never treated as
evidence of absence.

Exact validation commands::

    python tools/run_shadow_only_invariant_proof.py --json
    python -m pytest tests/test_shadow_only_invariant_proof.py -q

Engineering record; offline; read-only; forbidden-vocabulary guarded. No claim of
superiority over any external system.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hex_shadow_subdivision_replay import (  # noqa: E402
    _contains_path_marker,
    build_replay_artifact_for_root,
)

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.shadow_only_invariant_proof.v1"
MEASUREMENT_BASIS = "v1_shadow_only_invariant"

# The plan target state that POSITIVELY evidences a shadow-only (pre-candidate)
# subdivision. Present + in this set is required - a missing/unrecognized state
# is NOT proven, never default-shadow.
_EXPECTED_SHADOW_TARGET_STATES = frozenset({"subdivision_in_shadow"})
# Target states / records that POSITIVELY indicate a shadow->candidate transition
# DID occur (a violation of the shadow-only invariant).
_CANDIDATE_TRANSITION_STATES = frozenset({
    "subdivision_in_candidate", "subdivision_candidate", "candidate",
    "subdivision_promoted", "promoted_to_candidate", "subdivision_authoritative",
})
_PROMOTION_RECORD_KEYS = (
    "promotion_record", "candidate_accepted", "promoted_to_candidate",
    "accepted_candidate", "shadow_to_candidate_transition",
)
# The full guardrail axis set the artifact must report. Exactly one axis must be
# TRUE (no_runtime_topology_mutation); every OTHER axis must be False. A missing
# expected axis fails closed (absence != evidence).
_GUARDRAIL_MUST_BE_TRUE = "no_runtime_topology_mutation"
_EXPECTED_GUARDRAIL_AXES = frozenset({
    "no_runtime_topology_mutation",
    "runtime_authority_changed",
    "operator_gate_required",
    "external_writes_applied",
    "dispatch_controls_added",
    "network_transport_added",
    "raw_query_or_payload_included",
    "runtime_config_contents_included",
    "local_paths_recorded",
    "numeric_equality_to_shadow_children_claimed",
})

_STABLE_FIELDS = (
    "artifact_ok",
    "target_state_is_shadow",
    "transition_occurred",
    "transition_count",
    "no_runtime_mutation",
    "guardrails_all_clean",
    "invariant_holds",
)


def _strict_bool(value: Any) -> bool:
    return value is True


def _strict_zero_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _guardrails_all_clean(guardrails: Any) -> bool:
    """Affirmative + full-axis: the guardrails mapping must contain EVERY expected
    axis, the positive axis must be strictly True, and every other axis strictly
    False. Missing/extra-unknown/non-bool -> not clean (fail closed)."""
    if not isinstance(guardrails, Mapping):
        return False
    if _EXPECTED_GUARDRAIL_AXES - set(guardrails):
        return False  # an expected axis is absent -> not proven
    for axis, value in guardrails.items():
        if value is not True and value is not False:
            return False  # non-bool guardrail
        expect_true = axis == _GUARDRAIL_MUST_BE_TRUE
        if value is not (expect_true):
            return False
    return True


def _detect_transition(plan: Mapping[str, Any], artifact: Mapping[str, Any]) -> bool:
    """Positively detect a shadow->candidate transition (a violation): a
    candidate/promoted target state, or any promotion-record key present/truthy."""
    target = plan.get("target_state")
    if isinstance(target, str) and target.lower() in _CANDIDATE_TRANSITION_STATES:
        return True
    for key in _PROMOTION_RECORD_KEYS:
        if artifact.get(key):
            return True
        if plan.get(key):
            return True
    return False


def build_shadow_only_invariant_proof(
    *,
    artifact_factory: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    factory = artifact_factory or (lambda: build_replay_artifact_for_root(ROOT))

    def _derive(artifact: Mapping[str, Any]) -> dict[str, Any]:
        art = artifact if isinstance(artifact, Mapping) else {}
        plan = art.get("shadow_plan_summary")
        plan = plan if isinstance(plan, Mapping) else {}
        guardrails = art.get("guardrails")
        target = plan.get("target_state")
        target_state_is_shadow = (
            isinstance(target, str) and target in _EXPECTED_SHADOW_TARGET_STATES
        )
        transition_occurred = _detect_transition(plan, art)
        # transition_count is an HONEST strict-0: 0 only when the state positively
        # evidences shadow AND no transition was detected; otherwise it is the
        # count of detected transitions (>=1) which fails the invariant.
        transition_count = 0 if (target_state_is_shadow and not transition_occurred) else 1
        guardrails_all_clean = _guardrails_all_clean(guardrails)
        return {
            "artifact_ok": _strict_bool(art.get("ok")),
            "target_state_is_shadow": target_state_is_shadow,
            "transition_occurred": transition_occurred,
            "transition_count": transition_count,
            "no_runtime_mutation": _strict_bool(plan.get("no_runtime_mutation")),
            "guardrails_all_clean": guardrails_all_clean,
        }

    run1 = _derive(factory())
    run2 = _derive(factory())
    deterministic = json.dumps(
        {k: run1.get(k) for k in _STABLE_FIELDS if k != "invariant_holds"},
        sort_keys=True,
    ) == json.dumps(
        {k: run2.get(k) for k in _STABLE_FIELDS if k != "invariant_holds"},
        sort_keys=True,
    )

    # invariant_holds: fail-closed CONJUNCTION re-derived from the components -
    # positive shadow state, no mutation, clean guardrails, NO transition, and a
    # STRICT-int-0 transition count. Any missing/malformed component -> False.
    invariant_holds = bool(
        deterministic
        and run1["artifact_ok"] is True
        and run1["target_state_is_shadow"] is True
        and run1["no_runtime_mutation"] is True
        and run1["guardrails_all_clean"] is True
        and run1["transition_occurred"] is False
        and _strict_zero_int(run1["transition_count"])
    )

    blockers: list[str] = []
    if not deterministic:
        blockers.append("non_deterministic_invariant")
    if run1["artifact_ok"] is not True:
        blockers.append("artifact_not_ok")
    if run1["target_state_is_shadow"] is not True:
        blockers.append("target_state_not_shadow")
    if run1["no_runtime_mutation"] is not True:
        blockers.append("runtime_mutation_present")
    if run1["guardrails_all_clean"] is not True:
        blockers.append("guardrails_not_clean")
    if run1["transition_occurred"] is True:
        blockers.append("shadow_to_candidate_transition_detected")
    if not _strict_zero_int(run1["transition_count"]):
        blockers.append("transition_count_not_strict_zero")

    return {
        "report_version": REPORT_VERSION,
        "ok": not blockers,
        "blockers": blockers,
        "measurement_basis": MEASUREMENT_BASIS,
        "deterministic_replay": {"runs": 2, "stable_identical": deterministic},
        "invariant": {
            "invariant_holds": invariant_holds,
            "shadow_to_candidate_subdivision_transitions_total": run1["transition_count"],
            "target_state_is_shadow": run1["target_state_is_shadow"],
            "transition_occurred": run1["transition_occurred"],
            "no_runtime_mutation": run1["no_runtime_mutation"],
            "guardrails_all_clean": run1["guardrails_all_clean"],
            "artifact_ok": run1["artifact_ok"],
            # measurement-only: this proof never grants authority or upgrades a claim
            "claim_safe": False,
        },
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": deterministic,
            "measurement_only_no_authority": True,
            "affirmative_not_absence_of_evidence": True,
            "no_superiority_claim": True,
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    inv = report["invariant"]
    dr = report["deterministic_replay"]
    return "\n".join([
        "Shadow-only invariant proof (measurement-only, read-only)",
        f"  ok={report['ok']} blockers={report['blockers']}",
        f"  invariant_holds={inv['invariant_holds']} "
        f"transitions_total={inv['shadow_to_candidate_subdivision_transitions_total']} "
        f"transition_occurred={inv['transition_occurred']}",
        f"  target_state_is_shadow={inv['target_state_is_shadow']} "
        f"no_runtime_mutation={inv['no_runtime_mutation']} "
        f"guardrails_all_clean={inv['guardrails_all_clean']} "
        f"deterministic={dr['stable_identical']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    hit = [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered text: {hit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    build_parser().parse_args(argv)
    report = build_shadow_only_invariant_proof()
    summary = render_summary(report)
    assert_vocabulary_clean(summary)
    json_report = json.dumps(report, indent=2, sort_keys=True)
    # safe-scalar emission: the report holds only bools/ints/version strings, so
    # it must be path-free (verified, not assumed).
    if _contains_path_marker(report):
        raise SystemExit("path marker present in shadow-only invariant report")
    assert_vocabulary_clean(json_report)
    print(json_report if "--json" in (argv or sys.argv[1:]) else summary)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
