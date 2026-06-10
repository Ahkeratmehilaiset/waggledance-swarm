# SPDX-License-Identifier: BUSL-1.1
"""Log-only bridge-event signature auditor — HMAC Phase A.

Reads the bridge ``events.jsonl`` and reports, per recognized agent, how
many events are signature-``valid`` / ``invalid`` / ``unsigned`` /
``unverifiable`` under the per-agent keys in ``--key-dir`` (or the
``WD_AGENT_KEY_DIR`` environment variable).

PHASE A: this tool ONLY observes. It never blocks, never feeds a gate,
and emits ``enforcement_applied: false`` as a literal on every report.
Flipping any gate to require signatures is Phase B (operator-signed).

Also carries the two operator/integration helpers so a LATER
operator-signed ``Write-AgentEvent.ps1`` change is a one-line call:

- ``--init-key AGENT``  provision a new key file (never overwrites);
- ``--sign --agent ... --ts-utc ... --type ...``  print the
  ``payload.hmac`` JSON for the given identity fields.

Privacy: reports contain counts and agent ids only — never message text.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_hmac import (  # noqa: E402
    SIG_INVALID,
    SIG_UNSIGNED,
    SIG_UNVERIFIABLE,
    SIG_VALID,
    SIGNATURE_STATUSES,
    BridgeEventHmacError,
    generate_agent_key,
    load_agent_key,
    resolve_key_dir,
    sign_event_fields,
    verify_event_signature,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402

AUDIT_SCHEMA = "wd.bridge_event_signature_audit.v0"
RECOGNIZED_AGENTS = (
    "codex-lead-1",
    "codex-tools-1",
    "claude-rco-1",
    "claude-rco-2",
)


def build_signature_audit(
    *,
    events_path: Path,
    key_dir: Path | None,
    agents: Sequence[str],
    since: str = "",
) -> dict[str, Any]:
    """Aggregate log-only verification verdicts over events.jsonl."""
    resolved_dir = resolve_key_dir(key_dir)
    key_cache: dict[str, bytes | None] = {}

    def _key_lookup(agent: str) -> bytes | None:
        if agent not in key_cache:
            key_cache[agent] = load_agent_key(agent, resolved_dir)
        return key_cache[agent]

    agent_set = set(agents)
    per_agent: dict[str, dict[str, int]] = {
        agent: {status: 0 for status in SIGNATURE_STATUSES} | {"events": 0}
        for agent in sorted(agent_set)
    }
    scanned = 0
    parse_errors = 0
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"events file unreadable: {exc}") from exc
    for line in lines:
        if not line.strip():
            continue
        scanned += 1
        try:
            event = json.loads(line)
        except ValueError:
            parse_errors += 1
            continue
        if not isinstance(event, dict):
            parse_errors += 1
            continue
        agent = str(event.get("agent") or "")
        if agent not in agent_set:
            continue
        if since and str(event.get("ts_utc") or "") < since:
            continue
        verdict = verify_event_signature(event, _key_lookup)
        bucket = per_agent[agent]
        bucket["events"] += 1
        bucket[verdict["status"]] += 1

    totals = {status: 0 for status in SIGNATURE_STATUSES}
    audited = 0
    for bucket in per_agent.values():
        audited += bucket["events"]
        for status in SIGNATURE_STATUSES:
            totals[status] += bucket[status]
    signed = audited - totals[SIG_UNSIGNED]
    core: dict[str, Any] = {
        "schema_version": AUDIT_SCHEMA,
        "recognized_agents": sorted(agent_set),
        "since": since or None,
        "key_dir_present": resolved_dir is not None,
        "scanned_line_count": scanned,
        "parse_error_count": parse_errors,
        "audited_event_count": audited,
        "per_agent": per_agent,
        "totals": totals,
        "signed_count": signed,
        "signed_coverage_rate": (signed / audited) if audited else 0.0,
        "valid_rate_of_signed": (
            totals[SIG_VALID] / signed if signed else 0.0
        ),
        "invalid_signature_count": totals[SIG_INVALID],
        "unverifiable_count": totals[SIG_UNVERIFIABLE],
        # Phase A literals — this audit never gates anything.
        "enforcement_applied": False,
        "gate_consulted_this_report": False,
        "runtime_authority_granted": False,
    }
    return {**core, "canonical_digest": sha256_digest(core)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument("--key-dir", type=Path, default=None)
    parser.add_argument(
        "--agent",
        action="append",
        dest="agents",
        default=None,
        help="Recognized agent id (repeatable). Default: the 4-agent swarm.",
    )
    parser.add_argument("--since", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--init-key", default="")
    parser.add_argument("--sign", action="store_true")
    parser.add_argument("--ts-utc", default="")
    parser.add_argument("--type", dest="event_type", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--message", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.init_key:
        directory = resolve_key_dir(args.key_dir)
        if directory is None:
            print("--init-key requires --key-dir (or WD_AGENT_KEY_DIR)",
                  file=sys.stderr)
            return 2
        try:
            path = generate_agent_key(args.init_key, directory)
        except BridgeEventHmacError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps({"created": str(path), "agent": args.init_key}))
        return 0

    if args.sign:
        agents = args.agents or []
        if len(agents) != 1:
            print("--sign requires exactly one --agent", file=sys.stderr)
            return 2
        key = load_agent_key(agents[0], resolve_key_dir(args.key_dir))
        if key is None:
            print("--sign: no key available for agent", file=sys.stderr)
            return 2
        try:
            hmac_obj = sign_event_fields(
                key=key,
                agent=agents[0],
                ts_utc=args.ts_utc,
                event_type=args.event_type,
                status=args.status,
                task_id=args.task_id,
                message=args.message,
            )
        except BridgeEventHmacError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(hmac_obj, sort_keys=True))
        return 0

    if args.events is None:
        print("--events is required for an audit run", file=sys.stderr)
        return 2
    report = build_signature_audit(
        events_path=args.events,
        key_dir=args.key_dir,
        agents=args.agents or list(RECOGNIZED_AGENTS),
        since=args.since,
    )
    print(json.dumps(report, indent=2 if not args.json else None,
                     sort_keys=True))
    # Log-only: invalid signatures are REPORTED, never enforced (Phase A).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
