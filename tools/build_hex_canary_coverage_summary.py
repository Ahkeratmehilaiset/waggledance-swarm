# SPDX-License-Identifier: BUSL-1.1
"""Hex canary mirror coverage summary with next-targets (read-only).

The other V12 ingredients (counterfactual eval, adversarial corpus) each
have a coverage summary that says, in one read, how mature the evidence
is and what is still missing. The hex canary capability
(#1027 core -> #1033 proof -> #1034 verifier -> #1035 breakdown ->
#1043 trend) had the pieces but no roll-up. This is that roll-up.

Given one verified canary mirror proof artifact (or the built-in demo
run), it reports:

* classification coverage — which of the four canary classifications the
  run actually exercised, and whether all four are present;
* agreement / divergence counts and rate, sample count;
* mesh-cell and mesh-method spread (how many distinct cells / methods the
  run touched);
* a deterministic list of next coverage targets, e.g. "exercise all four
  classifications", "add a multi-intent corpus beyond the 4-case demo",
  "collect multiple runs for a trend" — each emitted only when the
  evidence does not already satisfy it.

The source artifact is re-derived with the merged fail-closed verifier
(#1034) before it is summarized, so a forged proof cannot inflate the
coverage view. Read-only, offline, deterministic. Output carries a
re-derivable canonical_digest and all claim gates false; advisory only,
never a merge gate or a cutover trigger.

Exit codes: 0 ok, 2 invalid arguments / unverifiable artifact,
3 artifact file missing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.hex_topology.canary_mirror import (  # noqa: E402
    CANARY_CLASSIFICATIONS,
)
from tools.run_hex_canary_mirror_proof import (  # noqa: E402
    CLAIM_GATES,
    REPORT_VERSION as PROOF_REPORT_VERSION,
    build_canary_mirror_proof,
    DEMO_DECISIONS,
)
from tools.verify_hex_canary_mirror_proof import (  # noqa: E402
    verify_canary_mirror_proof,
)

REPORT_VERSION = "wd.v12.hex_canary_coverage_summary.v0"
CLAIM_LABEL = "MEASURED_LOCAL_SHADOW_MIRROR_COVERAGE"
# Below this many distinct intents the run is treated as a thin demo corpus.
MIN_REPRESENTATIVE_INTENTS = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Roll a verified hex canary mirror proof up into an "
            "ingredient-style coverage summary with next-targets "
            "(read-only, advisory)."
        ),
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help="Path to a canary mirror proof artifact JSON.",
    )
    src.add_argument(
        "--demo",
        action="store_true",
        help="Summarize a fresh built-in demo proof run.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-8601 UTC override for generated_at_utc (deterministic).",
    )
    parser.add_argument("--out", type=Path, default=None, help="Also write JSON here.")
    parser.add_argument("--json", action="store_true", help="Emit JSON to stdout.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.now is not None:
        now = _parse_utc(args.now)
        if now is None:
            print(f"--now is not a valid ISO-8601 instant: {args.now!r}", file=sys.stderr)
            return 2
    else:
        now = datetime.now(timezone.utc)

    if args.demo:
        artifact = build_canary_mirror_proof(
            decisions=[dict(d) for d in DEMO_DECISIONS],
            source_label="demo",
            now=now,
        )
        intent_count = len({str(d["intent"]) for d in DEMO_DECISIONS})
    else:
        if not args.artifact.exists():
            print(f"artifact file not found: {args.artifact}", file=sys.stderr)
            return 3
        try:
            artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"artifact unreadable: {exc}", file=sys.stderr)
            return 2
        # The artifact does not carry per-intent data; intent breadth is only
        # known for the demo run. Treat a file artifact as intent-unknown.
        intent_count = None

    try:
        summary = build_coverage_summary(
            artifact=artifact, now=now, intent_count=intent_count
        )
    except ValueError as exc:
        print(f"coverage summary refused: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(summary, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        _print_summary(summary)
    return 0


def build_coverage_summary(
    *,
    artifact: Any,
    now: datetime,
    intent_count: int | None = None,
) -> dict[str, Any]:
    """Verify the proof artifact and roll it up into a coverage view.

    Raises ValueError if the artifact fails the fail-closed verifier or is
    not a canary mirror proof — coverage must never be reported off
    unverified evidence.
    """
    verdict = verify_canary_mirror_proof(artifact)
    if not verdict["verified"]:
        raise ValueError(f"artifact failed verification: {verdict['findings']}")
    if artifact.get("report_version") != PROOF_REPORT_VERSION:
        raise ValueError("artifact is not a canary mirror proof")

    report = artifact["mirror_report"]
    by_classification = dict(report["by_classification"])
    exercised = sorted(k for k, v in by_classification.items() if v > 0)
    missing = sorted(set(CANARY_CLASSIFICATIONS) - set(exercised))
    sample_count = int(report["sample_count"])
    distinct_cells = sum(1 for v in report["by_mesh_cell"].values() if v > 0)
    distinct_methods = sum(1 for v in report["by_mesh_method"].values() if v > 0)

    coverage = {
        "sample_count": sample_count,
        "agreement_count": int(report["agreement_count"]),
        "divergence_count": int(report["divergence_count"]),
        "agreement_rate": report["agreement_rate"],
        "classifications_total": len(CANARY_CLASSIFICATIONS),
        "classifications_exercised": exercised,
        "classifications_exercised_count": len(exercised),
        "classifications_missing": missing,
        "all_classifications_exercised": not missing,
        "distinct_mesh_cells": distinct_cells,
        "distinct_mesh_methods": distinct_methods,
        "intent_count": intent_count,
    }

    core: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "claim_label": CLAIM_LABEL,
        "advisory_only": True,
        "read_only": True,
        "generated_at_utc": _fmt(now),
        "source_proof_version": str(artifact.get("report_version", "")),
        "source_input": str(artifact.get("input_source", "")),
        "coverage": coverage,
        "next_coverage_targets": _next_targets(coverage),
        "ok": True,
    }
    for gate in CLAIM_GATES:
        core[gate] = False
    return {**core, "canonical_digest": sha256_digest(core)}


def _next_targets(coverage: Mapping[str, Any]) -> list[str]:
    targets: list[str] = []
    if not coverage["all_classifications_exercised"]:
        targets.append(
            "exercise all four canary classifications "
            f"(missing: {coverage['classifications_missing']})"
        )
    intent_count = coverage["intent_count"]
    if intent_count is None:
        targets.append(
            "report the production-intent breadth of the source corpus"
        )
    elif intent_count < MIN_REPRESENTATIVE_INTENTS:
        targets.append(
            "add a multi-intent corpus beyond the demo "
            f"(only {intent_count} distinct intent(s) seen)"
        )
    if coverage["distinct_mesh_methods"] < 2:
        targets.append("exercise both mesh routing methods (intent and keyword)")
    if coverage["sample_count"] < 20:
        targets.append(
            "collect at least 20 mirrored decisions for a representative rate"
        )
    return targets


def _parse_utc(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fmt(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _print_summary(summary: Mapping[str, Any]) -> None:
    c = summary["coverage"]
    print(
        f"hex canary coverage ({summary['claim_label']}, read-only): "
        f"{c['classifications_exercised_count']}/{c['classifications_total']} "
        f"classifications, {c['sample_count']} samples, "
        f"agreement {c['agreement_rate']}"
    )
    if c["classifications_missing"]:
        print(f"  missing classifications: {c['classifications_missing']}")
    print(
        f"  mesh cells: {c['distinct_mesh_cells']}, "
        f"methods: {c['distinct_mesh_methods']}, "
        f"intents: {c['intent_count']}"
    )
    print("  next coverage targets:")
    for target in summary["next_coverage_targets"] or ["- none"]:
        print(f"    - {target}")


if __name__ == "__main__":
    raise SystemExit(main())
