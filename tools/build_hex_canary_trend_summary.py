# SPDX-License-Identifier: BUSL-1.1
"""Hex canary mirror trend summary across multiple proof runs (read-only).

The canary chain so far evaluates ONE run at a time: proof runner (#1033)
emits an agreement artifact, the verifier (#1034) re-derives it, the
breakdown (#1035) says which routes diverge. Storyboard panels 4->5
readiness is a question of TREND, not a single snapshot: is the hex mesh
converging toward production routing over successive runs, holding, or
drifting away?

This tool takes several canary mirror proof artifacts (the JSON the proof
runner writes), verifies each one with the merged fail-closed verifier
(#1034) so a forged artifact can never enter the trend, orders them by
generated_at_utc, and reports:

* the agreement-rate series in chronological order;
* first vs last agreement rate, the delta, and a trend direction
  (improving / stable / degrading under --epsilon);
* mean / min / max agreement rate and total samples;
* per-classification first-vs-last counts (which of the four canary
  classifications grew or shrank across the window).

Read-only, offline, deterministic. The artifact carries a re-derivable
``canonical_digest`` and all claim gates false; it is advisory evidence,
never a merge gate or a cutover trigger.

Exit codes: 0 ok, 1 trend degrading below --fail-under (advisory floor),
2 invalid arguments / unverifiable artifact, 3 an input file is missing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from tools.verify_hex_canary_mirror_proof import (  # noqa: E402
    verify_canary_mirror_proof,
)
from tools.run_hex_canary_mirror_proof import (  # noqa: E402
    CLAIM_GATES,
    REPORT_VERSION as PROOF_REPORT_VERSION,
)
from waggledance.core.hex_topology.canary_mirror import (  # noqa: E402
    CANARY_CLASSIFICATIONS,
)

REPORT_VERSION = "wd.v12.hex_canary_trend_summary.v0"
CLAIM_LABEL = "MEASURED_LOCAL_SHADOW_MIRROR_TREND"

TREND_IMPROVING = "improving"
TREND_STABLE = "stable"
TREND_DEGRADING = "degrading"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize the agreement-rate trend across several verified hex "
            "canary mirror proof artifacts (read-only, advisory)."
        ),
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--artifact",
        action="append",
        type=Path,
        default=None,
        help="Path to a canary mirror proof artifact JSON (repeatable).",
    )
    src.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Directory whose *.json files are all proof artifacts.",
    )
    parser.add_argument(
        "--epsilon",
        type=float,
        default=0.01,
        help=(
            "Agreement-rate change within +/- this band counts as stable "
            "(default 0.01)."
        ),
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help=(
            "Advisory floor in [0, 1]: exit 1 when the latest agreement rate "
            "is below this value."
        ),
    )
    parser.add_argument("--out", type=Path, default=None, help="Also write JSON here.")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not (args.epsilon == args.epsilon and 0.0 <= args.epsilon <= 1.0):
        print("--epsilon must be within [0, 1]", file=sys.stderr)
        return 2
    if args.fail_under is not None and not (
        args.fail_under == args.fail_under and 0.0 <= args.fail_under <= 1.0
    ):
        print("--fail-under must be within [0, 1]", file=sys.stderr)
        return 2

    if args.dir is not None:
        if not args.dir.is_dir():
            print(f"directory not found: {args.dir}", file=sys.stderr)
            return 3
        paths = sorted(args.dir.glob("*.json"))
        if not paths:
            print(f"no *.json artifacts in {args.dir}", file=sys.stderr)
            return 2
    else:
        paths = list(args.artifact)
        for path in paths:
            if not path.exists():
                print(f"artifact file not found: {path}", file=sys.stderr)
                return 3

    artifacts: list[dict[str, Any]] = []
    for path in paths:
        try:
            artifacts.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"artifact unreadable ({path}): {exc}", file=sys.stderr)
            return 2

    try:
        summary = build_trend_summary(
            artifacts=artifacts,
            epsilon=float(args.epsilon),
            fail_under=args.fail_under,
        )
    except ValueError as exc:
        print(f"trend summary refused: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(summary, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        _print_summary(summary)
    return 1 if summary["below_fail_under"] else 0


def build_trend_summary(
    *,
    artifacts: Sequence[Mapping[str, Any]],
    epsilon: float = 0.01,
    fail_under: float | None = None,
) -> dict[str, Any]:
    """Verify every artifact, order by timestamp, and compute the trend.

    Raises ValueError on an empty set or any artifact that fails the
    fail-closed verifier (#1034) or lacks a usable generated_at_utc — a
    trend must never be built on unverified or unordered evidence.
    """
    if not artifacts:
        raise ValueError("at least one proof artifact is required")

    points: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        verdict = verify_canary_mirror_proof(artifact)
        if not verdict["verified"]:
            raise ValueError(
                f"artifact[{index}] failed verification: {verdict['findings']}"
            )
        if artifact.get("report_version") != PROOF_REPORT_VERSION:
            raise ValueError(f"artifact[{index}] is not a canary mirror proof")
        generated_at = artifact.get("generated_at_utc")
        if not isinstance(generated_at, str) or not generated_at:
            raise ValueError(f"artifact[{index}] missing generated_at_utc")
        report = artifact["mirror_report"]
        points.append(
            {
                "generated_at_utc": generated_at,
                "input_source": str(artifact.get("input_source", "")),
                "agreement_rate": report["agreement_rate"],
                "agreement_count": report["agreement_count"],
                "divergence_count": report["divergence_count"],
                "sample_count": report["sample_count"],
                "by_classification": dict(report["by_classification"]),
            }
        )

    # Stable chronological order (ties broken by input_source then original index).
    points.sort(key=lambda p: (p["generated_at_utc"], p["input_source"]))

    rates = [p["agreement_rate"] for p in points]
    first_rate, last_rate = rates[0], rates[-1]
    delta = round(last_rate - first_rate, 6)
    if delta > epsilon:
        direction = TREND_IMPROVING
    elif delta < -epsilon:
        direction = TREND_DEGRADING
    else:
        direction = TREND_STABLE

    first_cls = points[0]["by_classification"]
    last_cls = points[-1]["by_classification"]
    classification_shift = {
        key: {
            "first": int(first_cls.get(key, 0)),
            "last": int(last_cls.get(key, 0)),
            "delta": int(last_cls.get(key, 0)) - int(first_cls.get(key, 0)),
        }
        for key in CANARY_CLASSIFICATIONS
    }

    below = fail_under is not None and last_rate < fail_under
    core: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "claim_label": CLAIM_LABEL,
        "advisory_only": True,
        "read_only": True,
        "run_count": len(points),
        "epsilon": epsilon,
        "fail_under": fail_under,
        "agreement_rate_series": rates,
        "first_agreement_rate": first_rate,
        "last_agreement_rate": last_rate,
        "agreement_rate_delta": delta,
        "trend_direction": direction,
        "mean_agreement_rate": round(sum(rates) / len(rates), 6),
        "min_agreement_rate": min(rates),
        "max_agreement_rate": max(rates),
        "total_samples": sum(p["sample_count"] for p in points),
        "classification_shift": classification_shift,
        "points": points,
        "below_fail_under": bool(below),
    }
    for gate in CLAIM_GATES:
        core[gate] = False
    return {**core, "canonical_digest": sha256_digest(core)}


def _print_summary(summary: Mapping[str, Any]) -> None:
    print(
        f"hex canary trend ({summary['claim_label']}, read-only): "
        f"{summary['run_count']} runs, {summary['trend_direction']}"
    )
    print(
        f"  agreement {summary['first_agreement_rate']} -> "
        f"{summary['last_agreement_rate']} "
        f"(delta {summary['agreement_rate_delta']}, "
        f"mean {summary['mean_agreement_rate']})"
    )
    for key, shift in summary["classification_shift"].items():
        if shift["delta"]:
            sign = "+" if shift["delta"] > 0 else ""
            print(f"    {key}: {shift['first']} -> {shift['last']} ({sign}{shift['delta']})")
    if summary["fail_under"] is not None:
        verdict = "BELOW FLOOR" if summary["below_fail_under"] else "ok"
        print(f"  floor {summary['fail_under']}: {verdict}")


if __name__ == "__main__":
    raise SystemExit(main())
