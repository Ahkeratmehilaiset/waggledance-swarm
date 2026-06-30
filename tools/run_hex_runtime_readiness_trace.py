#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Offline deterministic TRACE that links a hex routing intent to runtime-readiness
evidence, bound end-to-end by a SINGLE shared execution-request digest.

Fable lane, P4 runtime-readiness sprint (Seed 6: "Hex runtime-readiness trace
harness"). This is product evidence, NOT activation. It stitches the already
merged readiness pieces into ONE deterministic, digest-bound trace:

    routing intent  ->  solver verdict (subdivision plan)
                    ->  executor-admission dry-run  (run_hex_subdivision_runtime_readiness_dry_run, #1421)
                    ->  observability roll-up         (run_hex_runtime_readiness_observability_rollup, #1431/#1432)

The load-bearing property (and the lesson from the #1421 readiness-composition
bug: "each link valid" != "links form one chain"): every stage is BOUND to one
shared `execution_request_digest`. The solver verdict reproduces the plan_id that
drives that request; the routing intent targets the same parent the verdict
subdivides; the dry-run's pipeline + admission digests and the roll-up's surfaced
digests must ALL equal the single canonical `execution_request_digest`. If any
link diverges, the binding fails closed (see evaluate_runtime_readiness_trace_binding,
exercised with a perturbed digest in the test).

Authority stays false throughout: the trace plans, delivers a ring observation,
reads what the readiness harnesses decide, and asserts transport=false,
scheduler_enqueue_allowed=false, runtime_mutation_authority=false, and
production_activation_ready=false. It grants no runtime authority and mutates no
live topology.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_hex_runtime_readiness_observability_rollup import (  # noqa: E402
    build_hex_runtime_readiness_observability_rollup,
)
from tools.run_hex_subdivision_runtime_pipeline_e2e_proof import (  # noqa: E402
    _build_chain,
)
from tools.run_hex_subdivision_runtime_readiness_dry_run import (  # noqa: E402
    build_hex_subdivision_runtime_readiness_dry_run,
)
from waggledance.core.hex_topology.cell_message_contract import (  # noqa: E402
    make_message,
)
from waggledance.core.hex_topology.ring_messaging import (  # noqa: E402
    deliver_one,
    summarize_ring_delivery_batch,
)
from waggledance.core.hex_topology.subdivision_operator import (  # noqa: E402
    apply_plan_to_topology,
    compute_plan_id,
    plan_subdivision,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


REPORT_VERSION = "wd.hex_runtime_readiness_trace.v0"
OUTPUT_FILENAME = "hex_runtime_readiness_trace.json"
# Strict: lowercase-hex only. A 'sha256:' + 64 non-hex chars value must NOT pass
# (a prefix+length check alone would fail open -- a fake but self-consistent
# digest could carry the single-shared-digest proof; see the binding test).
SHA256_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TRACE_STATUS = "runtime_ready_evidence_traced_activation_blocked"
TRACE_BLOCKED_STATUS = "runtime_readiness_trace_blocked"

# The trace asserts these dormancy flags hold across every stage it links.
TRACE_AUTHORITY_BOUNDARY = {
    "transport": False,
    "transport_performed": False,
    "scheduler_enqueue_allowed": False,
    "runtime_mutation_authority": False,
    "production_activation_ready": False,
    "routing_influence_applied": False,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="New output directory for the trace; must not exist.")
    parser.add_argument("--now", default=None,
                        help="Optional UTC override such as 2026-06-30T00:00:00Z.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_hex_runtime_readiness_trace(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(f"hex runtime-readiness trace FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(f"hex runtime-readiness trace OK: {report['report_path']}")
    else:
        print("hex runtime-readiness trace FAILED: "
              f"{', '.join(report['blockers'])}", file=sys.stderr)
    return 0 if report["ok"] else 1


def evaluate_runtime_readiness_trace_binding(
    *,
    canonical_execution_request_digest: str,
    canonical_plan_id: str,
    solver_plan_id: str,
    routing_target_parent_cell_id: str,
    solver_parent_cell_id: str,
    routing_delivered: bool,
    routing_transport_applied: bool,
    readiness_ok: bool,
    readiness_execution_request_digest: str,
    readiness_admission_request_digest: str,
    readiness_authority_false_everywhere: bool,
    readiness_production_activation_ready: bool,
    readiness_runtime_mutation_authority: bool,
    readiness_scheduler_enqueue_allowed: bool,
    readiness_forbidden_true_flag_paths: Sequence[str],
    rollup_ok: bool,
    rollup_pipeline_request_digest: str,
    rollup_admission_request_digest: str,
    rollup_production_activation_ready: bool,
    rollup_runtime_mutation_authority: bool,
    rollup_forbidden_true_flag_paths: Sequence[str],
) -> dict[str, bool]:
    """Pure binding evaluation. Every digest-binding check compares against the
    single canonical execution-request digest, so perturbing ANY one link makes
    its check (and the single-shared-digest check) fail. Fail-closed by design.
    """
    bound_digests = (
        readiness_execution_request_digest,
        readiness_admission_request_digest,
        rollup_pipeline_request_digest,
        rollup_admission_request_digest,
    )
    shared = canonical_execution_request_digest
    is_sha256 = isinstance(shared, str) and bool(SHA256_DIGEST_RE.fullmatch(shared))
    return {
        "canonical_execution_request_digest_well_formed": is_sha256,
        "solver_verdict_reproduces_canonical_plan_id": (
            solver_plan_id == canonical_plan_id and bool(canonical_plan_id)
        ),
        "routing_intent_targets_solver_parent": (
            routing_target_parent_cell_id == solver_parent_cell_id
            and bool(solver_parent_cell_id)
        ),
        "routing_intent_delivered": routing_delivered is True,
        "no_transport_in_routing": routing_transport_applied is False,
        "readiness_proof_ok": readiness_ok is True,
        "readiness_request_digest_bound": readiness_execution_request_digest == shared,
        "readiness_admission_digest_bound": readiness_admission_request_digest == shared,
        "rollup_proof_ok": rollup_ok is True,
        "rollup_pipeline_digest_bound": rollup_pipeline_request_digest == shared,
        "rollup_admission_digest_bound": rollup_admission_request_digest == shared,
        "single_shared_execution_request_digest": (
            is_sha256
            and all(
                isinstance(d, str) and bool(SHA256_DIGEST_RE.fullmatch(d))
                for d in bound_digests
            )
            and all(d == shared for d in bound_digests)
        ),
        "readiness_authority_false_everywhere": (
            readiness_authority_false_everywhere is True
        ),
        "production_activation_not_ready": (
            readiness_production_activation_ready is False
            and rollup_production_activation_ready is False
        ),
        "runtime_mutation_authority_false": (
            readiness_runtime_mutation_authority is False
            and rollup_runtime_mutation_authority is False
        ),
        "scheduler_enqueue_allowed_false": (
            readiness_scheduler_enqueue_allowed is False
        ),
        "forbidden_true_flags_absent": (
            list(readiness_forbidden_true_flag_paths) == []
            and list(rollup_forbidden_true_flag_paths) == []
        ),
    }


def build_hex_runtime_readiness_trace(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    # (1) Canonical execution request -- the single authoritative request the
    # readiness dry-run harness itself builds from the same deterministic chain.
    # Reusing _build_chain with the SAME generated_at guarantees our front-end
    # binds to the EXACT request the readiness/rollup back-end evaluates.
    request = _build_chain(generated_at)["request"]
    shared_digest = request["execution_request_digest"]
    canonical_plan_id = request["plan_id"]
    parent = request["parent_cell_id"]
    children = tuple(request["new_child_cell_ids"])

    # (2) Solver verdict: reproduce the subdivision plan that drives the request.
    plan = plan_subdivision(
        parent_cell_id=parent,
        new_child_cell_ids=children,
        rationale="trace: runtime-readiness evidence (no runtime authority)",
    )
    solver_verdict = plan.to_dict()
    solver_verdict["computed_plan_id"] = compute_plan_id(
        parent_cell_id=parent, new_child_cell_ids=children
    )

    # (3) Routing intent: an offline ring observation inside the subdivided
    # topology -- a child signalling its parent. Same parent the verdict
    # subdivides; delivered with transport_applied == False.
    routing_intent, routing_summary = _routing_intent(parent, children)

    # (4) Executor-admission dry-run (readiness evidence).
    readiness = build_hex_subdivision_runtime_readiness_dry_run(
        out_dir=out_dir / "readiness", now_utc=generated_at
    )
    readiness_pipeline = readiness["source_reports"]["pipeline_e2e"]
    readiness_admission = readiness["source_reports"]["executor_admission"]

    # (5) Observability roll-up over the readiness report.
    rollup = build_hex_runtime_readiness_observability_rollup(
        readiness_report_path=Path(readiness["report_path"]),
        out_dir=out_dir / "rollup",
        now_utc=generated_at,
    )
    rollup_evidence = rollup["evidence"]

    # (6) Bind the whole chain to the single canonical digest (fail-closed).
    trace_checks = evaluate_runtime_readiness_trace_binding(
        canonical_execution_request_digest=shared_digest,
        canonical_plan_id=canonical_plan_id,
        solver_plan_id=plan.plan_id,
        routing_target_parent_cell_id=routing_intent["target_parent_cell_id"],
        solver_parent_cell_id=parent,
        routing_delivered=routing_intent["delivered"],
        routing_transport_applied=routing_summary["transport_applied"],
        readiness_ok=readiness["ok"],
        readiness_execution_request_digest=readiness_pipeline["execution_request_digest"],
        readiness_admission_request_digest=readiness_admission[
            "runtime_execution_request_digest"
        ],
        readiness_authority_false_everywhere=readiness["proof_checks"][
            "runtime_authority_false_everywhere"
        ],
        readiness_production_activation_ready=readiness["production_activation_ready"],
        readiness_runtime_mutation_authority=readiness["runtime_mutation_authority"],
        readiness_scheduler_enqueue_allowed=readiness["authority_boundary"][
            "scheduler_enqueue_allowed"
        ],
        readiness_forbidden_true_flag_paths=readiness["forbidden_true_flag_paths"],
        rollup_ok=rollup["ok"],
        rollup_pipeline_request_digest=rollup_evidence["pipeline_execution_request_digest"],
        rollup_admission_request_digest=rollup_evidence[
            "executor_admission_execution_request_digest"
        ],
        rollup_production_activation_ready=rollup["production_activation_ready"],
        rollup_runtime_mutation_authority=rollup["runtime_mutation_authority"],
        rollup_forbidden_true_flag_paths=rollup["forbidden_true_flag_paths"],
    )
    blockers = [name for name, ok in trace_checks.items() if ok is not True]

    core = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "trace_status": TRACE_STATUS if not blockers else TRACE_BLOCKED_STATUS,
        "shared_execution_request_digest": shared_digest,
        "plan_id": canonical_plan_id,
        "production_activation_ready": False,
        "runtime_mutation_authority": False,
        "authority_boundary": dict(TRACE_AUTHORITY_BOUNDARY),
        "trace_checks": trace_checks,
        "trace_links": {
            "routing_intent": {
                "target_parent_cell_id": routing_intent["target_parent_cell_id"],
                "target_child_cell_ids": routing_intent["target_child_cell_ids"],
                "message_kind": routing_intent["message_kind"],
                "delivered": routing_intent["delivered"],
                "transport_applied": routing_summary["transport_applied"],
                "routing_intent_digest": sha256_digest(routing_intent),
            },
            "solver_verdict": {
                "plan_id": plan.plan_id,
                "parent_cell_id": parent,
                "new_child_cell_ids": list(children),
                "no_runtime_mutation": plan.no_runtime_mutation,
                "solver_verdict_digest": sha256_digest(solver_verdict),
            },
            "executor_admission_dry_run": {
                "report_version": readiness["report_version"],
                "ok": readiness["ok"],
                "readiness_status": readiness["readiness_status"],
                "readiness_digest": readiness["readiness_digest"],
                "execution_request_digest": readiness_pipeline["execution_request_digest"],
                "runtime_execution_request_digest": readiness_admission[
                    "runtime_execution_request_digest"
                ],
            },
            "observability_rollup": {
                "report_version": rollup["report_version"],
                "ok": rollup["ok"],
                "rollup_status": rollup["rollup_status"],
                "pipeline_execution_request_digest": rollup_evidence[
                    "pipeline_execution_request_digest"
                ],
                "executor_admission_execution_request_digest": rollup_evidence[
                    "executor_admission_execution_request_digest"
                ],
            },
        },
    }
    report_path = out_dir / OUTPUT_FILENAME
    report = {
        **core,
        "trace_digest": sha256_digest(core),
        "report_path": str(report_path),
    }
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _routing_intent(
    parent: str, children: tuple[str, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an offline ring observation inside the subdivided topology: the
    first new child signals its parent (child_to_parent). Delivers with no
    transport; binds to `parent`.
    """
    base = {
        "cells": {
            parent: {
                "cell_id": parent,
                "parent_cell_id": None,
                "child_cell_ids": [],
                "neighbor_cell_ids": [],
            }
        }
    }
    plan = plan_subdivision(
        parent_cell_id=parent,
        new_child_cell_ids=children,
        rationale="trace: routing-intent topology (no runtime authority)",
    )
    subdivided = apply_plan_to_topology(base, plan)
    first_child = sorted(children)[0]
    delivery = deliver_one(
        subdivided,
        make_message(
            from_cell_id=first_child, to_cell_id=parent, kind="child_to_parent"
        ),
        0,
    )
    summary = summarize_ring_delivery_batch([delivery])
    intent = {
        "target_parent_cell_id": parent,
        "target_child_cell_ids": sorted(children),
        "message_kind": "child_to_parent",
        "from_cell_id": first_child,
        "delivered": delivery.delivered,
        "blocked_category": delivery.blocked_category,
    }
    return intent, summary


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if (parsed.tzinfo is None
            or parsed.utcoffset() != timezone.utc.utcoffset(parsed)):
        raise ValueError(f"--now requires a UTC timestamp: {value}")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
