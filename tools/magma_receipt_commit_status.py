# SPDX-License-Identifier: BUSL-1.1
"""Offline MAGMA merge receipt -> GitHub commit-status payload (dormant, unwired, v1).

From a verified bridge-consensus merge receipt bundle (written by
``tools/write_bridge_consensus_merge_receipt.py``, checked by ``tools/verify_magma_receipt.py``)
this tool builds the exact commit status that carries the receipt digest onto the PR head
(and, after a squash merge, onto the merge commit via ``--status-sha``), and it re-derives
that status against one somebody else fetched. It never calls GitHub, never spawns a child
process and has no apply mode: the ``gh api`` line is TEXT for an operator-signed step. Any
other receipt shape fails closed with a named reason. Spec:
``docs/architecture/MERGE_RECEIPT_COMMIT_STATUS_V1.md``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402

STATUS_CONTEXT = "waggledance/magma-merge-receipt"
STATUS_STATE = "success"
RECEIPT_ARTIFACT_VERSION = "wd.bridge_consensus_merge_receipt.v0"
_ROLES = ("build_lead", "build_tools", "rco")
_COMPARED_FIELDS = ("state", "context", "description")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ReceiptStatusError(ValueError):
    """Fail-closed refusal; ``report`` carries decision, errors and exit_code."""

    def __init__(self, report: Mapping[str, Any]) -> None:
        super().__init__(str(report.get("decision", "refused")))
        self.report = dict(report)


def _refuse(decision: str, errors: Sequence[str], **extra: Any) -> None:
    raise ReceiptStatusError(
        {"ok": False, "decision": decision, "errors": list(errors), "exit_code": 2, **extra}
    )


def _require_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        _refuse("invalid_input", [f"{name} must be 40 lowercase hex characters"])
    return value


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _refuse("invalid_input", [f"cannot read {label}: {type(exc).__name__}"])
    return None  # unreachable; _refuse raises


def load_verified_receipt(manifest_path: Path) -> dict[str, Any]:
    """Verify the bundle with the shared verifier and bind to the one supported shape."""
    # Resolve first: verify_manifest resolves symlinks itself, so payload and receipt must
    # be read from the same (verified) directory, never from beside a symlink.
    manifest_path = Path(manifest_path).resolve()
    verifier = verify_manifest(manifest_path)
    if not verifier.get("ok", False):
        _refuse("receipt_verification_failed", [str(e) for e in verifier.get("errors", [])])
    manifest = _read_json(manifest_path, "manifest")
    entries = manifest.get("entries") if isinstance(manifest, Mapping) else None
    if not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], Mapping):
        _refuse("unsupported_receipt_shape", ["exactly one merge receipt entry expected"])
    entry = entries[0]
    payload = _read_json(manifest_path.parent / str(entry.get("payload", "")), "payload")
    receipt = _read_json(manifest_path.parent / str(entry.get("receipt", "")), "receipt")
    if not isinstance(payload, Mapping) or not isinstance(receipt, Mapping):
        _refuse("unsupported_receipt_shape", ["payload and receipt must be JSON objects"])
    if payload.get("artifact_version") != RECEIPT_ARTIFACT_VERSION:
        _refuse(
            "unsupported_receipt_shape",
            [f"payload artifact_version must be {RECEIPT_ARTIFACT_VERSION}"],
        )
    return {"payload": dict(payload), "receipt": dict(receipt), "manifest": str(manifest_path)}


def _identities(bridge_consensus: Mapping[str, Any]) -> dict[str, str]:
    errors: list[str] = []
    if bridge_consensus.get("ok") is not True or (
        bridge_consensus.get("decision") != "bridge_consensus_verified"
    ):
        errors.append("bridge_consensus is not a verified verdict")
    identities = bridge_consensus.get("identities")
    agents: dict[str, str] = {}
    if not isinstance(identities, Mapping):
        errors.append("bridge_consensus.identities missing")
        identities = {}
    for role in _ROLES:
        entry = identities.get(role)
        agent = str(entry.get("agent", "")) if isinstance(entry, Mapping) else ""
        if not agent:
            errors.append(f"identity {role} missing")
            continue
        if entry.get("approved") is not True:
            errors.append(f"identity {role} ({agent}) not approved")
        agents[role] = agent
    if len(set(agents.values())) != len(agents):
        errors.append("identities are not distinct")
    rco_ref = bridge_consensus.get("rco_pass_ref")
    rco_ref_agent = str(rco_ref.get("agent", "")) if isinstance(rco_ref, Mapping) else ""
    if not rco_ref_agent or rco_ref_agent != agents.get("rco", ""):
        errors.append("rco_pass_ref does not name the RCO identity")
    if errors:
        _refuse("missing_identities", errors)
    return agents


def build_commit_status(
    manifest_path: Path, *, expected_head: str, expected_base: str, status_sha: str | None = None
) -> dict[str, Any]:
    """Build the dry-run commit-status payload for a verified merge receipt.

    ``status_sha`` is the commit the status is posted on; it defaults to the PR head. A
    squash merge gives main a different SHA, so the same status is posted a second time
    on that merge commit with ``status_sha`` set to it; the description keeps the head.
    """
    head = _require_sha(expected_head, "expected_head")
    base = _require_sha(expected_base, "expected_base")
    target = _require_sha(status_sha, "status_sha") if status_sha is not None else head
    loaded = load_verified_receipt(Path(manifest_path))
    payload = loaded["payload"]
    if payload.get("head_sha") != head:
        _refuse("head_mismatch", [f"receipt head_sha is not {head}"])
    if payload.get("base_sha") != base:
        _refuse("base_mismatch", [f"receipt base_sha is not {base}"])
    flag_present = "autonomous_gate_pass_claimed" in payload
    flag = payload.get("autonomous_gate_pass_claimed")
    if flag_present and type(flag) is not bool:
        _refuse("autonomous_gate_pass_claimed_not_boolean", ["autonomous_gate_pass_claimed is present but not a boolean"])
    bridge_consensus = payload.get("bridge_consensus")
    if not isinstance(bridge_consensus, Mapping):
        _refuse("missing_identities", ["payload.bridge_consensus missing"])
    policy = payload.get("review_policy") or bridge_consensus.get("review_policy")
    if policy is not None:
        _refuse("unsupported_receipt_shape", [f"review_policy {policy!r} receipts are not supported in v1"])
    agents = _identities(bridge_consensus)
    author = str(bridge_consensus.get("author_agent") or "")
    if not author:
        _refuse("author_unknown", ["bridge_consensus.author_agent is empty; author vs voter cannot be checked"])
    if author in agents.values():
        _refuse("author_is_voter", [f"author {author} holds a voting slot"])
    repo = payload.get("repo")
    if not isinstance(repo, str) or not _REPO_RE.fullmatch(repo):
        _refuse("invalid_input", ["payload.repo must be owner/name"])
    digest = sha256_digest(loaded["receipt"])
    receipt_hex = digest.split(":", 1)[1]
    description = f"magma merge receipt {receipt_hex[:16]} rco={agents['rco']} head={head[:12]}"
    if target != head:
        description += f" merge={target[:12]}"
    status = {"state": STATUS_STATE, "context": STATUS_CONTEXT, "description": description, "target_url": None}
    command = shlex.join(
        [
            "gh", "api", "--method", "POST", f"repos/{repo}/statuses/{target}",
            "-f", f"state={STATUS_STATE}",
            "-f", f"context={STATUS_CONTEXT}",
            "-f", f"description={description}",
        ]
    )
    return {
        "ok": True,
        "decision": "commit_status_payload_built",
        "mode": "dry_run",
        "receipt_manifest": loaded["manifest"],
        "repo": repo,
        "pr_number": payload.get("pr_number"),
        "head_sha": head,
        "base_sha": base,
        "status_sha": target,
        "receipt_digest": digest,
        "receipt_sha256": receipt_hex,
        "identities": agents,
        "rco_agent": agents["rco"],
        "author_agent": author,
        "autonomous_gate_pass_claimed": flag if flag_present else None,
        "status": status,
        "gh_command_text": command,
    }


def verify_fetched_status(
    fetched_path: Path,
    manifest_path: Path,
    *,
    expected_head: str,
    expected_base: str,
    status_sha: str | None = None,
) -> dict[str, Any]:
    """Compare a fetched status with the re-derived one.

    Accepts one status object, the ``/commits/{sha}/statuses`` list (newest first, so a
    reposted history is allowed and the newest entry with our context is compared) or
    the combined ``/commits/{sha}/status`` object (one entry per context in ``statuses``).
    """
    built = build_commit_status(
        manifest_path, expected_head=expected_head, expected_base=expected_base, status_sha=status_sha
    )
    fetched = _read_json(Path(fetched_path), "fetched status")
    if isinstance(fetched, Mapping) and isinstance(fetched.get("statuses"), list):
        candidates = [item for item in fetched["statuses"] if isinstance(item, Mapping)]
    elif isinstance(fetched, Mapping):
        candidates = [fetched]
    elif isinstance(fetched, list):
        candidates = [item for item in fetched if isinstance(item, Mapping)]
    else:
        _refuse("invalid_input", ["fetched status must be a JSON object or list"])
        candidates = []
    matches = [item for item in candidates if item.get("context") == STATUS_CONTEXT]
    differences: list[dict[str, Any]] = []
    if not matches:
        differences.append(
            {"field": "context", "expected": STATUS_CONTEXT, "fetched": "no status with this context"}
        )
    else:
        newest = matches[0]
        for field in _COMPARED_FIELDS:
            if newest.get(field) != built["status"][field]:
                differences.append(
                    {"field": field, "expected": built["status"][field], "fetched": newest.get(field)}
                )
    return {
        "ok": not differences,
        "decision": "EQUAL" if not differences else "DIFFERENT",
        "mode": "dry_run",
        "head_sha": built["head_sha"],
        "status_sha": built["status_sha"],
        "receipt_sha256": built["receipt_sha256"],
        "expected_status": built["status"],
        "matching_statuses": len(matches),
        "differences": differences,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline: build (or re-derive) the GitHub commit status that carries a verified "
            "MAGMA merge receipt digest. Dry-run only; the gh command is printed as text."
        )
    )
    parser.add_argument("--receipt-manifest", required=True, type=Path)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-base", required=True)
    parser.add_argument(
        "--status-sha", default=None,
        help="commit the status is posted on when it is not the PR head (the squash merge commit)",
    )
    parser.add_argument(
        "--verify", type=Path, default=None,
        help="path of a status JSON fetched by someone else; reports EQUAL or DIFFERENT",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.verify is not None:
            report = verify_fetched_status(
                args.verify, args.receipt_manifest, expected_head=args.expected_head,
                expected_base=args.expected_base, status_sha=args.status_sha,
            )
            code = 0 if report["ok"] else 1
        else:
            report = build_commit_status(
                args.receipt_manifest, expected_head=args.expected_head,
                expected_base=args.expected_base, status_sha=args.status_sha,
            )
            code = 0
    except ReceiptStatusError as exc:
        report = exc.report
        code = int(report.get("exit_code", 2))
    print(json.dumps(report, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
