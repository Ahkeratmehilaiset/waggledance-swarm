# SPDX-License-Identifier: BUSL-1.1
"""Path-free, measurement-only cross-consistency digest for the low-risk views.

Advances the WD Image #1 *low-risk autonomy loop* pillar. Three measurement-only
views of the same low-risk autogrowth loop are already wired into
``low_risk_autonomy_proof``:

* ``real_loop_dry_run``                    - the local autogrowth-chain dry-run proof
* ``repeat_window_trend``                  - the default-on repeat-window trend aggregate (#1281)
* ``repeat_window_trend_reviewer_summary`` - the path-free reviewer summary of that trend (#1284/#1286)

This digest confirms those three views are MUTUALLY CONSISTENT and surfaces a single
derived ``cross_consistent`` boolean. Per the STRICT semantics mirrored from the hex
cross-consistency digest (#1278), ``cross_consistent`` is True ONLY when all three
views are present AND each re-derived clean from its OWN components (never the view's
own ``ok`` / ``trend_review_clean`` aggregate - #1274) AND the reviewer summary is a
FAITHFUL view of the trend (its surfaced window/count values match the trend's). Any
view absent, malformed, component-degraded, or in contradiction drives it False
(fail-closed).

The reviewer summary is DERIVED from the trend (unlike the hex digest's independent
views), so a conjunction of per-view clean verdicts is not sufficient: a forged
reviewer summary and trend can each look clean while disagreeing on the underlying
window/count. ``reviewer_matches_trend`` closes that gap by requiring the reviewer's
surfaced strict-int window/min/max to equal the trend's.

It is READ-ONLY and grants no authority: content-safe by construction (emits ONLY
derived booleans via a closed ``_DIGEST_SAFE_KEYS`` allowlist, raising on any
non-allowlisted key), ``path_free_verified`` DERIVED via the chain's own
``_contains_path_marker`` (never hardcoded), ``claim_safe`` hardcoded False. It never
appends to the bridge, includes payloads/paths, transports, mutates runtime, grants
runtime/scheduler authority, upgrades any claim, or flips the low-risk proof ``ok``;
it is NOT folded into any proof ``ok``.

Exact validation commands::

    python tools/run_low_risk_cross_consistency_digest.py --json
    python -m pytest tests/test_run_low_risk_cross_consistency_digest.py -q

Engineering record; offline; read-only; forbidden-vocabulary guarded. No claim of
superiority over any external system.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hex_shadow_subdivision_replay import (  # noqa: E402
    _contains_path_marker,
)
from tools.render_low_risk_repeat_window_trend_reviewer_summary import (  # noqa: E402
    _MAX_WINDOW,
    _MIN_WINDOW,
)

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.low_risk_cross_consistency_digest.v1"

# The ONLY keys the digest may contain - all derived booleans (plus the fixed
# version string), never raw content. Enforced so a future edit cannot widen the
# surface to a raw field.
_DIGEST_SAFE_KEYS = frozenset({
    "report_version",
    "real_loop_present",
    "repeat_window_trend_present",
    "reviewer_summary_present",
    "all_views_present",
    "real_loop_clean",
    "trend_clean",
    "reviewer_clean",
    "reviewer_matches_trend",
    "cross_consistent",
    "path_free_verified",
    "claim_safe",
})

# repeat_window_trend (#1281) component fields. The positive measurement axes must be
# strictly True; the side-effect/authority axes strictly False.
_TREND_TRUE_FIELDS = (
    "all_runs_ok",
    "deterministic",
    "promoted_solver_count_stable",
    "evidence_present",
)
_TREND_FALSE_FIELDS = (
    "any_guardrail_tripped",
    "trend_runtime_authority_granted",
    "trend_external_writes_applied",
)
# real_loop_dry_run authority_boundary axes - ALL must be strictly False (no runtime /
# control-plane / scheduler / provider / builder / gate-skip authority crossed). The
# full set is required present-and-False, and any EXTRA boundary key that is not False
# also fails closed (a forged authority axis cannot ride in - #1271-style).
_REAL_LOOP_BOUNDARY_FALSE_FIELDS = (
    "external_writes_applied",
    "production_control_plane_touched",
    "production_scheduler_enqueue",
    "provider_jobs_created",
    "builder_jobs_created",
    "gate_skip_authority",
    "operator_gate_bypassed",
    "runtime_authority_granted",
    "fast_track_priority",
)
# reviewer_summary (#1284/#1286) component booleans that must each be strictly True
# (its OWN trend_review_clean aggregate is deliberately NOT consulted - #1274).
_REVIEWER_TRUE_FIELDS = (
    "trend_present",
    "all_runs_ok",
    "deterministic",
    "promotion_count_stable",
    "evidence_present",
    "no_guardrail_tripped",
    "no_runtime_authority_granted",
    "no_external_writes",
    "window_size_valid",
    "promotion_count_positive",
)


def _strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _window_count_valid(window: Any, count_min: Any, count_max: Any) -> bool:
    """Strict numeric validity shared by the trend and reviewer views: window a strict
    int in [_MIN_WINDOW, _MAX_WINDOW], min/max strict ints with 1 <= min <= max."""
    return bool(
        _strict_int(window)
        and _MIN_WINDOW <= window <= _MAX_WINDOW
        and _strict_int(count_min)
        and _strict_int(count_max)
        and 1 <= count_min <= count_max
    )


def _real_loop_clean(view: Any) -> bool:
    """Re-derive the real-loop dry-run clean verdict from its COMPONENTS (mirrors the
    manifest's own low-risk ok gate): ok True, no local artifacts written, and the
    runtime-authority / external-writes boundary strictly False. Absent/malformed ->
    False."""
    if not isinstance(view, Mapping):
        return False
    # refuse-to-certify if the measurement-only view self-declares claim_safe.
    if view.get("claim_safe") is True:
        return False
    if view.get("ok") is not True or view.get("local_artifacts_written") is not False:
        return False
    boundary = view.get("authority_boundary")
    if not isinstance(boundary, Mapping):
        return False
    # every expected authority axis present-and-False, AND no extra boundary key that
    # is anything other than False (a forged authority axis fails closed).
    if not all(boundary.get(field) is False for field in _REAL_LOOP_BOUNDARY_FALSE_FIELDS):
        return False
    return all(value is False for value in boundary.values())


def _trend_clean(view: Any) -> bool:
    """Re-derive the repeat-window trend clean verdict from its COMPONENTS (never an
    aggregate ``ok``): the positive axes True, side-effect/authority axes False, and a
    strict-int window in range with strict-int 1 <= min <= max. Absent/malformed ->
    False."""
    if not isinstance(view, Mapping):
        return False
    # refuse-to-certify if the measurement-only view self-declares claim_safe.
    if view.get("claim_safe") is True:
        return False
    if not _window_count_valid(
        view.get("window_size"),
        view.get("promoted_solver_count_min"),
        view.get("promoted_solver_count_max"),
    ):
        return False
    if not all(view.get(field) is True for field in _TREND_TRUE_FIELDS):
        return False
    return all(view.get(field) is False for field in _TREND_FALSE_FIELDS)


def _reviewer_clean(view: Any) -> bool:
    """Re-derive the reviewer summary clean verdict from its COMPONENTS (never its own
    ``trend_review_clean`` aggregate - #1274): every component bool strictly True, the
    summary must NOT self-declare ``claim_safe``, and a strict-int window/count.
    Absent/malformed -> False."""
    if not isinstance(view, Mapping):
        return False
    if view.get("claim_safe") is True:
        return False
    if not _window_count_valid(
        view.get("window_size"),
        view.get("promoted_solver_count_min"),
        view.get("promoted_solver_count_max"),
    ):
        return False
    return all(view.get(field) is True for field in _REVIEWER_TRUE_FIELDS)


def _reviewer_matches_trend(reviewer: Any, trend: Any) -> bool:
    """The reviewer summary must be a FAITHFUL view of the trend: its surfaced
    strict-int window/min/max equal the trend's. Catches a forge where the reviewer and
    trend each look clean but describe different underlying measurements. Both must be
    strict ints (a None/non-strict on either side fails closed)."""
    if not isinstance(reviewer, Mapping) or not isinstance(trend, Mapping):
        return False
    for field in ("window_size", "promoted_solver_count_min", "promoted_solver_count_max"):
        r_val = reviewer.get(field)
        t_val = trend.get(field)
        if not _strict_int(r_val) or not _strict_int(t_val) or r_val != t_val:
            return False
    return True


def build_low_risk_cross_consistency_digest(proof: Any) -> dict[str, Any]:
    """Build the path-free cross-consistency digest from a low_risk_autonomy_loop proof.

    Emits only DERIVED booleans. cross_consistent is True ONLY when all three views are
    present, each re-derived clean from its own components, AND the reviewer summary
    faithfully matches the trend; any absence/malformation/degradation/contradiction
    fails closed.
    """
    p: Mapping[str, Any] = proof if isinstance(proof, Mapping) else {}
    real_loop = p.get("real_loop_dry_run")
    trend = p.get("repeat_window_trend")
    reviewer = p.get("repeat_window_trend_reviewer_summary")

    real_loop_present = isinstance(real_loop, Mapping)
    trend_present = isinstance(trend, Mapping)
    reviewer_present = isinstance(reviewer, Mapping)
    all_present = real_loop_present and trend_present and reviewer_present

    real_loop_clean = _real_loop_clean(real_loop)
    trend_clean = _trend_clean(trend)
    reviewer_clean = _reviewer_clean(reviewer)
    reviewer_matches_trend = _reviewer_matches_trend(reviewer, trend)

    # STRICT: consistent ONLY when every view is present, each re-derived clean from its
    # own components (so the views AGREE on cleanliness - a view claiming clean while a
    # sibling is not-clean cannot pass), AND the reviewer is a faithful view of the
    # trend (matching window/count). all-not-clean/degraded agreement surfaces via the
    # component flags but is NOT a clean cross-check, so cross_consistent stays False.
    cross_consistent = bool(
        all_present
        and real_loop_clean
        and trend_clean
        and reviewer_clean
        and reviewer_matches_trend
    )

    digest: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "real_loop_present": real_loop_present,
        "repeat_window_trend_present": trend_present,
        "reviewer_summary_present": reviewer_present,
        "all_views_present": all_present,
        "real_loop_clean": real_loop_clean,
        "trend_clean": trend_clean,
        "reviewer_clean": reviewer_clean,
        "reviewer_matches_trend": reviewer_matches_trend,
        "cross_consistent": cross_consistent,
        # measurement-only: this digest never grants authority or upgrades a claim.
        "claim_safe": False,
    }
    # DERIVE path_free_verified by scanning the rendered output (never hardcoded). By
    # construction the digest holds only booleans + the fixed version string, so this is
    # True - but it is verified, so a future raw field would flip it.
    digest["path_free_verified"] = not _contains_path_marker(digest)

    extra = set(digest) - _DIGEST_SAFE_KEYS
    if extra:
        raise ValueError(
            f"low-risk cross-consistency digest has non-allowlisted keys: {sorted(extra)}"
        )
    return digest


def render_summary(digest: Mapping[str, Any]) -> str:
    return "\n".join([
        "Low-risk cross-consistency digest (path-free, read-only, measurement-only)",
        f"  cross_consistent={digest['cross_consistent']} "
        f"all_views_present={digest['all_views_present']} "
        f"path_free_verified={digest['path_free_verified']}",
        f"  real_loop_clean={digest['real_loop_clean']} "
        f"trend_clean={digest['trend_clean']} "
        f"reviewer_clean={digest['reviewer_clean']} "
        f"reviewer_matches_trend={digest['reviewer_matches_trend']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    hit = [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered text: {hit}")


def _build_live_proof() -> dict[str, Any]:
    """Read the real low_risk_autonomy_loop proof from the capability manifest
    (read-only; reuses the manifest's own construction as single source of truth)."""
    from tools.wd_image1_capability_manifest import build_manifest  # noqa: PLC0415

    manifest = build_manifest(ROOT)
    for capability in manifest.get("capabilities", []):
        if capability.get("capability_id") == "low_risk_autonomy_loop":
            proof = capability.get("proof")
            if isinstance(proof, Mapping):
                return dict(proof)
    raise SystemExit("low_risk_autonomy_loop proof not found in manifest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    digest = build_low_risk_cross_consistency_digest(_build_live_proof())
    text = render_summary(digest)
    assert_vocabulary_clean(text)
    json_report = json.dumps(digest, indent=2, sort_keys=True)
    # safe-scalar emission: the digest holds only bools + the version string, so it must
    # be path-free (verified, not assumed).
    if _contains_path_marker(digest):
        raise SystemExit("path marker present in cross-consistency digest")
    assert_vocabulary_clean(json_report)
    print(json_report if args.json else text)
    return 0 if digest["cross_consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
