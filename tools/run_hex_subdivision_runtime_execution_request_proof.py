#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Emit a local proof for fail-closed subdivision execution requests."""
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
from waggledance.core.hex_topology.subdivision_rehearsal import (  # noqa: E402
    build_subdivision_runtime_rehearsal,
)
from waggledance.core.hex_topology.subdivision_runtime_commit import (  # noqa: E402
    build_subdivision_runtime_commit_application,
)
from waggledance.core.hex_topology.subdivision_runtime_execution_request import (  # noqa: E402
    SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION,
    build_subdivision_runtime_execution_request,
)


REPORT_VERSION = "wd.hex_subdivision_runtime_execution_request_proof.v0"


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
        report = build_hex_subdivision_runtime_execution_request_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            "hex subdivision runtime execution request proof FAILED: "
            f"{exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "hex subdivision runtime execution request proof OK: "
            f"{report['proof_path']}"
        )
    else:
        print(
            "hex subdivision runtime execution request proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_subdivision_runtime_execution_request_proof(
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

    topology = _topology()
    preflight = build_subdivision_activation_preflight(
        topology=topology,
        plan=plan_subdivision(
            parent_cell_id="thermal",
            new_child_cell_ids=("thermal.heating", "thermal.cooling"),
            rationale="proof: request executor review for shadow children",
        ),
        canary_comparisons=_canary_comparisons(),
    )
    envelope = build_subdivision_runtime_commit_envelope(
        preflight=preflight,
        operator_signature=_synthetic_signature(
            preflight=preflight,
            signed_at_utc=_format_utc(generated_at),
        ),
    )
    rehearsal = build_subdivision_runtime_rehearsal(
        topology=topology,
        preflight=preflight,
    )
    application = build_subdivision_runtime_commit_application(
        topology=topology,
        commit_envelope=envelope,
        runtime_rehearsal=rehearsal,
    )
    request = build_subdivision_runtime_execution_request(
        runtime_application=application,
        request_metadata=_request_metadata(
            application=application,
            requested_at_utc=_format_utc(generated_at),
        ),
    )
    tampered_application = build_subdivision_runtime_execution_request(
        runtime_application={**application, "application_status": "drifted"},
        request_metadata=_request_metadata(
            application=application,
            requested_at_utc=_format_utc(generated_at),
        ),
    )
    mismatched_metadata = build_subdivision_runtime_execution_request(
        runtime_application=application,
        request_metadata={
            **_request_metadata(
                application=application,
                requested_at_utc=_format_utc(generated_at),
            ),
            "application_digest": "bad",
        },
    )
    runtime_claim_metadata = build_subdivision_runtime_execution_request(
        runtime_application=application,
        request_metadata={
            **_request_metadata(
                application=application,
                requested_at_utc=_format_utc(generated_at),
            ),
            "operator_approval": True,
            "runtime_executor_invoked": True,
        },
    )

    proof_checks = {
        "execution_request_ready": request["ok"] is True,
        "execution_request_does_not_authorize_live_runtime": (
            request["live_runtime_execution_authorized"] is False
            and request["live_runtime_commit_authorized"] is False
            and request["runtime_commit_performed"] is False
            and request["runtime_executor_invoked"] is False
            and request["transport_performed"] is False
        ),
        "execution_request_preserves_application_digest": (
            request["runtime_application_digest"]
            == application["application_digest"]
        ),
        "tampered_application_blocks_request": (
            tampered_application["ok"] is False
            and "runtime_application_status_ready"
            in tampered_application["blockers"]
            and "runtime_application_digest_rederives"
            in tampered_application["blockers"]
        ),
        "mismatched_metadata_blocks_request": (
            mismatched_metadata["ok"] is False
            and "request_application_digest_matches"
            in mismatched_metadata["blockers"]
        ),
        "runtime_claim_metadata_blocks_request": (
            runtime_claim_metadata["ok"] is False
            and "request_contains_no_operator_approval"
            in runtime_claim_metadata["blockers"]
            and "request_contains_no_runtime_claim"
            in runtime_claim_metadata["blockers"]
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
        "request_metadata_fixture": "synthetic_request_not_operator_approval",
        "subdivision_runtime_execution_request": request,
        "tampered_application_blockers": tampered_application["blockers"],
        "mismatched_metadata_blockers": mismatched_metadata["blockers"],
        "runtime_claim_metadata_blockers": runtime_claim_metadata["blockers"],
    }
    proof_path = (
        out_dir / "hex_subdivision_runtime_execution_request_proof.json"
    )
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
            production_cell_id="general",
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


def _request_metadata(
    *,
    application: Mapping[str, Any],
    requested_at_utc: str,
) -> dict[str, Any]:
    return {
        "requested_action": SUBDIVISION_RUNTIME_EXECUTION_REQUEST_ACTION,
        "application_digest": application["application_digest"],
        "plan_id": application["plan_id"],
        "requested_by": "codex-lead-1-proof-fixture",
        "requested_at_utc": requested_at_utc,
        "operator_approval": False,
        "live_runtime_execution_authorized": False,
        "runtime_executor_invoked": False,
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
