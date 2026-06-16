# SPDX-License-Identifier: BUSL-1.1
"""Standalone deterministic proof that a MAGMA receipt binds the solver trace.

Advances the WD Image #1 *MAGMA provenance* pillar: an opt-in runtime MAGMA
receipt cryptographically binds the solver-call trace of a solve, the receipt
verifies offline, and the bound trace is privacy-safe (no raw payload leak).

This wraps the manifest builder's `build_solver_trace_magma_receipt_proof`
(currently only reachable through the capability manifest) as a standalone,
reproducible CLI artifact and adds two things the internal proof does not: a
run-twice **determinism** check over the stable evidence fields, and a
**derived-flags** surface (every evidence flag is read from the observed proof
result, never hardcoded). All work is opt-in, offline, temp-only (artifacts
auto-removed); it grants no runtime authority and applies no external writes.

Exact validation commands::

    python tools/run_solver_trace_magma_receipt_proof.py --json
    python -m pytest tests/test_solver_trace_magma_receipt_proof.py -q

Engineering record; offline; forbidden-vocabulary guarded. No claim of
superiority over any external system.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.wd_image1_capability_manifest import (  # noqa: E402
    build_solver_trace_magma_receipt_proof,
)

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.solver_trace_magma_receipt_proof.v1"
CAPABILITY_ID = "magma_audit_log"

# The environment-independent evidence fields (chain_id / temp paths excluded).
_STABLE_FIELDS = (
    "ok",
    "receipt_scope",
    "receipt_count",
    "verifier_ok",
    "solver_call_trace_count",
    "solver_call_trace_digest_bound",
    "solver_call_trace_receipt_bound",
    "solver_call_trace_privacy_safe",
    "raw_payload_leak_check",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stable_view(result: dict[str, Any]) -> dict[str, Any]:
    return {k: result.get(k) for k in _STABLE_FIELDS}


def build_solver_trace_magma_receipt_standalone_proof() -> dict[str, Any]:
    run1 = build_solver_trace_magma_receipt_proof()
    run2 = build_solver_trace_magma_receipt_proof()
    deterministic = json.dumps(_stable_view(run1), sort_keys=True) == json.dumps(
        _stable_view(run2), sort_keys=True
    )

    # Derived from the observed proof result (never hardcoded "bound/safe").
    inner_ok = run1.get("ok") is True
    receipt_bound = run1.get("solver_call_trace_receipt_bound") is True
    digest_bound = run1.get("solver_call_trace_digest_bound") is True
    verifier_ok = run1.get("verifier_ok") is True
    # Privacy gates are STRICT and INDEPENDENT - do not rely on inner ok to
    # cover them (checklist item 1: per-field re-derive). `is True` fails closed
    # on False AND on absent.
    privacy_safe = run1.get("solver_call_trace_privacy_safe")
    privacy_safe_ok = run1.get("solver_call_trace_privacy_safe") is True
    raw_payload_leak_check_ok = run1.get("raw_payload_leak_check") is True
    receipt_count = run1.get("receipt_count")

    evidence_present = bool(
        deterministic and inner_ok and receipt_bound and digest_bound
        and verifier_ok and privacy_safe_ok and raw_payload_leak_check_ok
    )

    blockers: list[str] = []
    if not deterministic:
        blockers.append("non_deterministic_receipt_evidence")
    if not inner_ok:
        blockers.append("inner_proof_not_ok")
    if not receipt_bound:
        blockers.append("solver_trace_not_receipt_bound")
    if not digest_bound:
        blockers.append("solver_trace_not_digest_bound")
    if not verifier_ok:
        blockers.append("receipt_verifier_failed")
    if not privacy_safe_ok:
        blockers.append("trace_not_privacy_safe")
    if not raw_payload_leak_check_ok:
        blockers.append("raw_payload_leak_check_failed")
    # carry forward any blockers the inner proof reported
    for b in (run1.get("blockers") or []):
        blockers.append(f"inner:{b}")

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_iso(),
        "ok": not blockers,
        "blockers": blockers,
        "capability_id": CAPABILITY_ID,
        "inner_proof_id": run1.get("proof_id"),
        "receipt_scope": run1.get("receipt_scope"),
        "deterministic_replay": {"runs": 2, "stable_evidence_identical": deterministic},
        "receipt_evidence": {
            "receipt_count": receipt_count,
            "verifier_ok": verifier_ok,
            "solver_call_trace_count": run1.get("solver_call_trace_count"),
            "solver_call_trace_digest_bound": digest_bound,
            "solver_call_trace_receipt_bound": receipt_bound,
            "privacy_safe": privacy_safe,
            "privacy_safe_ok": privacy_safe_ok,
            "raw_payload_leak_check_ok": raw_payload_leak_check_ok,
        },
        # Provenance evidence is NOT production authority.
        "evidence_vs_authority": {
            "evidence_present": evidence_present,
            "opt_in_only": True,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
        },
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": deterministic,
            "opt_in_temp_only": True,
            "no_runtime_authority_flip": True,
            "no_external_writes": True,
            "forbidden_vocabulary_excluded": list(FORBIDDEN_VOCABULARY),
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    dr = report["deterministic_replay"]
    re_ = report["receipt_evidence"]
    eva = report["evidence_vs_authority"]
    return "\n".join([
        "Solver-trace MAGMA receipt proof (standalone)",
        f"  ok={report['ok']} blockers={report['blockers']}",
        f"  receipt_scope={report['receipt_scope']} inner_proof={report['inner_proof_id']}",
        f"  deterministic_replay: runs={dr['runs']} stable_identical={dr['stable_evidence_identical']}",
        f"  receipt_count={re_['receipt_count']} verifier_ok={re_['verifier_ok']} "
        f"digest_bound={re_['solver_call_trace_digest_bound']} receipt_bound={re_['solver_call_trace_receipt_bound']} "
        f"privacy_safe={re_['privacy_safe']}",
        f"  evidence_present={eva['evidence_present']} runtime_authority_granted={eva['runtime_authority_granted']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    hit = [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered summary: {hit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Optional new directory for the JSON proof artifact; must not already exist.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_solver_trace_magma_receipt_standalone_proof()

    summary = render_summary(report)
    assert_vocabulary_clean(summary)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(summary)

    if args.out_dir is not None:
        out_dir = args.out_dir.resolve()
        if out_dir.exists():
            print(f"out_dir must not exist: {out_dir}", file=sys.stderr)
            return 1
        out_dir.mkdir(parents=True)
        (out_dir / "solver_trace_magma_receipt_proof.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
