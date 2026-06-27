#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""End-to-end conformance proof for the shadow subdivision-runtime pipeline.

The per-stage proofs each validate ONE stage with synthetic fixtures for the
prior stages. This proof instead builds the REAL chain --
``plan -> preflight -> commit envelope -> rehearsal -> commit application ->
execution request`` -- feeding each stage's real output into the next, and
asserts the cross-stage HAND-OFFS hold end-to-end:

* the preflight / envelope / rehearsal / application digests flow unchanged from
  the stage that mints them to every downstream stage that embeds them;
* the plan identity (plan_id, parent, children) is identical across all stages;
* NO stage in the assembled chain authorizes a live runtime commit, mutates
  topology, influences routing, performs transport, or invokes the executor
  (the dormant / shadow invariant holds for the whole pipeline, not just one
  stage); and
* a corrupted hand-off propagates to a fail-closed final request (the chain does
  not let an inconsistent intermediate result through).

It is observability-only: it builds artifacts and compares digests; it has no
runtime authority and performs no topology mutation, routing, or transport.
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


REPORT_VERSION = "wd.hex_subdivision_runtime_pipeline_e2e_proof.v0"

# Flags that, if ever True on any stage, would mean a live runtime side effect.
_FORBIDDEN_TRUE_FLAGS = (
    "live_runtime_execution_authorized",
    "live_runtime_commit_authorized",
    "runtime_authority_granted",
    "runtime_topology_mutation_applied",
    "routing_influence_applied",
    "transport_performed",
    "claim_safe_upgrade",
    "runtime_commit_performed",
    "runtime_executor_invoked",
)


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
        report = build_hex_subdivision_runtime_pipeline_e2e_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"hex subdivision runtime pipeline e2e proof FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "hex subdivision runtime pipeline e2e proof OK: "
            f"{report['proof_path']}"
        )
    else:
        print(
            "hex subdivision runtime pipeline e2e proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_subdivision_runtime_pipeline_e2e_proof(
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

    chain = _build_chain(generated_at)
    preflight = chain["preflight"]
    envelope = chain["envelope"]
    rehearsal = chain["rehearsal"]
    application = chain["application"]
    request = chain["request"]

    # --- cross-stage hand-off digest flow (the unique e2e assertions) ---
    preflight_digest = preflight.get("preflight_digest")
    preflight_flow = [
        envelope.get("subdivision_preflight_digest"),
        rehearsal.get("subdivision_preflight_digest"),
        application.get("subdivision_preflight_digest"),
        request.get("subdivision_preflight_digest"),
    ]
    envelope_flow = [
        application.get("commit_envelope_digest"),
        request.get("commit_envelope_digest"),
    ]
    rehearsal_flow = [
        application.get("runtime_rehearsal_digest"),
        request.get("runtime_rehearsal_digest"),
    ]

    identity = _identity(preflight)

    # --- fail-closed propagation: corrupt the application->request hand-off ---
    broken_application = {
        **application,
        "commit_envelope_digest": "tampered-handoff-digest",
    }
    broken_request = build_subdivision_runtime_execution_request(
        runtime_application=broken_application,
        request_metadata=_request_metadata(
            application=application,
            requested_at_utc=_format_utc(generated_at),
        ),
    )

    proof_checks = {
        "pipeline_chain_composes": (
            preflight.get("ok") is True
            and envelope.get("ok") is True
            and rehearsal.get("ok") is True
            and application.get("ok") is True
            and request.get("ok") is True
        ),
        "preflight_digest_flows_end_to_end": (
            isinstance(preflight_digest, str)
            and bool(preflight_digest)
            and all(value == preflight_digest for value in preflight_flow)
        ),
        "envelope_digest_flows_end_to_end": (
            isinstance(envelope.get("envelope_digest"), str)
            and bool(envelope.get("envelope_digest"))
            and all(
                value == envelope.get("envelope_digest")
                for value in envelope_flow
            )
        ),
        "rehearsal_digest_flows_end_to_end": (
            isinstance(rehearsal.get("rehearsal_digest"), str)
            and bool(rehearsal.get("rehearsal_digest"))
            and all(
                value == rehearsal.get("rehearsal_digest")
                for value in rehearsal_flow
            )
        ),
        "application_digest_flows_to_request": (
            isinstance(application.get("application_digest"), str)
            and bool(application.get("application_digest"))
            and request.get("runtime_application_digest")
            == application.get("application_digest")
        ),
        "plan_identity_consistent_end_to_end": (
            identity is not None
            and _identity(envelope) == identity
            and _identity(rehearsal) == identity
            and _identity(application) == identity
            and _identity(request) == identity
        ),
        "no_stage_authorizes_live_runtime": all(
            _stage_has_no_forbidden_true_flag(stage)
            for stage in (
                preflight,
                envelope,
                rehearsal,
                application,
                request,
            )
        ),
        "broken_handoff_fails_closed": (
            broken_request.get("ok") is False
            and "application_envelope_digest_matches_embedded"
            in broken_request.get("blockers", [])
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
        "pipeline_stage_order": [
            "preflight",
            "commit_envelope",
            "rehearsal",
            "commit_application",
            "execution_request",
        ],
        "handoff_digests": {
            "preflight_digest": preflight_digest,
            "envelope_digest": envelope.get("envelope_digest"),
            "rehearsal_digest": rehearsal.get("rehearsal_digest"),
            "application_digest": application.get("application_digest"),
            "execution_request_digest": request.get("execution_request_digest"),
        },
        "plan_identity": (
            [identity[0], identity[1], list(identity[2])]
            if identity is not None
            else None
        ),
        "broken_handoff_blockers": broken_request.get("blockers", []),
        "execution_request_ok": request.get("ok"),
        "execution_request_live_runtime_authorized": request.get(
            "live_runtime_execution_authorized"
        ),
    }
    proof_path = out_dir / "hex_subdivision_runtime_pipeline_e2e_proof.json"
    report["proof_path"] = str(proof_path)
    proof_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _build_chain(generated_at: datetime) -> dict[str, Any]:
    topology = _topology()
    preflight = build_subdivision_activation_preflight(
        topology=topology,
        plan=plan_subdivision(
            parent_cell_id="thermal",
            new_child_cell_ids=("thermal.heating", "thermal.cooling"),
            rationale="proof: end-to-end shadow subdivision pipeline",
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
    return {
        "preflight": preflight,
        "envelope": envelope,
        "rehearsal": rehearsal,
        "application": application,
        "request": request,
    }


def _identity(stage: Mapping[str, Any]) -> tuple[Any, Any, tuple[Any, ...]] | None:
    if not isinstance(stage, Mapping):
        return None
    if "plan_id" not in stage or "parent_cell_id" not in stage:
        return None
    return (
        stage.get("plan_id"),
        stage.get("parent_cell_id"),
        tuple(stage.get("new_child_cell_ids") or []),
    )


def _stage_has_no_forbidden_true_flag(stage: Mapping[str, Any]) -> bool:
    if not isinstance(stage, Mapping):
        return False
    return all(stage.get(flag) is not True for flag in _FORBIDDEN_TRUE_FLAGS)


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
        "requested_by": "fable-5-e2e-proof-fixture",
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
