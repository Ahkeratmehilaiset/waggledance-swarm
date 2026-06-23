#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Lint a bridge status NAME for the phantom-block token class (pre-post check).

RFC: WD bridge throughput & resilience, item P2/D5. The autonomous-merge gate's
peer-veto classifier (``tools/check_bridge_changes_requested.py``) reads a
*blocking* signal from the free-text ``status`` NAME of ANY event type (blocks
are type-agnostic and fail-closed). So an agent that posts an otherwise-harmless
event whose status NAME merely CONTAINS block / changes-requested tokens (e.g.
a status like ``..._verify_block_cleared_...``) is misread as a fresh peer veto
and wedges the PR until the SAME agent posts an explicit clear -- a
self-inflicted wall-loop (observed: fable-5 self-block on PR #1368, 2026-06-22).

This tool lets an agent check a candidate status NAME BEFORE posting it. It is a
thin, FAITHFUL wrapper over the live classifier predicate (it imports
``_is_blocking_status`` from ``check_bridge_changes_requested``), so its verdict
can never drift from the gate's real behavior. It is pure-string and read-only:
no bridge access, no network, no mutation.

    python tools/check_status_name_safe.py --status fable_pr_verify_block_cleared --json

Exit codes:
  0  status name is safe (the gate will NOT read it as a peer block)
  3  status name WOULD be read as a block (rephrase before posting)
  2  argument error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_bridge_changes_requested import (  # noqa: E402
    BLOCKING_STATUSES,
    BLOCKING_WORD_TOKENS,
    _is_blocking_status,
    _is_clear_status,
    _status_tokens,
)

CHANGES_REQUESTED_PREFIXES = ("changes_requested", "rco_changes_requested")


def _normalize(status: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", status.lower()).strip("_")


def _triggers(status: str) -> list[str]:
    """Human-readable reasons the gate classifies this status NAME as blocking.

    Best-effort explanation only; the authoritative verdict is ``would_block``
    (from the imported classifier predicate). Surfaced so an agent can see WHICH
    token to remove.
    """
    reasons: list[str] = []
    normalized = _normalize(status)
    if normalized in BLOCKING_STATUSES:
        reasons.append(f"exact blocking status: {normalized}")
    for prefix in CHANGES_REQUESTED_PREFIXES:
        if normalized == prefix or normalized.startswith(prefix + "_"):
            reasons.append(f"changes-requested prefix: {prefix}")
            break
    tokens = _status_tokens(status)
    if {"changes", "requested"}.issubset(tokens):
        reasons.append("contains both 'changes' and 'requested' tokens")
    hit = sorted(tokens.intersection(BLOCKING_WORD_TOKENS))
    if hit:
        reasons.append(f"contains blocking word token(s): {', '.join(hit)}")
    if not reasons:
        # would_block True with no enumerated reason -> still warn explicitly.
        reasons.append("classifier reads this status NAME as blocking")
    return reasons


def check_status_name(status: str) -> dict:
    """Return whether a status NAME is safe to post (won't be read as a block)."""
    status = status or ""
    would_block = _is_blocking_status(status)
    result: dict = {
        "status": status,
        "safe": not would_block,
        "would_block": would_block,
        "is_clear_form": _is_clear_status(status),
        "triggers": _triggers(status) if would_block else [],
    }
    if would_block:
        result["reason"] = (
            "the peer-veto classifier would read this status NAME as a fresh "
            "block (type-agnostic, fail-closed) and wedge the PR"
        )
        result["suggestion"] = (
            "rephrase the status NAME without block / blocked / blocking or the "
            "'changes'+'requested' pair; if you intend to CLEAR a prior block, "
            "post type=decision with an explicit clear status (e.g. "
            "no_changes_requested / build_consensus_pass / rco_pass)"
        )
    else:
        result["reason"] = "the classifier will not read this status NAME as a block"
        result["suggestion"] = ""
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", required=True, help="candidate status NAME")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.status or not args.status.strip():
        print("--status must not be empty", file=sys.stderr)
        return 2
    result = check_status_name(args.status)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["safe"]:
        print(f"SAFE: {args.status}")
    else:
        print(f"WOULD BLOCK: {args.status}", file=sys.stderr)
        for trigger in result["triggers"]:
            print(f"  - {trigger}", file=sys.stderr)
        print(f"  suggestion: {result['suggestion']}", file=sys.stderr)
    return 0 if result["safe"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
