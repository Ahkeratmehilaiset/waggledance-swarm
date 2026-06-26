#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""P4a auto-rollback ELIGIBILITY verifier (RFC P4 / item P4a).

DORMANT pure logic: this module decides WHETHER a proposed rollback is auto-eligible.
It performs NO git mutation, no network, no I/O on the core path -- the actual
revert/push wiring is a separate, operator-signed step (this mirrors the P1 checker:
build the dormant decision logic first, wire it later).

The ONLY auto-eligible rollback is a PURE REVERT to a known-green SHA, fired by a
confirmed (debounced, high-confidence) failure signal (rco-1's P4a/P4b constraints,
RFC #1385):

  * the target SHA must be a prior KNOWN-GREEN + CONSENSUS-APPROVED state, and
  * the rollback's resulting tree must equal the target SHA's tree (mechanically
    verified -- no new content may ride in on the rollback), and
  * the failure signal must be CONFIRMED (debounced / N-of-M) -- never a single
    possibly-flaky canary run (prevents revert-thrash of a good merge).

Anything else -- target not known-green, tree mismatch, unconfirmed/low-confidence
signal, a force-push/rewrite/release-surface target, or any missing/malformed input --
returns ``operator_escalate``. FAIL-CLOSED: uncertainty never auto-rolls-back.

    python tools/auto_rollback_eligibility.py --decision-json <path> [--json]

Exit codes: 0 auto_rollback eligible; 3 operator_escalate; 2 arg/parse error.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Mapping, Sequence

AUTO_ROLLBACK = "auto_rollback"
OPERATOR_ESCALATE = "operator_escalate"

# A rollback target that touches any of these is NEVER auto-eligible (operator-only,
# per CLAUDE.md release/tag/Docker-stable rules + Rule-10). Mirrors the gate's
# off-limits surfaces; the rollback path must not become an autonomous escape hatch.
FORBIDDEN_TARGET_SURFACES = (
    "release",
    "tag",
    "docker-stable",
    "stable-promotion",
    "stage2",
    "atomic-flip",
)
_FULL_SHA_LEN = 40
# Hard verifier-side debounce floor: a confirmed failure signal needs at least this
# many confirmations. The caller may require MORE, never fewer (so a caller cannot
# defeat debounce by passing required_confirmations=1). rco-1 #1389 trust-gap fix.
MIN_CONFIRMATIONS_FLOOR = 2


def _is_full_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _FULL_SHA_LEN
        and all(c in "0123456789abcdef" for c in value.lower())
    )


def is_known_green_consensus(sha: str, registry: Mapping[str, Mapping[str, Any]]) -> bool:
    """True iff ``sha`` is recorded as a prior CI-green AND consensus-approved state.

    Strict bool: a registry entry must have ``ci_green is True`` and
    ``consensus_approved is True`` -- string/None/missing never qualifies (no
    fail-open on a coerced truthy value).
    """
    if not _is_full_sha(sha) or not isinstance(registry, Mapping):
        return False
    entry = registry.get(sha)
    if not isinstance(entry, Mapping):
        return False
    return entry.get("ci_green") is True and entry.get("consensus_approved") is True


def _signal_confirmed(signal: Any) -> bool:
    """A failure signal is actionable only if explicitly CONFIRMED (debounced)."""
    if not isinstance(signal, Mapping):
        return False
    # confirmed must be a strict True, and at least the configured confirmations met.
    if signal.get("confirmed") is not True:
        return False
    required = signal.get("required_confirmations", MIN_CONFIRMATIONS_FLOOR)
    if not isinstance(required, int):
        return False
    # Enforce the hard floor: the caller may require MORE confirmations but never
    # fewer than MIN_CONFIRMATIONS_FLOOR (caller-supplied required cannot weaken it).
    effective_required = max(required, MIN_CONFIRMATIONS_FLOOR)
    observed = signal.get("observed_confirmations")
    return isinstance(observed, int) and observed >= effective_required


def decide_rollback(decision_input: Mapping[str, Any]) -> dict[str, Any]:
    """Decide auto_rollback vs operator_escalate for a proposed rollback.

    ``decision_input`` keys:
      offending_sha, target_sha : 40-char SHAs (the bad merge, the rollback target).
      result_tree, target_tree  : the tree-hash the rollback WOULD produce, and the
                                   target SHA's actual tree-hash (computed by the
                                   caller via git; the core only compares them).
      known_green_registry       : {sha: {ci_green: bool, consensus_approved: bool, ...}}.
      failure_signal             : {confirmed: bool, required_confirmations, observed_confirmations}.
      target_paths (optional)    : paths the rollback target touches (forbidden-surface check).
    """
    def escalate(reason: str) -> dict[str, Any]:
        return {
            "decision": OPERATOR_ESCALATE,
            "eligible": False,
            "reason": reason,
            "receipt": _receipt(decision_input, OPERATOR_ESCALATE, reason),
        }

    if not isinstance(decision_input, Mapping):
        return escalate("decision_input not a mapping -> escalate")

    offending = decision_input.get("offending_sha")
    target = decision_input.get("target_sha")
    if not _is_full_sha(offending):
        return escalate("offending_sha is not a full 40-char SHA -> escalate")
    if not _is_full_sha(target):
        return escalate("target_sha is not a full 40-char SHA -> escalate")
    if offending == target:
        return escalate("target_sha == offending_sha (no-op) -> escalate")

    # Forbidden surfaces: a rollback touching release/tag/stable/Rule-10 is operator-only.
    # target_paths is REQUIRED -- absence/None/non-Sequence/empty means we CANNOT verify
    # the target isn't a forbidden surface (a green release/tag SHA can be in the registry),
    # so we ESCALATE rather than silently skip the guard (rco-1 #1389 fail-open fix:
    # absence-of-evidence is not evidence-of-absence).
    paths = decision_input.get("target_paths")
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)) or not paths:
        return escalate("target_paths missing/invalid/empty -> cannot verify target surfaces -> escalate")
    for p in paths:
        low = str(p).lower()
        if any(s in low for s in FORBIDDEN_TARGET_SURFACES):
            return escalate(f"rollback touches forbidden surface ('{p}') -> operator-only")

    # The target must be a prior known-green + consensus-approved state.
    registry = decision_input.get("known_green_registry")
    if not isinstance(registry, Mapping):
        return escalate("known_green_registry missing/invalid -> escalate")
    if not is_known_green_consensus(target, registry):
        return escalate(
            "target_sha is not a known-green + consensus-approved state -> escalate"
        )

    # Pure revert: the rollback result tree must EQUAL the target tree (no new content).
    result_tree = decision_input.get("result_tree")
    target_tree = decision_input.get("target_tree")
    if not isinstance(result_tree, str) or not result_tree:
        return escalate("result_tree missing -> escalate")
    if not isinstance(target_tree, str) or not target_tree:
        return escalate("target_tree missing -> escalate")
    if result_tree != target_tree:
        return escalate(
            "result tree != target tree (rollback would introduce new content) -> escalate"
        )

    # The failure signal must be CONFIRMED/debounced (no thrash on a flaky run).
    if not _signal_confirmed(decision_input.get("failure_signal")):
        return escalate("failure signal not confirmed/debounced -> escalate")

    reason = "pure revert to a known-green+consensus SHA on a confirmed signal"
    return {
        "decision": AUTO_ROLLBACK,
        "eligible": True,
        "reason": reason,
        "receipt": _receipt(decision_input, AUTO_ROLLBACK, reason),
    }


def _receipt(decision_input: Any, decision: str, reason: str) -> dict[str, Any]:
    """Re-derivable MAGMA rollback receipt fields (a consumer re-derives the verdict)."""
    if not isinstance(decision_input, Mapping):
        return {"offending_sha": None, "target_sha": None, "result_tree": None,
                "target_tree": None, "decision": decision, "reason": reason, "trigger": None}
    return {
        "offending_sha": decision_input.get("offending_sha"),
        "target_sha": decision_input.get("target_sha"),
        "result_tree": decision_input.get("result_tree"),
        "target_tree": decision_input.get("target_tree"),
        "decision": decision,
        "reason": reason,
        "trigger": (decision_input.get("failure_signal") or {}).get("source")
        if isinstance(decision_input.get("failure_signal"), Mapping)
        else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-json", required=True, help="path to the decision-input JSON")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with open(args.decision_json, encoding="utf-8") as fh:
            decision_input = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read decision-json: {exc}", file=sys.stderr)
        return 2
    result = decide_rollback(decision_input)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['decision']}: {result['reason']}")
    return 0 if result["decision"] == AUTO_ROLLBACK else 3


if __name__ == "__main__":
    raise SystemExit(main())
