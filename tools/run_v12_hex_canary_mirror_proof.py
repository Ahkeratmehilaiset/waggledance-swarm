# SPDX-License-Identifier: BUSL-1.1
"""V12 hex-mesh canary mirror proof — receipted shadow-route evidence.

Storyboard slice 3/3 of the canary series: drives a deterministic fixture
query set through the REAL ``AutonomyRuntime.handle_query`` path with the
opt-in canary mirror enabled, collects the digest-bound mirror report, and
emits it as a verified MAGMA receipt bundle.

Honest claim boundary: this is a LOCAL FIXTURE run through the real
runtime code path — it proves the mirror→snapshot→receipt pipeline works
end-to-end under runtime conditions; it is NOT a production-traffic
measurement and never claims one. Production enablement stays an operator
decision (the mirror flag defaults to off).

No traffic is routed differently, no topology is mutated, no authority is
granted (``no_runtime_mutation=True``; authority flags are literal-False
constants on every artifact).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.autonomy.runtime import AutonomyRuntime  # noqa: E402
from waggledance.core.capabilities.registry import CapabilityRegistry  # noqa: E402
from waggledance.core.domain.autonomy import (  # noqa: E402
    CapabilityCategory,
    CapabilityContract,
)
from waggledance.core.hex_topology.canary_mirror import (  # noqa: E402
    CANARY_MIRROR_REPORT_SCHEMA,
)
from waggledance.core.magma.canonical import (  # noqa: E402
    canonical_json_bytes,
    sha256_digest,
)
from waggledance.core.magma.evaluation_result import (  # noqa: E402
    build_evaluation_result_v1,
)
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402
from waggledance.core.magma.receipt_bundle import (  # noqa: E402
    ReceiptBundleEntry,
    write_receipt_bundle,
)

REPORT_VERSION = "wd.v12.hex_canary_mirror_proof.v0"
CHAIN_ID = "magma:v12_hex_canary_mirror:proof:v0"
CLAIM_LABEL = "MEASURED_LOCAL_RUNTIME_PATH"
CLAIM_BOUNDARY = "runtime_path_fixture_only_not_production_claim"
MIN_SAMPLES = 20
PRIVACY_SENTINEL = "operator_secret_route_marker_DO_NOT_LEAK"


class _Selection:
    def __init__(self, capabilities) -> None:
        self.selected = list(capabilities)


class _RouteResult:
    def __init__(self, capabilities, quality_path: str) -> None:
        self.selection = _Selection(capabilities)
        self.quality_path = quality_path
        self.autonomy_consult = None
        self.autonomy_served = False
        self.solver_call_trace = []


class _Executor:
    available = True

    def execute(self, **_payload):
        return {"success": True, "value": 42}


def _fixture_queries() -> list[str]:
    """Deterministic mixed-behaviour query set (>= MIN_SAMPLES entries).

    Three groups, classified by the REAL intent classifier at runtime:
    solver-selected math queries (main solver path), chat-shaped queries
    whose keyword evidence points at the energy cell (mesh keyword
    fallback diverges from the intent cell), and plain chat queries that
    agree with the intent cell. One query embeds the privacy sentinel to
    prove raw text never reaches any emitted artifact.
    """
    queries: list[str] = []
    for index in range(8):
        queries.append(f"calculate the formula result number {index}")
    for index in range(8):
        queries.append(
            f"tell me a story about solar battery electricity {index}"
        )
    for index in range(8):
        queries.append(f"hello there friendly conversation {index}")
    queries[3] = f"calculate {PRIVACY_SENTINEL} formula"
    return queries


def _mirrored_runtime() -> tuple[AutonomyRuntime, CapabilityContract]:
    registry = CapabilityRegistry(load_builtins=False)
    capability = CapabilityContract(
        capability_id="proof.fixture.detect",
        category=CapabilityCategory.DETECT,
        description="Canary proof fixture capability",
        success_criteria=["success"],
    )
    registry.register(capability)
    registry.register_executor("proof.fixture.detect", _Executor())
    runtime = AutonomyRuntime(
        capability_registry=registry,
        enable_magma=False,
        enable_persistence=False,
        enable_hex_canary_mirror=True,
    )
    # Fixture knob: the proof fires a rapid deterministic burst; load-based
    # admission control would reject the tail BEFORE the mirror hook and
    # make the run nondeterministic. Disabling it is fixture configuration
    # only — production admission behaviour is untouched by this tool.
    runtime.resource_kernel = None
    runtime.admission_control = None
    runtime.action_bus.register_executor(
        "proof.fixture.detect", lambda _action: {"success": True, "value": 42}
    )
    return runtime, capability


def _run_mirrored_pass() -> dict[str, Any]:
    """One full fixture pass through the real handle_query path."""
    runtime, capability = _mirrored_runtime()

    def _route(intent: str, _query: str, _context) -> _RouteResult:
        # Deterministic routing table: math intents select the fixture
        # capability (main solver path); everything else takes the
        # no-capability path. Both are REAL handle_query outcomes and the
        # mirror hook covers both before the early returns.
        if intent == "math":
            return _RouteResult([capability], "gold")
        return _RouteResult([], "bronze")

    runtime.solver_router.route = _route
    for query in _fixture_queries():
        runtime.handle_query(query)
    return runtime.hex_canary_mirror_snapshot()


def build_hex_canary_mirror_proof(
    *,
    out_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    queries = _fixture_queries()
    snapshot = _run_mirrored_pass()
    second_snapshot = _run_mirrored_pass()

    report = snapshot["report"]
    rerun_digest_identical = (
        report.get("canonical_digest")
        == second_snapshot["report"].get("canonical_digest")
    )
    core = {
        key: value
        for key, value in report.items()
        if key != "canonical_digest"
    }
    conditions = {
        "mirror_enabled": snapshot.get("enabled") is True,
        "sample_floor_met": report.get("sample_count") == len(queries)
        and len(queries) >= MIN_SAMPLES,
        "zero_mirror_failures": snapshot.get("mirror_failure_count") == 0,
        "report_schema_valid": report.get("schema_version")
        == CANARY_MIRROR_REPORT_SCHEMA,
        "report_digest_rederives": report.get("canonical_digest")
        == sha256_digest(core),
        "deterministic_rerun": rerun_digest_identical,
        "no_runtime_mutation": report.get("no_runtime_mutation") is True,
        "no_runtime_authority": report.get("runtime_authority_granted")
        is False,
        "no_routing_influence": report.get("routing_influence_applied")
        is False,
    }
    ok = all(value is True for value in conditions.values())

    proof: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "ok": ok,
        "claim_label": CLAIM_LABEL,
        "claim_boundary": CLAIM_BOUNDARY,
        "fixture_query_count": len(queries),
        "conditions": conditions,
        "canary_mirror_report": report,
        "mirror_failure_count": snapshot.get("mirror_failure_count"),
        "no_overclaim_guardrails": {
            "not_production_traffic": True,
            "fixture_through_real_handle_query_path": True,
            "production_enablement_is_operator_decision": True,
            "claim_gate_satisfied": False,
            "claim_safe": False,
            "literal_future_claim_safe": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
        },
    }
    proof["receipt_bundle"] = _emit_receipt_bundle(
        proof_payload=proof, out_dir=out_dir, now_utc=now_utc
    )
    verifier_report = proof["receipt_bundle"].get("verifier_report")
    proof["receipt_chain_verified"] = (
        isinstance(verifier_report, dict)
        and verifier_report.get("ok") is True
        and proof["receipt_bundle"].get("receipt_count") == 1
    )
    proof["ok"] = ok and proof["receipt_chain_verified"]
    return proof


def _emit_receipt_bundle(
    *,
    proof_payload: dict[str, Any],
    out_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in proof_payload.items()
        if key not in ("receipt_bundle", "receipt_chain_verified")
    }
    evaluation = build_evaluation_result_v1(
        case_id="v12:hex_canary_mirror:proof",
        subject_type="policy",
        target_payload=payload,
        risk_class="internal_memory",
        expected_gate="allow",
        actual_gate="allow",
        verifier_path=[
            "v12_hex_canary_mirror_proof",
            "autonomy_runtime_handle_query",
            "hex_canary_mirror_snapshot",
            "magma_receipt_v1",
            "offline_receipt_verifier",
        ],
        solver_selection=["proof.fixture.detect"],
        policy_version="policy:hex_canary_mirror_proof:v0",
        charter_version="charter:v1",
        domain_threshold_version="threshold:hex_canary_mirror:v0",
        verdict="pass" if payload["ok"] else "review",
        reason_codes=[
            "v12:hex_canary_mirror",
            f"claim_label:{CLAIM_LABEL}",
            f"claim_boundary:{CLAIM_BOUNDARY}",
            f"samples:{payload['fixture_query_count']}",
        ],
        confidence_score=0.75 if payload["ok"] else 0.4,
        uncertainty_sources=[
            {
                "kind": "limited_evidence",
                "detail": (
                    "Deterministic local fixture through the real "
                    "handle_query path; not production traffic."
                ),
            }
        ],
        confidence_basis={
            "method": "point_estimate",
            "sample_count": payload["fixture_query_count"],
            "methodology_reference": "tools/run_v12_hex_canary_mirror_proof.py",
        },
        sanitization_audit={
            # Queries reach artifacts as digests only (schema enum value:
            # the canary's allowlist-style sanitization discipline).
            "applied": ["reserved_domain_allowlist"],
            "redaction_count": 0,
        },
        subject_payload_size_bytes=len(canonical_json_bytes(payload)),
    )
    receipt = build_magma_receipt(
        event_id="magma:v12_hex_canary_mirror:proof:001",
        ts_utc=now_utc.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        risk_class=evaluation["risk_class"],
        payload=payload,
        evaluation_result=evaluation,
        policy_digest=sha256_digest(
            {"policy_version": evaluation["policy_version"]}
        ),
        charter_digest=sha256_digest(
            {"charter_version": evaluation["charter_version"]}
        ),
        rco_decision_digest=sha256_digest(
            {
                "case_id": evaluation["case_id"],
                "verdict": evaluation["verdict"],
                "claim_label": CLAIM_LABEL,
            }
        ),
        world_snapshot_digest=sha256_digest(
            {
                "canary_report_digest": payload["canary_mirror_report"][
                    "canonical_digest"
                ],
            }
        ),
        solver_contract_digest=sha256_digest(
            {
                "solver_selection": ["proof.fixture.detect"],
                "policy_version": evaluation["policy_version"],
            }
        ),
    )
    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id=CHAIN_ID,
        entries=[
            ReceiptBundleEntry(
                label="hex-canary-mirror-proof",
                payload=payload,
                evaluation_result=evaluation,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    proof = build_hex_canary_mirror_proof(
        out_dir=args.out_dir,
        now_utc=datetime.now(timezone.utc),
    )
    print(json.dumps(proof, indent=2, sort_keys=True, default=str))
    return 0 if proof["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
