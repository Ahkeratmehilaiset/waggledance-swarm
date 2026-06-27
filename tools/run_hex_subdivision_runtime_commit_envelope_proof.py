#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Emit a local proof for the dormant subdivision runtime commit envelope."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.hex_topology.canary_mirror import (  # noqa: E402
    build_canary_route_comparison,
)
from waggledance.core.hex_topology.subdivision_commit import (  # noqa: E402
    SUBDIVISION_RUNTIME_COMMIT_ACTION,
    build_subdivision_runtime_commit_envelope,
)
from waggledance.core.hex_topology.subdivision_operator import (  # noqa: E402
    plan_subdivision,
)
from waggledance.core.hex_topology.subdivision_preflight import (  # noqa: E402
    build_subdivision_activation_preflight,
)


REPORT_VERSION = "wd.hex_subdivision_runtime_commit_envelope_proof.v0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New output directory for the proof. It must not already exist.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-27T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_hex_subdivision_runtime_commit_envelope_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            "hex subdivision runtime commit envelope proof FAILED: "
            f"{exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "hex subdivision runtime commit envelope proof OK: "
            f"{report['proof_path']}"
        )
    else:
        print(
            "hex subdivision runtime commit envelope proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_subdivision_runtime_commit_envelope_proof(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    preflight = build_subdivision_activation_preflight(
        topology=_topology(),
        plan=plan_subdivision(
            parent_cell_id="thermal",
            new_child_cell_ids=("thermal.heating", "thermal.cooling"),
            rationale="proof: split thermal cell into shadow children",
        ),
        canary_comparisons=_canary_comparisons(),
    )
    signed_envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=_synthetic_signature(
            preflight=preflight,
            signed_at_utc=_format_utc(generated_at),
        ),
    )
    unsigned_envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
    )
    forged_envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature={
            **_synthetic_signature(
                preflight=preflight,
                signed_at_utc=_format_utc(generated_at),
            ),
            "preflight_digest": "forged",
        },
    )

    proof_checks = {
        "signed_fixture_envelope_ready": signed_envelope["ok"] is True,
        "unsigned_envelope_blocks": (
            unsigned_envelope["ok"] is False
            and "operator_signature_present"
            in unsigned_envelope["blockers"]
        ),
        "forged_signature_blocks": (
            forged_envelope["ok"] is False
            and "operator_signature_preflight_digest_matches"
            in forged_envelope["blockers"]
        ),
        "signed_fixture_does_not_commit_runtime": (
            signed_envelope["runtime_commit_performed"] is False
            and signed_envelope["runtime_topology_mutation_applied"] is False
            and signed_envelope["transport_performed"] is False
        ),
    }
    blockers = [
        name for name, passed in proof_checks.items() if passed is not True
    ]
    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "proof_checks": proof_checks,
        "operator_signature_fixture": (
            "synthetic_test_only_not_real_operator_approval"
        ),
        "subdivision_runtime_commit_envelope": signed_envelope,
        "unsigned_envelope_blockers": unsigned_envelope["blockers"],
        "forged_signature_blockers": forged_envelope["blockers"],
    }
    proof_path = out_dir / "hex_subdivision_runtime_commit_envelope_proof.json"
    report["proof_path"] = str(proof_path)
    proof_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _topology() -> dict[str, Any]:
    return {
        "cells": {
            "root": {
                "cell_id": "root",
                "parent_cell_id": None,
                "child_cell_ids": ["thermal"],
                "neighbor_cell_ids": [],
            },
            "thermal": {
                "cell_id": "thermal",
                "parent_cell_id": "root",
                "child_cell_ids": [],
                "neighbor_cell_ids": [],
            },
        },
    }


def _canary_comparisons() -> list[dict[str, Any]]:
    return [
        build_canary_route_comparison(
            query="heating load during frost warning",
            intent="thermal",
            production_capability_id="cap.thermal.frost",
            production_cell_id="thermal",
            quality_path="shadow_preflight",
        ),
        build_canary_route_comparison(
            query="hello from the general assistant",
            intent="chat",
            production_capability_id="cap.chat.general",
            quality_path="shadow_preflight",
        ),
    ]


def _synthetic_signature(
    *,
    preflight: Mapping[str, Any],
    signed_at_utc: str,
) -> dict[str, Any]:
    return {
        "action": SUBDIVISION_RUNTIME_COMMIT_ACTION,
        "plan_id": preflight["plan_id"],
        "preflight_digest": preflight["preflight_digest"],
        "signed_by": "synthetic-operator-fixture",
        "signed_at_utc": signed_at_utc,
    }


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise ValueError(f"--now requires a UTC timestamp: {value}")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
