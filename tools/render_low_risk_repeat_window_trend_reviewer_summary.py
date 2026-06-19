# SPDX-License-Identifier: BUSL-1.1
"""Path-free reviewer summary renderer for the low-risk repeat-window trend.

Advances the WD Image #1 *low-risk autonomy loop* pillar. PR #1281 wired the
repeat-window trend proof into the capability manifest as DEFAULT-ON measurement-only
evidence (``low_risk_autonomy_proof["repeat_window_trend"]``, a curated content-safe
aggregate). This tool renders that aggregate into a path-free reviewer summary -
WITHOUT appending it anywhere.

It is READ-ONLY and grants no authority. Content-safe by construction: it emits ONLY
DERIVED booleans and STRICT ints (plus the fixed version string), never a raw blocker
string, status value, identifier, path, query, or payload. ``trend_review_clean`` is
RE-DERIVED fail-closed from the aggregate's COMPONENT fields - it NEVER trusts the
aggregate's own ``ok`` flag (an inconsistent aggregate with a component degraded but
``ok`` True must fail closed). A stable 100% trend NEVER upgrades the low-risk claim:
``claim_safe`` is hardcoded False and no authority/guardrail field can flip it.
``path_free_verified`` is DERIVED by scanning the rendered output with the chain's own
``_contains_path_marker`` (never hardcoded).

Exact validation commands::

    python tools/render_low_risk_repeat_window_trend_reviewer_summary.py --json
    python -m pytest tests/test_render_low_risk_repeat_window_trend_reviewer_summary.py -q

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

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.low_risk_repeat_window_trend_reviewer_summary.v1"
# The repeat-window trend window must be a strict int in this inclusive range (mirrors
# the proof's [2, MAX_WINDOW] bound; a window outside it is not a valid measurement).
_MIN_WINDOW = 2
_MAX_WINDOW = 25

# The ONLY keys the summary may contain - all derived booleans / strict ints (plus the
# fixed version string). Enforced so a future edit cannot widen the surface to a raw
# field.
_SUMMARY_SAFE_KEYS = frozenset({
    "report_version",
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
    "window_size",
    "promoted_solver_count_min",
    "promoted_solver_count_max",
    "trend_review_clean",
    "path_free_verified",
    "claim_safe",
})


def _strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _strict_int_or_none(value: Any) -> int | None:
    return value if _strict_int(value) else None


def render_repeat_window_trend_reviewer_summary(trend: Any) -> dict[str, Any]:
    """Render a path-free reviewer summary from a repeat-window trend aggregate.

    Emits only DERIVED booleans / strict ints. A non-mapping / absent / degraded
    aggregate fails closed (trend_review_clean False). NEVER trusts the aggregate's own
    ``ok``; re-derives from components.
    """
    t: Mapping[str, Any] = trend if isinstance(trend, Mapping) else {}
    trend_present = isinstance(trend, Mapping) and bool(trend)

    all_runs_ok = t.get("all_runs_ok") is True
    deterministic = t.get("deterministic") is True
    promotion_count_stable = t.get("promoted_solver_count_stable") is True
    evidence_present = t.get("evidence_present") is True
    # Authority/side-effect axes must be strictly False (a True is a measurement that
    # crossed the boundary - never clean).
    no_guardrail_tripped = t.get("any_guardrail_tripped") is False
    no_runtime_authority_granted = t.get("trend_runtime_authority_granted") is False
    no_external_writes = t.get("trend_external_writes_applied") is False
    # window_size must be a strict int in [_MIN_WINDOW, _MAX_WINDOW].
    _window = t.get("window_size")
    window_size_valid = _strict_int(_window) and _MIN_WINDOW <= _window <= _MAX_WINDOW
    # promotion count must be a strict positive int (a 0-count is no evidence).
    _count_min = t.get("promoted_solver_count_min")
    promotion_count_positive = _strict_int(_count_min) and _count_min >= 1

    summary: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "trend_present": trend_present,
        "all_runs_ok": all_runs_ok,
        "deterministic": deterministic,
        "promotion_count_stable": promotion_count_stable,
        "evidence_present": evidence_present,
        "no_guardrail_tripped": no_guardrail_tripped,
        "no_runtime_authority_granted": no_runtime_authority_granted,
        "no_external_writes": no_external_writes,
        "window_size_valid": window_size_valid,
        "promotion_count_positive": promotion_count_positive,
        # Informational strict ints (None when not a strict int) - never gate on the
        # raw values, surface them for the reviewer.
        "window_size": _strict_int_or_none(_window),
        "promoted_solver_count_min": _strict_int_or_none(_count_min),
        "promoted_solver_count_max": _strict_int_or_none(
            t.get("promoted_solver_count_max")
        ),
        # RE-DERIVED fail-closed from the components above; NEVER the aggregate's own ok.
        "trend_review_clean": bool(
            trend_present
            and all_runs_ok
            and deterministic
            and promotion_count_stable
            and evidence_present
            and no_guardrail_tripped
            and no_runtime_authority_granted
            and no_external_writes
            and window_size_valid
            and promotion_count_positive
        ),
        # measurement-only: a stable trend NEVER upgrades the low-risk claim.
        "claim_safe": False,
    }
    # DERIVE path_free_verified by scanning the rendered output (never hardcoded). By
    # construction the summary holds only booleans/ints/the version string, so this is
    # True - but it is verified, so a future raw field would flip it.
    summary["path_free_verified"] = not _contains_path_marker(summary)

    extra = set(summary) - _SUMMARY_SAFE_KEYS
    if extra:
        raise ValueError(
            f"repeat-window trend reviewer summary has non-allowlisted keys: {sorted(extra)}"
        )
    return summary


def render_summary(summary: Mapping[str, Any]) -> str:
    return "\n".join([
        "Low-risk repeat-window trend reviewer summary (path-free, read-only)",
        f"  trend_review_clean={summary['trend_review_clean']} "
        f"trend_present={summary['trend_present']} "
        f"path_free_verified={summary['path_free_verified']}",
        f"  all_runs_ok={summary['all_runs_ok']} deterministic={summary['deterministic']} "
        f"promotion_count_stable={summary['promotion_count_stable']} "
        f"evidence_present={summary['evidence_present']}",
        f"  no_guardrail_tripped={summary['no_guardrail_tripped']} "
        f"no_runtime_authority_granted={summary['no_runtime_authority_granted']} "
        f"no_external_writes={summary['no_external_writes']} "
        f"window_size_valid={summary['window_size_valid']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    hit = [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered text: {hit}")


def _build_live_trend() -> dict[str, Any]:
    """Read the real repeat-window trend aggregate from the capability manifest
    (read-only; reuses the manifest's own default-on wiring)."""
    from tools.wd_image1_capability_manifest import build_manifest  # noqa: PLC0415

    manifest = build_manifest(ROOT)
    for capability in manifest.get("capabilities", []):
        if capability.get("capability_id") == "low_risk_autonomy_loop":
            proof = capability.get("proof")
            proof = proof if isinstance(proof, Mapping) else {}
            trend = proof.get("repeat_window_trend")
            return dict(trend) if isinstance(trend, Mapping) else {}
    raise SystemExit("low_risk_autonomy_loop proof not found in manifest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = render_repeat_window_trend_reviewer_summary(_build_live_trend())
    text = render_summary(summary)
    assert_vocabulary_clean(text)
    json_report = json.dumps(summary, indent=2, sort_keys=True)
    # safe-scalar emission: the summary holds only bools/ints/the version string, so it
    # must be path-free (verified, not assumed).
    if _contains_path_marker(summary):
        raise SystemExit("path marker present in repeat-window trend reviewer summary")
    assert_vocabulary_clean(json_report)
    print(json_report if args.json else text)
    return 0 if summary["trend_review_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
