# SPDX-License-Identifier: BUSL-1.1
"""Receipt evidence contract for the manual (a)-class merge route (MANUAL-A).

Second slice of the manual merge + MAGMA receipt route (operator decision
8A480508, Lead plan F9D23F10).  It assembles and verifies a local receipt
directory for ONE manual merge.  The caller hands over raw evidence (statement
bytes, signature bytes, the original bridge event objects, raw ``gh`` output)
and this module re-derives everything it can itself:

* the detached operator statement is re-parsed and its SSHSIG signature is
  re-verified with ``ssh-keygen -Y verify`` against the ``allowed_signers``
  anchor loaded from the statement's own trusted base commit (public API of
  ``tools.manual_bridge_merge_statement``); a caller-supplied verification
  object is never accepted;
* the recognized non-author RCO_PASS and the separate Lead and Tools
  ``build_consensus_pass`` decisions must be exact, structurally head-bound and
  on the canonical task id; their bridge ``event_id`` is recomputed here;
* the autonomous refusal is either the original event (preserved unchanged) or
  an explicit UNKNOWN absence note; this module never builds a refusal;
* the GitHub merge result is the raw ``gh pr view --json`` output, parsed
  strictly and bound to the same PR and head;
* the statement nonce must have reached a merged terminal state in the
  statement-module ledger.

The SSHSIG signature is NOT a MAGMA ``signature_envelope`` (that field holds a
raw Ed25519 / ML-DSA signature over the receipt itself).  The receipt keeps its
envelope null and preserves the detached statement and signature as separate
raw artifacts.  Raw-byte SHA-256 values (plain ``hex``) and MAGMA canonical
JSON digests (``sha256:<hex>``) are kept in separate, labelled fields.

What this module does NOT establish (integration prerequisites; they stay
UNKNOWN until a later reviewed slice wires real validators): presence and order
of the events in the canonical bridge log, agent-UUID binding against the
trusted identity registry, the canonical author / contributor lineage, absence
of a later recognized-RCO veto (DN-A) and the authenticity of the GitHub merge
result.  A GENUINE receipt is therefore always refused in this slice
(``integration_prerequisites_unverified``).  Tests can only write receipts that
are labelled ``synthetic_unit_mock`` inside the digest-bound payload.

Writing is append-only: the receipt directory is created exclusively, nothing
is overwritten, and a directory without the completion marker is an unaccepted
failure artifact that must be reconciled, never retried.  The writer reports
success only after the finished directory re-verifies complete and the marker
reads back exactly as written; a failed write can still leave a marker that
verifies, so reconciliation always re-runs the verifier.  Importing this module
has no side effects beyond the repository-root ``sys.path`` entry that every
``tools`` module uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.manual_bridge_merge_statement import (  # noqa: E402
    ALLOWED_SIGNERS_PATH,
    NAMESPACE,
    PRINCIPAL,
    PURPOSE,
    NonceLedger,
    Runner,
    Statement,
    StatementError,
    TrustAnchor,
    VerificationResult,
    VerifiedStatement,
    load_trust_anchor,
    parse_expiry,
    parse_statement,
    require_genuine_provenance,
    statement_sha256,
    verify_statement_signature,
)
from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402
from waggledance.core.magma.receipt_bundle import (  # noqa: E402
    ReceiptBundleEntry,
    write_receipt_bundle,
)

RECEIPT_PAYLOAD_SCHEMA = "wd.manual-merge-a.receipt-payload.v1"
COMPLETION_SCHEMA = "wd.manual-merge-a.receipt-complete.v1"
COMPLETION_MARKER = "RECEIPT-COMPLETE.json"
EVIDENCE_DIR = "evidence"
MAGMA_DIR = "magma"
BUNDLE_LABEL = "manual-merge"
AUTHORITY_DECISION_SHA256 = "8a480508f633106473e741976e5ef7e30215cb5c113988b9b95de1eed1b83c6a"

LEAD_AGENT = "codex-lead-1"
TOOLS_AGENT = "codex-tools-1"
RECOGNIZED_RCO_AGENTS: tuple[str, ...] = ("claude-rco-1", "claude-rco-2")
DECISION_TYPE = "decision"
RCO_PASS_STATUS = "rco_pass"
BUILD_CONSENSUS_STATUS = "build_consensus_pass"
# Every structured head field any base reader consults; all present ones must
# equal the head.  Prose never binds.
STRUCTURED_HEAD_KEYS: tuple[str, ...] = (
    "exact_head",
    "head",
    "head_sha",
    "expected_head",
    "head_oid",
    "head_commit",
)
APPROVAL_ROLES: tuple[str, ...] = ("rco", "build_lead", "build_tools")
MERGED_NONCE_STATES = frozenset({"executed", "reconciled_merged"})
REFUSAL_STATES: tuple[str, ...] = ("recorded", "absent_unknown")
PR_PAYLOAD_KEYS: tuple[str, ...] = ("pr", "pr_number", "pull_request", "pull_request_number")
GH_VIEW_FIELDS: tuple[str, ...] = (
    "baseRefName",
    "headRefName",
    "headRefOid",
    "mergeCommit",
    "mergedAt",
    "number",
    "state",
)
BASE_REF_NAME = "main"

EVIDENCE_GENUINE = "genuine"
EVIDENCE_SYNTHETIC = "synthetic_unit_mock"
INTEGRATION_PREREQUISITES: tuple[str, ...] = (
    "bridge_event_provenance",
    "identity_registry_binding",
    "author_lineage",
    "rco_veto_admission",
    "github_merge_authenticity",
)
PREREQUISITE_UNKNOWN = "unknown_not_established_by_receipt_module"

ARTIFACT_FILES: tuple[str, ...] = (
    "evidence/statement.json",
    "evidence/statement.sig",
    "evidence/approval-events.jsonl",
    "evidence/autonomous-refusal.json",
    "evidence/merge-result.json",
    "evidence/merge-result-command.json",
    "evidence/nonce-ledger.json",
)
MAGMA_FILES: tuple[str, ...] = (
    f"payload-001-{BUNDLE_LABEL}.json",
    f"evaluation-001-{BUNDLE_LABEL}.json",
    f"receipt-001-{BUNDLE_LABEL}.json",
    "manifest.json",
)
# Exact key sets of the v1 completion marker and of its ``magma`` object.
MARKER_KEYS: tuple[str, ...] = (
    "schema",
    "evidence_class",
    "genuine",
    "receipt_dir_name",
    "chain_id",
    "artifacts",
    "magma",
    "verified_at_utc",
)
MARKER_MAGMA_KEYS: tuple[str, ...] = (
    "payload_digest",
    "evaluation_result_digest",
    "receipt_digest",
    "manifest_raw_sha256",
)

LIMITS: tuple[str, ...] = (
    "The shared GitHub account can still merge outside this route; the receipt evidences, it does not prevent.",
    "ssh-keygen -Y verify cannot distinguish a FIDO touch from a passphrase key; it proves possession of the anchored key only.",
    "The nonce ledger is local state; deleting it is not cryptographically prevented.",
    "Integration prerequisites named in this payload are UNKNOWN unless marked otherwise.",
)

SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
EVENT_TS_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?Z"
)
GH_TS_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z")
MAX_NOTE_CHARS = 500
MAX_GH_BYTES = 256 * 1024


class ReceiptError(ValueError):
    """Fail-closed refusal with a stable reason code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class AutonomousRefusalEvidence:
    """The original autonomous refusal event, or an explicit UNKNOWN absence."""

    state: str
    event: Mapping[str, Any] | None = None
    absence_note: str = ""


@dataclass(frozen=True)
class MergeResultEvidence:
    """Raw ``gh pr view <n> --json ...`` invocation and its unmodified stdout."""

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes


@dataclass(frozen=True)
class ReceiptEvidence:
    statement_bytes: bytes
    signature_bytes: bytes
    canonical_task_id: str
    author_agents: tuple[str, ...]
    rco_pass_event: Mapping[str, Any]
    lead_build_event: Mapping[str, Any]
    tools_build_event: Mapping[str, Any]
    autonomous_refusal: AutonomousRefusalEvidence
    merge_result: MergeResultEvidence


@dataclass(frozen=True)
class ReceiptAssessment:
    statement: Statement
    statement_sha256: str
    signature_sha256: str
    anchor: TrustAnchor
    verification: VerificationResult
    evidence_class: str
    approvals: Mapping[str, Mapping[str, Any]]
    approval_lines: tuple[bytes, ...]
    refusal: Mapping[str, Any]
    refusal_bytes: bytes
    merge: Mapping[str, Any]
    merge_command_bytes: bytes
    ledger_records: tuple[Mapping[str, Any], ...]
    integration_prerequisites: Mapping[str, str]


# --- digests -----------------------------------------------------------------


def raw_sha256(data: bytes) -> str:
    """Plain hex SHA-256 of raw bytes (never a MAGMA ``sha256:`` digest)."""
    return hashlib.sha256(data).hexdigest()


def bridge_event_bytes(event: Mapping[str, Any]) -> bytes:
    """Canonical bytes of a bridge event in the ``tools/bridge_compact_view`` domain."""
    try:
        text = json.dumps(
            event, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ReceiptError("event_not_canonical_json", type(exc).__name__) from exc
    return text.encode("utf-8")


def bridge_event_id(event: Mapping[str, Any]) -> str:
    """Same digest as ``tools.bridge_compact_view.event_id`` (drift-guarded in tests)."""
    return raw_sha256(bridge_event_bytes(event))


def _canonical_json_line(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError("duplicate_key", key)
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise ReceiptError("non_canonical_json", f"JSON constant {token} is not allowed")


def _strict_json(data: bytes, reason: str) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReceiptError(reason, "invalid UTF-8") from exc
    if text.startswith("﻿"):
        raise ReceiptError(reason, "BOM is not allowed")
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except ReceiptError as exc:
        raise ReceiptError(reason, exc.reason) from exc
    except ValueError as exc:
        raise ReceiptError(reason, "invalid JSON") from exc


def _require_utc_clock(now_utc: Any) -> datetime:
    if (
        not isinstance(now_utc, datetime)
        or now_utc.tzinfo is None
        or now_utc.utcoffset() != timedelta(0)
    ):
        raise ReceiptError("invalid_clock", "now_utc must be an aware UTC datetime")
    return now_utc


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_event_ts(value: Any, reason: str) -> datetime:
    """Parse a bridge ``ts_utc`` (``Z``, up to 9 fractional digits; extra digits truncated)."""
    if type(value) is not str:
        raise ReceiptError(reason, "ts_utc must be a string")
    match = EVENT_TS_RE.fullmatch(value)
    if match is None:
        raise ReceiptError(reason, "ts_utc is not an exact UTC timestamp")
    parts = [int(part) for part in match.groups()[:6]]
    micros = int((match.group(7) or "").ljust(6, "0")[:6])
    try:
        return datetime(*parts, micros, tzinfo=timezone.utc)
    except ValueError as exc:
        raise ReceiptError(reason, "ts_utc is not a real time") from exc


# --- approvals ---------------------------------------------------------------


def _check_approval(
    event: Any,
    *,
    role: str,
    expected_agents: Sequence[str],
    expected_status: str,
    head_sha: str,
    task_id: str,
    author_agents: Sequence[str],
) -> tuple[dict[str, Any], bytes]:
    if not isinstance(event, Mapping):
        raise ReceiptError(f"approval_missing:{role}", "event must be an object")
    line = bridge_event_bytes(event)
    for key in ("agent", "agent_uuid", "type", "status", "task_id", "ts_utc"):
        if type(event.get(key)) is not str:
            raise ReceiptError(f"approval_invalid:{role}", f"{key} must be a string")
    agent = event["agent"]
    if agent not in expected_agents:
        raise ReceiptError(f"approval_agent_invalid:{role}", agent)
    if not event["agent_uuid"]:
        raise ReceiptError(f"approval_invalid:{role}", "agent_uuid is empty")
    if event["type"] != DECISION_TYPE:
        raise ReceiptError(f"approval_type_invalid:{role}", event["type"])
    if event["status"] != expected_status:
        raise ReceiptError(f"approval_status_invalid:{role}", event["status"])
    if event["task_id"] != task_id:
        raise ReceiptError(f"approval_task_mismatch:{role}", event["task_id"])
    _parse_event_ts(event["ts_utc"], f"approval_invalid:{role}")
    if role == "rco" and agent in author_agents:
        raise ReceiptError("rco_is_author", agent)
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise ReceiptError(f"approval_head_unbound:{role}", "no structured payload")
    present = [key for key in STRUCTURED_HEAD_KEYS if key in payload]
    if not present or (role == "rco" and "exact_head" not in present):
        raise ReceiptError(f"approval_head_unbound:{role}", "no structured head field")
    for key in present:
        if payload[key] != head_sha:
            raise ReceiptError(f"approval_head_mismatch:{role}", key)
    summary = {
        "role": role,
        "agent": agent,
        "agent_uuid": event["agent_uuid"],
        "type": event["type"],
        "status": event["status"],
        "task_id": event["task_id"],
        "ts_utc": event["ts_utc"],
        "bound_head": head_sha,
        "event_id": raw_sha256(line),
    }
    return summary, line


def assess_approvals(
    *,
    rco_pass_event: Any,
    lead_build_event: Any,
    tools_build_event: Any,
    head_sha: str,
    task_id: str,
    author_agents: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], tuple[bytes, ...]]:
    """Exact RCO_PASS plus BOTH actual build votes; no author waiver, no token rules."""
    if type(task_id) is not str or not task_id:
        raise ReceiptError("canonical_task_id_missing")
    if isinstance(author_agents, (str, bytes)) or not isinstance(author_agents, Sequence) or not author_agents:
        raise ReceiptError("author_agents_missing", "the author set is required")
    if any(type(agent) is not str or not agent for agent in author_agents):
        raise ReceiptError("author_agents_missing", "author identities must be strings")
    checks = (
        ("rco", rco_pass_event, RECOGNIZED_RCO_AGENTS, RCO_PASS_STATUS),
        ("build_lead", lead_build_event, (LEAD_AGENT,), BUILD_CONSENSUS_STATUS),
        ("build_tools", tools_build_event, (TOOLS_AGENT,), BUILD_CONSENSUS_STATUS),
    )
    approvals: dict[str, dict[str, Any]] = {}
    lines: list[bytes] = []
    for role, event, agents, status in checks:
        summary, line = _check_approval(
            event,
            role=role,
            expected_agents=agents,
            expected_status=status,
            head_sha=head_sha,
            task_id=task_id,
            author_agents=author_agents,
        )
        approvals[role] = summary
        lines.append(line)
    uuids = [approvals[role]["agent_uuid"] for role in APPROVAL_ROLES]
    if len(set(uuids)) != len(uuids):
        raise ReceiptError("approval_identity_duplicate", "agent_uuid repeated")
    ids = [approvals[role]["event_id"] for role in APPROVAL_ROLES]
    if len(set(ids)) != len(ids):
        raise ReceiptError("approval_identity_duplicate", "event repeated")
    return approvals, tuple(lines)


# --- autonomous refusal ------------------------------------------------------


def _event_mentions_pr(event: Mapping[str, Any], pull_request: int) -> bool:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return False
    for key in PR_PAYLOAD_KEYS:
        value = payload.get(key)
        if type(value) is int and value == pull_request:
            return True
    return False


def assess_refusal(
    refusal: Any, *, task_id: str, pull_request: int
) -> tuple[dict[str, Any], bytes]:
    """Preserve the original refusal or an explicit UNKNOWN absence; never build one."""
    if not isinstance(refusal, AutonomousRefusalEvidence):
        raise ReceiptError("refusal_evidence_missing")
    if refusal.state not in REFUSAL_STATES:
        raise ReceiptError("refusal_state_invalid", str(refusal.state))
    if refusal.state == "absent_unknown":
        note = refusal.absence_note
        if refusal.event is not None:
            raise ReceiptError("refusal_evidence_contradictory", "absence with an event")
        if (
            type(note) is not str
            or not note.strip()
            or len(note) > MAX_NOTE_CHARS
            or any(ord(char) < 0x20 or ord(char) > 0x7E for char in note)
        ):
            raise ReceiptError("refusal_absence_note_invalid")
        record = {"state": "absent_unknown", "absence_note": note, "event_id": None}
        return record, _canonical_json_line(record)
    event = refusal.event
    if not isinstance(event, Mapping):
        raise ReceiptError("refusal_evidence_contradictory", "recorded without an event")
    if refusal.absence_note:
        raise ReceiptError("refusal_evidence_contradictory", "recorded with an absence note")
    line = bridge_event_bytes(event)
    for key in ("agent", "type", "status", "task_id", "ts_utc"):
        if type(event.get(key)) is not str:
            raise ReceiptError("refusal_event_invalid", f"{key} must be a string")
    _parse_event_ts(event["ts_utc"], "refusal_event_invalid")
    if event["task_id"] != task_id and not _event_mentions_pr(event, pull_request):
        raise ReceiptError("refusal_event_scope_mismatch", event["task_id"])
    record = {
        "state": "recorded",
        "event_id": raw_sha256(line),
        "agent": event["agent"],
        "type": event["type"],
        "status": event["status"],
        "task_id": event["task_id"],
        "ts_utc": event["ts_utc"],
        "classification": "preserved_unchanged_not_classified_by_receipt_module",
    }
    return record, line


# --- GitHub merge result -----------------------------------------------------


def _parse_gh_time(value: Any, label: str) -> datetime:
    if type(value) is not str:
        raise ReceiptError("merge_result_invalid", f"{label} must be a string")
    match = GH_TS_RE.fullmatch(value)
    if match is None:
        raise ReceiptError("merge_result_invalid", f"{label} must be YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime(*(int(part) for part in match.groups()), tzinfo=timezone.utc)
    except ValueError as exc:
        raise ReceiptError("merge_result_invalid", f"{label} is not a real time") from exc


def assess_merge_result(
    merge: Any, *, statement: Statement, task_id: str
) -> tuple[dict[str, Any], bytes]:
    """Strictly bind the raw ``gh pr view --json`` output to the signed PR, head and branch."""
    if not isinstance(merge, MergeResultEvidence):
        raise ReceiptError("merge_result_missing")
    argv = merge.argv
    if type(argv) is not tuple or not argv or any(type(arg) is not str or not arg for arg in argv):
        raise ReceiptError("merge_result_invalid", "argv must be a non-empty tuple of strings")
    if type(merge.returncode) is not int:
        raise ReceiptError("merge_result_invalid", "returncode must be an int")
    if merge.returncode != 0:
        raise ReceiptError("merge_result_ambiguous", f"gh exit {merge.returncode}; reconcile, do not retry")
    if not isinstance(merge.stdout, bytes) or not merge.stdout or len(merge.stdout) > MAX_GH_BYTES:
        raise ReceiptError("merge_result_ambiguous", "empty or oversized gh output")
    decoded = _strict_json(merge.stdout, "merge_result_invalid")
    if not isinstance(decoded, dict):
        raise ReceiptError("merge_result_invalid", "gh output must be a JSON object")
    missing = [field for field in GH_VIEW_FIELDS if field not in decoded]
    if missing:
        raise ReceiptError("merge_result_invalid", "missing " + ",".join(missing))
    number = decoded["number"]
    if type(number) is not int or number != statement.pull_request:
        raise ReceiptError("merge_result_pr_mismatch", str(number))
    if decoded["state"] != "MERGED":
        raise ReceiptError("merge_result_not_merged", str(decoded["state"]))
    if decoded["headRefOid"] != statement.head_sha:
        raise ReceiptError("merge_result_head_mismatch", str(decoded["headRefOid"]))
    # The canonical bridge task id is the PR branch name.
    if decoded["headRefName"] != task_id:
        raise ReceiptError("merge_result_task_mismatch", str(decoded["headRefName"]))
    if decoded["baseRefName"] != BASE_REF_NAME:
        raise ReceiptError("merge_result_base_mismatch", str(decoded["baseRefName"]))
    commit = decoded["mergeCommit"]
    oid = commit.get("oid") if isinstance(commit, dict) else None
    if type(oid) is not str or SHA1_RE.fullmatch(oid) is None:
        raise ReceiptError("merge_result_invalid", "mergeCommit.oid must be a 40-hex sha")
    if oid in (statement.head_sha, statement.base_sha):
        raise ReceiptError("merge_result_invalid", "merge commit equals head or base")
    merged_at = _parse_gh_time(decoded["mergedAt"], "mergedAt")
    if merged_at >= parse_expiry(statement.expires_at_utc):
        raise ReceiptError("merged_after_statement_expiry", decoded["mergedAt"])
    summary = {
        "argv": list(argv),
        "returncode": merge.returncode,
        "raw_sha256": raw_sha256(merge.stdout),
        "number": number,
        "state": decoded["state"],
        "head_ref_oid": decoded["headRefOid"],
        "head_ref_name": decoded["headRefName"],
        "base_ref_name": decoded["baseRefName"],
        "merge_commit_oid": oid,
        "merged_at": decoded["mergedAt"],
    }
    command = _canonical_json_line({"argv": list(argv), "returncode": merge.returncode})
    return summary, command


# --- nonce ledger ------------------------------------------------------------


def assess_ledger(
    ledger: Any, *, statement: Statement, statement_digest: str
) -> tuple[dict[str, Any], ...]:
    """Require the statement's nonce to be in a merged terminal state."""
    if not isinstance(ledger, NonceLedger):
        raise ReceiptError("ledger_missing")
    try:
        history = ledger.history(statement.nonce)
    except StatementError as exc:
        raise ReceiptError(f"ledger:{exc.reason}", exc.detail) from exc
    if not history:
        raise ReceiptError("nonce_not_reserved", statement.nonce)
    final_state = history[-1].to_state
    if final_state not in MERGED_NONCE_STATES:
        raise ReceiptError("nonce_not_merged", final_state)
    records: list[dict[str, Any]] = []
    for record in history:
        if (
            record.statement_sha256 != statement_digest
            or record.pull_request != statement.pull_request
            or record.head_sha != statement.head_sha
            or record.base_sha != statement.base_sha
            or record.batch_id != statement.batch_id
        ):
            raise ReceiptError("ledger_binding_mismatch", f"seq {record.seq}")
        records.append(
            {
                "seq": record.seq,
                "from": record.from_state,
                "to": record.to_state,
                "ts_utc": record.ts_utc,
            }
        )
    if records[0]["to"] != "reserved" or "merge_started" not in [row["to"] for row in records]:
        raise ReceiptError("ledger_binding_mismatch", "merge was never started")
    return tuple(records)


# --- assessment --------------------------------------------------------------


def assess_receipt_evidence(
    evidence: ReceiptEvidence,
    *,
    repo_root: Path,
    ssh_keygen: Path,
    ledger: NonceLedger,
    runner: Runner | None = None,
    git_runner: Runner | None = None,
    git_executable: str = "git",
) -> ReceiptAssessment:
    """Re-derive every locally checkable fact; read-only (no receipt is written)."""
    if not isinstance(evidence, ReceiptEvidence):
        raise ReceiptError("evidence_missing")
    try:
        statement = parse_statement(evidence.statement_bytes)
        anchor = load_trust_anchor(
            repo_root=repo_root,
            trusted_commit=statement.base_sha,
            runner=git_runner,
            git_executable=git_executable,
        )
        if statement.allowed_signers_blob_sha != anchor.blob_sha:
            raise StatementError("anchor_blob_mismatch", anchor.blob_sha)
        if statement.key_fingerprint != anchor.fingerprint:
            raise StatementError("key_fingerprint_mismatch", anchor.fingerprint)
        verification = verify_statement_signature(
            statement_bytes=evidence.statement_bytes,
            signature_bytes=evidence.signature_bytes,
            anchor=anchor,
            ssh_keygen=ssh_keygen,
            runner=runner,
        )
    except StatementError as exc:
        raise ReceiptError(f"statement:{exc.reason}", exc.detail) from exc
    digest = statement_sha256(evidence.statement_bytes)
    if verification.statement_sha256 != digest:
        raise ReceiptError("statement:verification_binding_mismatch")
    genuine = (
        runner is None
        and git_runner is None
        and verification.evidence_class == "subprocess_ssh_keygen"
    )
    if genuine:
        # Runner-free is not enough on its own: the statement module's single
        # provenance predicate must also accept the anchor and verification
        # derived above (never caller-supplied).  Necessary, never sufficient.
        try:
            require_genuine_provenance(
                VerifiedStatement(
                    statement=statement, statement_sha256=digest, anchor=anchor, verification=verification
                )
            )
        except StatementError as exc:
            raise ReceiptError(f"statement:{exc.reason}", exc.detail) from exc
    author_agents = evidence.author_agents
    if type(author_agents) is not tuple:
        raise ReceiptError("author_agents_missing", "author_agents must be a tuple")
    approvals, approval_lines = assess_approvals(
        rco_pass_event=evidence.rco_pass_event,
        lead_build_event=evidence.lead_build_event,
        tools_build_event=evidence.tools_build_event,
        head_sha=statement.head_sha,
        task_id=evidence.canonical_task_id,
        author_agents=author_agents,
    )
    refusal, refusal_bytes = assess_refusal(
        evidence.autonomous_refusal,
        task_id=evidence.canonical_task_id,
        pull_request=statement.pull_request,
    )
    merge, merge_command = assess_merge_result(
        evidence.merge_result, statement=statement, task_id=evidence.canonical_task_id
    )
    # An approval or refusal recorded at or after the merged second cannot
    # have preceded the effect (GitHub reports whole seconds; fail closed).
    merged_at = _parse_gh_time(merge["merged_at"], "mergedAt")
    for role in APPROVAL_ROLES:
        if _parse_event_ts(approvals[role]["ts_utc"], f"approval_invalid:{role}") >= merged_at:
            raise ReceiptError(f"approval_after_merge:{role}", approvals[role]["ts_utc"])
    if refusal["state"] == "recorded" and _parse_event_ts(refusal["ts_utc"], "refusal_event_invalid") >= merged_at:
        raise ReceiptError("refusal_after_merge", refusal["ts_utc"])
    ledger_records = assess_ledger(ledger, statement=statement, statement_digest=digest)
    return ReceiptAssessment(
        statement=statement,
        statement_sha256=digest,
        signature_sha256=verification.signature_sha256,
        anchor=anchor,
        verification=verification,
        evidence_class=EVIDENCE_GENUINE if genuine else EVIDENCE_SYNTHETIC,
        approvals=approvals,
        approval_lines=approval_lines,
        refusal=refusal,
        refusal_bytes=refusal_bytes,
        merge=merge,
        merge_command_bytes=merge_command,
        ledger_records=ledger_records,
        integration_prerequisites={name: PREREQUISITE_UNKNOWN for name in INTEGRATION_PREREQUISITES},
    )


def _receipt_mode_decision(*, evidence_class: str, synthetic_fixture: bool, prerequisites: Mapping[str, str]) -> str:
    """Return the only writable label, or refuse.

    ``prerequisites`` comes only from ``assess_receipt_evidence`` (never from a
    caller).  Genuine evidence needs every integration prerequisite
    established, which no code in this slice does; synthetic evidence is
    written only on explicit request and is labelled inside the digest-bound
    payload.
    """
    if type(synthetic_fixture) is not bool:
        raise ReceiptError("receipt_mode_invalid", "synthetic_fixture must be a bool")
    if evidence_class == EVIDENCE_GENUINE:
        if synthetic_fixture:
            raise ReceiptError("receipt_mode_invalid", "genuine evidence cannot be relabelled synthetic")
        unresolved = sorted(name for name in INTEGRATION_PREREQUISITES if prerequisites.get(name) != "verified")
        if unresolved:
            raise ReceiptError("integration_prerequisites_unverified", ",".join(unresolved))
        return EVIDENCE_GENUINE
    if evidence_class == EVIDENCE_SYNTHETIC:
        if not synthetic_fixture:
            raise ReceiptError("evidence_not_genuine", "unit/mock verifier evidence")
        return EVIDENCE_SYNTHETIC
    raise ReceiptError("receipt_mode_invalid", str(evidence_class))


# --- payload and MAGMA triple --------------------------------------------------


def _raw_ref(hex_value: str) -> dict[str, str]:
    return {"domain": "raw_bytes", "algorithm": "sha256", "hex": hex_value}


def build_receipt_payload(
    assessment: ReceiptAssessment,
    *,
    evidence: ReceiptEvidence,
    label: str,
    artifacts: Mapping[str, str],
) -> dict[str, Any]:
    statement = assessment.statement
    verification = assessment.verification
    anchor = assessment.anchor
    return {
        "schema": RECEIPT_PAYLOAD_SCHEMA,
        "evidence_class": label,
        "genuine": label == EVIDENCE_GENUINE,
        "authority": {"operator_decision_sha256": AUTHORITY_DECISION_SHA256},
        "repository": statement.repository,
        "pull_request": statement.pull_request,
        "head_sha": statement.head_sha,
        "base_sha": statement.base_sha,
        "diff_digest_sha256": statement.diff_digest_sha256,
        "exact_paths": list(statement.exact_paths),
        "merge_method": statement.merge_method,
        "batch_id": statement.batch_id,
        "batch_order": statement.batch_order,
        "dependencies": list(statement.dependencies),
        "operation_scope": statement.operation_scope,
        "expires_at_utc": statement.expires_at_utc,
        "nonce": statement.nonce,
        "canonical_task_id": evidence.canonical_task_id,
        "author_agents": list(evidence.author_agents),
        "statement": {
            "raw_sha256": _raw_ref(assessment.statement_sha256),
            "signature_raw_sha256": _raw_ref(assessment.signature_sha256),
            "signature_format": "sshsig_armored_detached",
            "magma_signature_envelope": None,
            "namespace": NAMESPACE,
            "principal": PRINCIPAL,
            "purpose": PURPOSE,
            "anchor": {
                "trusted_commit": anchor.trusted_commit,
                "path": ALLOWED_SIGNERS_PATH,
                "blob_sha": anchor.blob_sha,
                "data_raw_sha256": _raw_ref(anchor.data_sha256),
                "key_type": anchor.key_type,
                "key_fingerprint": anchor.fingerprint,
            },
            "verification": {
                "evidence_class": verification.evidence_class,
                "verifier_argv": list(verification.verifier_argv),
                "verifier_returncode": verification.verifier_returncode,
                "stdout_raw_sha256": _raw_ref(verification.stdout_sha256),
                "stderr_raw_sha256": _raw_ref(verification.stderr_sha256),
                "good_line": verification.good_line,
                "ssh_keygen_path": verification.ssh_keygen_path,
                "ssh_keygen_raw_sha256": verification.ssh_keygen_sha256,
            },
        },
        "approvals": {role: dict(assessment.approvals[role]) for role in APPROVAL_ROLES},
        "autonomous_refusal": dict(assessment.refusal),
        "merge_result": dict(assessment.merge),
        "nonce_ledger": {
            "final_state": assessment.ledger_records[-1]["to"],
            "records": [dict(row) for row in assessment.ledger_records],
        },
        "integration_prerequisites": dict(assessment.integration_prerequisites),
        "artifacts": {path: _raw_ref(value) for path, value in sorted(artifacts.items())},
        "limits": list(LIMITS),
    }


def build_magma_triple(
    payload: Mapping[str, Any], *, now_utc: datetime
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Build the EvaluationResult and receipt with the public MAGMA builders."""
    genuine = payload.get("genuine") is True and payload.get("evidence_class") == EVIDENCE_GENUINE
    prefix = "magma:manual_merge_a" if genuine else "synthetic:manual_merge_a"
    pull_request = payload["pull_request"]
    head = payload["head_sha"]
    nonce = payload["nonce"]
    approvals = payload["approvals"]
    solver_selection = [
        approvals["build_lead"]["agent"],
        approvals["build_tools"]["agent"],
        approvals["rco"]["agent"],
    ]
    uncertainty = [
        {"kind": "limited_evidence", "detail": LIMITS[0]},
        {"kind": "limited_evidence", "detail": LIMITS[1]},
        {"kind": "limited_evidence", "detail": LIMITS[2]},
    ]
    if genuine:
        verdict = "pass"
        actual_gate = "allow"
        reason_codes = [
            "manual_merge_a:operator_statement_verified",
            "rco:pass_exact_head_non_author",
            "build_consensus:lead_and_tools_exact_head",
            "merge:github_result_bound",
            "nonce:merged_terminal",
        ]
        confidence = 1.0
    else:
        verdict = "abstain"
        actual_gate = "review"
        reason_codes = [
            "synthetic_fixture:unit_mock_not_genuine",
            "integration_prerequisites:unknown",
        ]
        confidence = 0.0
        uncertainty.append(
            {"kind": "unknown", "detail": "Synthetic unit/mock fixture; not genuine merge evidence."}
        )
    evaluation = build_evaluation_result(
        case_id=f"{prefix}:pr{pull_request}:{head[:12]}",
        subject_type="promotion",
        target_payload=dict(payload),
        risk_class="external_effect",
        expected_gate="require_approval",
        actual_gate=actual_gate,
        verifier_path=[
            "manual_bridge_merge_statement.verify_statement_signature",
            "manual_bridge_merge_receipt.assess_receipt_evidence",
            "magma_receipt_verifier_v1",
        ],
        solver_selection=solver_selection,
        policy_version="policy:manual_merge_a_v1",
        charter_version=f"authority:operator_decision_sha256:{AUTHORITY_DECISION_SHA256}",
        domain_threshold_version="threshold:manual_merge_a:v1",
        verdict=verdict,
        reason_codes=reason_codes,
        confidence_score=confidence,
        uncertainty_sources=uncertainty,
        allow_external_effect=True,
    )
    receipt = build_magma_receipt(
        event_id=f"{prefix}:pr{pull_request}:{head[:12]}:{nonce}",
        ts_utc=_iso(_require_utc_clock(now_utc)),
        risk_class="external_effect",
        payload=dict(payload),
        evaluation_result=evaluation,
        policy_digest=sha256_digest(
            {
                "policy_version": evaluation["policy_version"],
                "namespace": NAMESPACE,
                "principal": PRINCIPAL,
                "purpose": PURPOSE,
                "allowed_signers_path": ALLOWED_SIGNERS_PATH,
                "rco_status": RCO_PASS_STATUS,
                "build_status": BUILD_CONSENSUS_STATUS,
            }
        ),
        charter_digest=sha256_digest(
            {
                "authority_decision_sha256": AUTHORITY_DECISION_SHA256,
                "base_sha": payload["base_sha"],
            }
        ),
        rco_decision_digest=sha256_digest(approvals["rco"]),
        world_snapshot_digest=sha256_digest(
            {
                "repository": payload["repository"],
                "pull_request": pull_request,
                "head_sha": head,
                "base_sha": payload["base_sha"],
                "diff_digest_sha256": payload["diff_digest_sha256"],
                "exact_paths": payload["exact_paths"],
                "merge_commit_oid": payload["merge_result"]["merge_commit_oid"],
            }
        ),
        solver_contract_digest=sha256_digest(
            {
                "verifier_path": evaluation["verifier_path"],
                "solver_selection": evaluation["solver_selection"],
            }
        ),
        approval_id=f"{prefix}:statement:{payload['statement']['raw_sha256']['hex']}",
        # SSHSIG is not a MAGMA signature envelope; never copy it here.
        signature_envelope=None,
        allow_external_effect=True,
    )
    chain_id = f"{prefix}:pr{pull_request}:{head[:12]}:{nonce}"
    return evaluation, receipt, chain_id


# --- writing -----------------------------------------------------------------


def _require_out_root(out_root: Any) -> Path:
    if not isinstance(out_root, Path) or not out_root.is_absolute():
        raise ReceiptError("out_root_invalid", "out_root must be an absolute Path")
    if out_root.is_symlink() or not out_root.is_dir():
        raise ReceiptError("out_root_invalid", "out_root must be an existing real directory")
    return out_root


def _write_new_file(path: Path, data: bytes) -> None:
    """Create ``path`` exclusively and write all of ``data`` once; never retry.

    Every ``os.write`` must report real progress: an ``int`` (not ``bool``) in
    ``1..remaining``.  A zero, negative, oversized or non-integer count, or a
    final size other than ``len(data)``, refuses with ``receipt_write_failed``
    instead of looping or accepting a short file; the partial file stays as part
    of the unaccepted failure artifact.  A close failure after an earlier error
    is noted on that error and never replaces it.  Counts and size are not
    content: other bytes of the same length pass here, so callers verify what
    was stored.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if type(written) is not int or not 0 < written <= len(view):
                shown = written if type(written) is int else type(written).__name__
                raise ReceiptError(
                    "receipt_write_failed", f"{path.name}: write reported {shown} of {len(view)} bytes"
                )
            view = view[written:]
        os.fsync(fd)
        size = os.fstat(fd).st_size
        if size != len(data):
            raise ReceiptError("receipt_write_failed", f"{path.name}: size {size} != {len(data)}")
    except BaseException as exc:
        try:
            os.close(fd)
        except OSError as close_exc:
            exc.add_note(f"close after failure also failed: {type(close_exc).__name__}")
        raise
    os.close(fd)


def receipt_dir_name(statement: Statement) -> str:
    return f"pr{statement.pull_request}-{statement.head_sha[:12]}-{statement.nonce}"


def _artifact_bytes(assessment: ReceiptAssessment, evidence: ReceiptEvidence) -> dict[str, bytes]:
    approval_events = b"".join(line + b"\n" for line in assessment.approval_lines)
    refusal = assessment.refusal_bytes
    if not refusal.endswith(b"\n"):
        refusal += b"\n"
    return {
        "evidence/statement.json": bytes(evidence.statement_bytes),
        "evidence/statement.sig": bytes(evidence.signature_bytes),
        "evidence/approval-events.jsonl": approval_events,
        "evidence/autonomous-refusal.json": refusal,
        "evidence/merge-result.json": bytes(evidence.merge_result.stdout),
        "evidence/merge-result-command.json": assessment.merge_command_bytes,
        "evidence/nonce-ledger.json": _canonical_json_line(
            [dict(row) for row in assessment.ledger_records]
        ),
    }


def _require_complete_as_written(receipt_dir: Path, marker_bytes: bytes, label: str) -> None:
    """Refuse unless the finished directory verifies complete exactly as written.

    ``_write_new_file`` checks counts and size only, so a marker or artifact
    stored as other bytes must be caught here before success is reported.
    """
    report = verify_manual_merge_receipt(receipt_dir)
    if report["ok"] is not True or report["complete"] is not True:
        raise ReceiptError("receipt_verification_failed", "completion: " + "; ".join(report["errors"]))
    if report["evidence_class"] != label or report["genuine"] is not (label == EVIDENCE_GENUINE):
        raise ReceiptError("receipt_verification_failed", "completion: evidence label differs from the one written")
    if (receipt_dir / COMPLETION_MARKER).read_bytes() != marker_bytes:
        raise ReceiptError("receipt_verification_failed", "completion: marker differs from the bytes written")


def write_manual_merge_receipt(
    evidence: ReceiptEvidence,
    *,
    repo_root: Path,
    ssh_keygen: Path,
    ledger: NonceLedger,
    out_root: Path,
    now_utc: datetime,
    synthetic_fixture: bool = False,
    runner: Runner | None = None,
    git_runner: Runner | None = None,
    git_executable: str = "git",
) -> dict[str, Any]:
    """Assess, then write one receipt directory exclusively; never overwrite or retry.

    Every refusal before the directory exists leaves no file behind.  Success
    is returned only after the finished directory passed
    :func:`verify_manual_merge_receipt` (``ok`` and ``complete``) with the
    expected evidence label and its marker read back as the exact bytes
    written.  After the directory exists, a ``ReceiptError``, ``OSError`` or
    ``ValueError`` (a failed completion check included) is raised as
    ``receipt_write_failed`` with the original as ``__cause__``.  Any other
    exception, for example ``TypeError``, ``MemoryError``,
    ``KeyboardInterrupt`` or ``SystemExit``, propagates unchanged; under
    ``MemoryError`` nothing further is promised.  Every failure leaves the
    directory as it is: partial, without a marker, or with a marker that may
    even verify (a close failure or an interruption after the marker write).
    The caller reconciles it with the verifier and never retries, deletes or
    overwrites it.  These are point-in-time checks: no crash atomicity and no
    protection against another process changing the files.
    """
    now = _require_utc_clock(now_utc)
    root = _require_out_root(out_root)
    assessment = assess_receipt_evidence(
        evidence,
        repo_root=repo_root,
        ssh_keygen=ssh_keygen,
        ledger=ledger,
        runner=runner,
        git_runner=git_runner,
        git_executable=git_executable,
    )
    label = _receipt_mode_decision(
        evidence_class=assessment.evidence_class,
        synthetic_fixture=synthetic_fixture,
        prerequisites=assessment.integration_prerequisites,
    )
    merged_at = _parse_gh_time(assessment.merge["merged_at"], "mergedAt")
    if now < merged_at:
        raise ReceiptError("receipt_clock_before_merge", assessment.merge["merged_at"])
    artifacts = _artifact_bytes(assessment, evidence)
    artifact_digests = {path: raw_sha256(data) for path, data in artifacts.items()}
    payload = build_receipt_payload(
        assessment, evidence=evidence, label=label, artifacts=artifact_digests
    )
    evaluation, receipt, chain_id = build_magma_triple(payload, now_utc=now)
    receipt_dir = root / receipt_dir_name(assessment.statement)
    # One receipt (or failure artifact) per PR head, whatever its nonce.
    same_merge = f"pr{assessment.statement.pull_request}-{assessment.statement.head_sha[:12]}-"
    existing = sorted(entry.name for entry in root.iterdir() if entry.name.startswith(same_merge))
    if existing:
        raise ReceiptError("receipt_collision", ",".join(existing))
    try:
        receipt_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ReceiptError("receipt_collision", receipt_dir.name) from exc
    except OSError as exc:
        raise ReceiptError("receipt_write_failed", type(exc).__name__) from exc
    try:
        (receipt_dir / EVIDENCE_DIR).mkdir(exist_ok=False)
        for path in ARTIFACT_FILES:
            _write_new_file(receipt_dir / Path(*path.split("/")), artifacts[path])
        bundle = write_receipt_bundle(
            out_dir=receipt_dir / MAGMA_DIR,
            chain_id=chain_id,
            entries=[
                ReceiptBundleEntry(
                    label=BUNDLE_LABEL,
                    payload=dict(payload),
                    evaluation_result=evaluation,
                    receipt=receipt,
                )
            ],
            verify_manifest=verify_manifest,
        )
        report = verify_receipt_contents(receipt_dir, expected_label=label)
        if not report["ok"]:
            raise ReceiptError("receipt_verification_failed", "; ".join(report["errors"]))
        marker = {
            "schema": COMPLETION_SCHEMA,
            "evidence_class": label,
            "genuine": label == EVIDENCE_GENUINE,
            "receipt_dir_name": receipt_dir.name,
            "chain_id": chain_id,
            "artifacts": {path: _raw_ref(value) for path, value in sorted(artifact_digests.items())},
            "magma": {
                "payload_digest": sha256_digest(payload),
                "evaluation_result_digest": sha256_digest(evaluation),
                "receipt_digest": sha256_digest(receipt),
                "manifest_raw_sha256": _raw_ref(
                    raw_sha256((receipt_dir / MAGMA_DIR / "manifest.json").read_bytes())
                ),
            },
            "verified_at_utc": _iso(now),
        }
        marker_bytes = _canonical_json_line(marker)
        _write_new_file(receipt_dir / COMPLETION_MARKER, marker_bytes)
        _require_complete_as_written(receipt_dir, marker_bytes, label)
    except ReceiptError as exc:
        raise ReceiptError(
            "receipt_write_failed", f"{receipt_dir.name}: {exc.reason}; reconcile, do not retry"
        ) from exc
    except (OSError, ValueError) as exc:
        raise ReceiptError(
            "receipt_write_failed", f"{receipt_dir.name}: {type(exc).__name__}; reconcile, do not retry"
        ) from exc
    return {
        "receipt_dir": str(receipt_dir),
        "evidence_class": label,
        "genuine": label == EVIDENCE_GENUINE,
        "chain_id": chain_id,
        "receipt_count": bundle["receipt_count"],
        "completion_marker": COMPLETION_MARKER,
    }


# --- verification ------------------------------------------------------------


def _list_entries(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


def verify_receipt_contents(receipt_dir: Path, *, expected_label: str | None = None) -> dict[str, Any]:
    """Re-derive the receipt from its files (no completion marker needed).

    Structural and digest re-derivation only: it does not re-run
    ``ssh-keygen`` (that needs the trusted anchor and the verifier binary; use
    ``assess_receipt_evidence``) and it does not establish any integration
    prerequisite.
    """
    errors: list[str] = []
    result: dict[str, Any] = {"ok": False, "evidence_class": None, "genuine": False, "errors": errors}
    if not isinstance(receipt_dir, Path) or not receipt_dir.is_absolute():
        errors.append("receipt_dir must be an absolute Path")
        return result
    if receipt_dir.is_symlink() or not receipt_dir.is_dir():
        errors.append("receipt_dir must be an existing real directory")
        return result
    evidence_dir = receipt_dir / EVIDENCE_DIR
    magma_dir = receipt_dir / MAGMA_DIR
    for directory in (evidence_dir, magma_dir):
        if directory.is_symlink() or not directory.is_dir():
            errors.append(f"{directory.name}: missing directory")
            return result
    expected_evidence = sorted(path.split("/", 1)[1] for path in ARTIFACT_FILES)
    if _list_entries(evidence_dir) != expected_evidence:
        errors.append("evidence: unexpected or missing entries")
    if _list_entries(magma_dir) != sorted(MAGMA_FILES):
        errors.append("magma: unexpected or missing entries")
    top = [name for name in _list_entries(receipt_dir) if name not in (EVIDENCE_DIR, MAGMA_DIR, COMPLETION_MARKER)]
    if top:
        errors.append("receipt_dir: unexpected entries")
    if errors:
        return result
    report = verify_manifest(magma_dir / "manifest.json")
    if report.get("ok") is not True or report.get("receipt_count") != 1 or report.get("errors") != []:
        errors.append("magma: manifest verification failed")
        return result
    try:
        payload = _strict_json((magma_dir / MAGMA_FILES[0]).read_bytes(), "payload_invalid")
        receipt = _strict_json((magma_dir / MAGMA_FILES[2]).read_bytes(), "receipt_invalid")
    except ReceiptError as exc:
        errors.append(f"magma: {exc.reason}")
        return result
    if not isinstance(payload, dict) or payload.get("schema") != RECEIPT_PAYLOAD_SCHEMA:
        errors.append("payload: schema mismatch")
        return result
    if not isinstance(receipt, dict) or any(
        receipt.get(key) is not None for key in ("signature_algorithm", "signature", "key_id")
    ):
        errors.append("receipt: a MAGMA signature envelope is present")
    label = payload.get("evidence_class")
    if label not in (EVIDENCE_GENUINE, EVIDENCE_SYNTHETIC) or payload.get("genuine") is not (label == EVIDENCE_GENUINE):
        errors.append("payload: evidence class is inconsistent")
        return result
    if expected_label is not None and label != expected_label:
        errors.append("payload: evidence class differs from the expected label")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or sorted(artifacts) != sorted(ARTIFACT_FILES):
        errors.append("payload: artifact list mismatch")
        return result
    contents: dict[str, bytes] = {}
    for path in ARTIFACT_FILES:
        file_path = receipt_dir / Path(*path.split("/"))
        if file_path.is_symlink() or not file_path.is_file():
            errors.append(f"{path}: not a regular file")
            continue
        data = file_path.read_bytes()
        contents[path] = data
        ref = artifacts.get(path)
        if not isinstance(ref, dict) or ref != _raw_ref(raw_sha256(data)):
            errors.append(f"{path}: raw digest mismatch")
    if errors:
        return result
    try:
        statement = parse_statement(contents["evidence/statement.json"])
    except StatementError as exc:
        errors.append(f"statement: {exc.reason}")
        return result
    for field in ("pull_request", "head_sha", "base_sha", "nonce", "batch_id", "diff_digest_sha256"):
        if payload.get(field) != getattr(statement, field):
            errors.append(f"payload: {field} differs from the statement")
    if payload.get("exact_paths") != list(statement.exact_paths):
        errors.append("payload: exact_paths differ from the statement")
    statement_ref = payload.get("statement", {}).get("raw_sha256") if isinstance(payload.get("statement"), dict) else None
    if statement_ref != _raw_ref(raw_sha256(contents["evidence/statement.json"])):
        errors.append("payload: statement digest mismatch")
    lines = contents["evidence/approval-events.jsonl"].split(b"\n")
    if len(lines) != len(APPROVAL_ROLES) + 1 or lines[-1] != b"":
        errors.append("approval-events: expected exactly three lines")
    else:
        approvals = payload.get("approvals")
        for role, line in zip(APPROVAL_ROLES, lines):
            try:
                event = _strict_json(line, "approval_line_invalid")
                canonical = bridge_event_bytes(event) if isinstance(event, dict) else b""
            except ReceiptError as exc:
                errors.append(f"approval-events: {role} {exc.reason}")
                continue
            if canonical != line:
                errors.append(f"approval-events: {role} line is not canonical")
            if not isinstance(approvals, dict) or not isinstance(approvals.get(role), dict) or approvals[role].get("event_id") != raw_sha256(line):
                errors.append(f"approval-events: {role} event_id mismatch")
    refusal = payload.get("autonomous_refusal")
    refusal_bytes = contents["evidence/autonomous-refusal.json"]
    if not isinstance(refusal, dict) or not refusal_bytes.endswith(b"\n"):
        errors.append("autonomous-refusal: missing record")
    elif refusal.get("state") == "recorded":
        if refusal.get("event_id") != raw_sha256(refusal_bytes[:-1]):
            errors.append("autonomous-refusal: event_id mismatch")
    elif refusal.get("state") != "absent_unknown" or _canonical_json_line(refusal) != refusal_bytes:
        errors.append("autonomous-refusal: absence record mismatch")
    merge = payload.get("merge_result")
    if not isinstance(merge, dict) or merge.get("raw_sha256") != raw_sha256(contents["evidence/merge-result.json"]):
        errors.append("merge-result: raw digest mismatch")
    if not errors:
        result["ok"] = True
        result["evidence_class"] = label
        result["genuine"] = label == EVIDENCE_GENUINE
    return result


def _is_iso_second(value: Any) -> bool:
    """True only for a real UTC second spelled exactly as ``_iso`` writes it."""
    try:
        return _iso(_parse_gh_time(value, "verified_at_utc")) == value
    except ReceiptError:
        return False


def _marker_binding_errors(
    receipt_dir: Path, marker: Mapping[str, Any], report: Mapping[str, Any]
) -> list[str]:
    """Compare every marker value with the files ``verify_receipt_contents`` accepted.

    Reads the fixed MAGMA file names, never the manifest's entry paths.  Each
    value needs its exact JSON type and must equal what the files give: the
    payload's artifact references and label; the chain identity rebuilt from
    the payload's own pull request, head and nonce, which the manifest
    ``chain_id`` and the receipt ``event_id`` must also equal; the canonical
    MAGMA digests of payload, evaluation and receipt (the first two also as
    recorded in the receipt); the raw SHA-256 of the manifest bytes; and the
    receipt ``ts_utc`` in the writer's seconds-only ``_iso`` spelling (the
    writer stores both from one clock reading).  Equality is internal
    consistency only: no clock, freshness or authenticity check, and unsigned
    files rewritten coherently still verify.
    """
    magma_dir = receipt_dir / MAGMA_DIR
    manifest_bytes = (magma_dir / MAGMA_FILES[3]).read_bytes()
    try:
        payload, evaluation, receipt = [
            _strict_json((magma_dir / name).read_bytes(), "magma_invalid") for name in MAGMA_FILES[:3]
        ]
        manifest = _strict_json(manifest_bytes, "manifest_invalid")
    except ReceiptError as exc:
        return [f"marker: {exc.reason}"]
    if not all(type(value) is dict for value in (payload, evaluation, receipt, manifest)):
        return ["marker: a MAGMA file is not a JSON object"]
    magma = marker["magma"]
    ref = magma["manifest_raw_sha256"]
    wrong = [
        name
        for name, value in (
            ("evidence_class", marker["evidence_class"]),
            ("chain_id", marker["chain_id"]),
            ("verified_at_utc", marker["verified_at_utc"]),
            ("magma.payload_digest", magma["payload_digest"]),
            ("magma.evaluation_result_digest", magma["evaluation_result_digest"]),
            ("magma.receipt_digest", magma["receipt_digest"]),
        )
        if type(value) is not str
    ]
    if type(marker["genuine"]) is not bool:
        wrong.append("genuine")
    if type(marker["artifacts"]) is not dict:
        wrong.append("artifacts")
    if type(ref) is not dict or sorted(ref) != ["algorithm", "domain", "hex"] or any(
        type(value) is not str for value in ref.values()
    ):
        wrong.append("magma.manifest_raw_sha256")
    if wrong:
        return ["marker: wrong type or shape: " + ",".join(wrong)]
    errors: list[str] = []
    if marker["artifacts"] != payload.get("artifacts"):
        errors.append("marker: artifact digests differ from the payload")
    if marker["evidence_class"] != report["evidence_class"] or marker["genuine"] is not report["genuine"]:
        errors.append("marker: evidence class differs from the payload")
    pull_request, head, nonce = payload.get("pull_request"), payload.get("head_sha"), payload.get("nonce")
    if type(pull_request) is not int or type(head) is not str or type(nonce) is not str:
        errors.append("marker: payload identity has the wrong type")
    else:
        prefix = "magma:manual_merge_a" if report["genuine"] else "synthetic:manual_merge_a"
        chain_id = f"{prefix}:pr{pull_request}:{head[:12]}:{nonce}"
        if chain_id != marker["chain_id"] or chain_id != manifest.get("chain_id") or chain_id != receipt.get("event_id"):
            errors.append("marker: chain_id differs from the payload identity, manifest or receipt event_id")
    payload_digest = sha256_digest(payload)
    if magma["payload_digest"] != payload_digest or receipt.get("canonical_payload_digest") != payload_digest:
        errors.append("marker: payload digest mismatch")
    evaluation_digest = sha256_digest(evaluation)
    if (
        magma["evaluation_result_digest"] != evaluation_digest
        or receipt.get("evaluation_result_digest") != evaluation_digest
    ):
        errors.append("marker: evaluation_result_digest mismatch")
    if magma["receipt_digest"] != sha256_digest(receipt):
        errors.append("marker: receipt_digest mismatch")
    if ref != _raw_ref(raw_sha256(manifest_bytes)):
        errors.append("marker: manifest_raw_sha256 mismatch")
    if not _is_iso_second(marker["verified_at_utc"]):
        errors.append("marker: verified_at_utc is not a YYYY-MM-DDTHH:MM:SSZ UTC second")
    elif marker["verified_at_utc"] != receipt.get("ts_utc"):
        errors.append("marker: verified_at_utc differs from the receipt ts_utc")
    return errors


def verify_manual_merge_receipt(receipt_dir: Path) -> dict[str, Any]:
    """Full read-only verification of a completed receipt directory.

    ``complete`` is true only for a complete, internally consistent receipt:
    the completion marker has exactly the v1 keys and types, and every value
    equals what the accepted files give (``_marker_binding_errors``).  It is
    NOT acceptance as merge evidence, NOT proof that the writer reported
    success and NOT a freshness or authenticity check: consumers must also
    require ``genuine`` (true only when the digest-bound payload says so, which
    this slice never writes).  A directory without the completion marker is
    an unaccepted failure artifact.
    """
    report = verify_receipt_contents(receipt_dir)
    report["complete"] = False
    if not report["ok"]:
        return report
    marker_path = receipt_dir / COMPLETION_MARKER
    errors: list[str] = report["errors"]
    if marker_path.is_symlink() or not marker_path.is_file():
        errors.append("completion marker missing: unaccepted failure artifact")
        report["ok"] = False
        return report
    data = marker_path.read_bytes()
    try:
        marker = _strict_json(data, "marker_invalid")
    except ReceiptError as exc:
        errors.append(f"marker: {exc.reason}")
        report["ok"] = False
        return report
    if not isinstance(marker, dict) or _canonical_json_line(marker) != data:
        errors.append("marker: not canonical")
    elif sorted(marker) != sorted(MARKER_KEYS):
        errors.append("marker: keys differ from the v1 marker")
    elif type(marker["magma"]) is not dict or sorted(marker["magma"]) != sorted(MARKER_MAGMA_KEYS):
        errors.append("marker: magma keys differ from the v1 marker")
    elif marker["schema"] != COMPLETION_SCHEMA or marker["receipt_dir_name"] != receipt_dir.name:
        errors.append("marker: schema or directory mismatch")
    else:
        errors.extend(_marker_binding_errors(receipt_dir, marker, report))
    if errors:
        report["ok"] = False
        return report
    report["complete"] = True
    return report


__all__ = [
    "APPROVAL_ROLES",
    "ARTIFACT_FILES",
    "AutonomousRefusalEvidence",
    "COMPLETION_MARKER",
    "EVIDENCE_GENUINE",
    "EVIDENCE_SYNTHETIC",
    "INTEGRATION_PREREQUISITES",
    "MergeResultEvidence",
    "ReceiptAssessment",
    "ReceiptError",
    "ReceiptEvidence",
    "assess_approvals",
    "assess_ledger",
    "assess_merge_result",
    "assess_receipt_evidence",
    "assess_refusal",
    "bridge_event_bytes",
    "bridge_event_id",
    "build_magma_triple",
    "build_receipt_payload",
    "raw_sha256",
    "receipt_dir_name",
    "verify_manual_merge_receipt",
    "verify_receipt_contents",
    "write_manual_merge_receipt",
]
