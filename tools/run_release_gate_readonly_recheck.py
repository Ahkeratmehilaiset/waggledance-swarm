#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record a read-only release-gate recheck.

This tool observes the existing stable-release gate and records the result
without creating tags, moving Docker aliases, claiming a stable release, or
granting external-effect authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_release_gate import evaluate_release_gate

SCHEMA_VERSION = "waggledance.release_gate_readonly_recheck.v0"
DEFAULT_RELEASE_READINESS = Path("docs/release/RELEASE_READINESS.md")
DEFAULT_SOAK_EVIDENCE = Path("docs/runs/release_soak_evidence/v3.12.0.json")

RELEASE_BOUNDARY = {
    "tag_creation": False,
    "docker_latest_move": False,
    "stable_release_claim": False,
    "external_effect_authority_change": False,
}

READ_ONLY_INVARIANTS = {
    "release_gate_effect": "observation_only",
    "no_tag_created": True,
    "no_docker_latest_moved": True,
    "no_stable_release_claim": True,
    "no_external_effect_authority_change": True,
}


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value.strip())


def _parse_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def build_report(
    *,
    release_readiness: Path | str = DEFAULT_RELEASE_READINESS,
    soak_evidence: Path | str | None = DEFAULT_SOAK_EVIDENCE,
    checked_at_utc: dt.datetime | None = None,
    today: dt.date | None = None,
) -> dict[str, Any]:
    checked_at_utc = checked_at_utc or _utc_now()
    checked_at_utc = checked_at_utc.astimezone(dt.UTC).replace(microsecond=0)
    today = today or checked_at_utc.date()

    gate = evaluate_release_gate(
        release_readiness,
        soak_evidence_path=soak_evidence,
        today=today,
    )
    decision = gate.get("decision")
    blockers = gate.get("blockers", [])
    ok = decision in {"hold", "pass"} and all(
        value is False for value in RELEASE_BOUNDARY.values()
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "checked_at_utc": checked_at_utc.isoformat().replace("+00:00", "Z"),
        "release_gate_decision": decision,
        "blockers": blockers if isinstance(blockers, list) else [],
        "read_only": True,
        "release_gate_effect": "none",
        "release_boundary": dict(RELEASE_BOUNDARY),
        "read_only_invariants": dict(READ_ONLY_INVARIANTS),
        "gate": gate,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-readiness",
        default=DEFAULT_RELEASE_READINESS,
        type=Path,
    )
    parser.add_argument("--soak-evidence", default=DEFAULT_SOAK_EVIDENCE, type=Path)
    parser.add_argument(
        "--today",
        type=_parse_date,
        help="Override the release-gate date, YYYY-MM-DD.",
    )
    parser.add_argument(
        "--checked-at-utc",
        type=_parse_timestamp,
        help="Override report timestamp, ISO-8601 UTC.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    report = build_report(
        release_readiness=args.release_readiness,
        soak_evidence=args.soak_evidence,
        checked_at_utc=args.checked_at_utc,
        today=args.today,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
