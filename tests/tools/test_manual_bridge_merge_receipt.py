# SPDX-License-Identifier: BUSL-1.1
"""Tests for the manual merge receipt evidence contract (MANUAL-A slice 2).

Every signature check here uses injected git / ssh-keygen runners, so all
results are ``unit_mock`` evidence and every written receipt is labelled
``synthetic_unit_mock``.  Nothing here is a real cryptographic proof, a real
merge or a genuine receipt; that positive path is a named NOT_RUN skip.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

import pytest

from tools import manual_bridge_merge_receipt as mmr
from tools import manual_bridge_merge_statement as mms
from tools.manual_bridge_merge_receipt import (
    AutonomousRefusalEvidence,
    MergeResultEvidence,
    ReceiptError,
    ReceiptEvidence,
)
from tools.manual_bridge_merge_statement import ALLOWED_SIGNERS_PATH, NonceLedger, RunResult
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "manual_bridge_merge_receipt.py"
BASE = "b" * 40
HEAD = "a" * 40
OTHER = "f" * 40
MERGE_COMMIT = "c" * 40
NONCE = "0123456789abcdef0123456789abcdef"
DIFF = "d" * 64
TASK = "manual-a-20261005"
PR = 1763
EXPIRY = "2026-10-05T12:00:00Z"
MERGED_AT = "2026-10-05T07:10:00Z"
NOW = datetime(2026, 10, 5, 7, 15, 0, tzinfo=timezone.utc)
PATHS = ("tests/tools/test_manual_bridge_merge_receipt.py", "tools/manual_bridge_merge_receipt.py")
AUTHORS = ("codex-lead-1", "fable-5")
SIGNATURE = b"-----BEGIN SSH SIGNATURE-----\nU1NIU0lHAAAAAQ==\n-----END SSH SIGNATURE-----\n"
GH_ARGV = ("gh", "pr", "view", str(PR), "--json", "baseRefName,headRefOid,mergeCommit,mergedAt,number,state")


# --- synthetic fixtures (public-key shaped bytes only; no private key exists) ---


def ssh_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


SYNTHETIC_BLOB = ssh_string(b"ssh-ed25519") + ssh_string(bytes(range(32)))
ANCHOR_DATA = (
    f'{mms.PRINCIPAL} {mms.ANCHOR_OPTIONS} ssh-ed25519 '
    f"{base64.b64encode(SYNTHETIC_BLOB).decode('ascii')} operator-public-synthetic\n"
).encode("ascii")


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\x00" + data).hexdigest()


class FakeGit:
    """unit_mock git: answers only the trust-anchor loader's four commands."""

    def __init__(self, data: bytes = ANCHOR_DATA, commit: str = BASE):
        self.data = data
        self.commit = commit
        self.blob_sha = git_blob_sha(data)

    def __call__(self, argv, *, input_bytes, timeout, env):
        args = list(argv[3:])
        if args[:3] == ["rev-parse", "--verify", "--quiet"]:
            if args[3] == f"{self.commit}^{{commit}}":
                return RunResult(0, (self.commit + "\n").encode(), b"")
            if args[3] == f"{self.commit}:{ALLOWED_SIGNERS_PATH}":
                return RunResult(0, (self.blob_sha + "\n").encode(), b"")
            return RunResult(1, b"", b"")
        if args[:2] == ["cat-file", "-t"]:
            return RunResult(0, b"blob\n", b"")
        if args[:2] == ["cat-file", "blob"]:
            return RunResult(0, self.data, b"")
        raise AssertionError(f"unexpected git call {argv}")


class FakeSsh:
    """unit_mock ssh-keygen: 'Good' only for the exact signed bytes (never real crypto)."""

    def __init__(self, signed: bytes, anchor: mms.TrustAnchor):
        self.signed = signed
        self.anchor = anchor
        self.calls = 0

    def __call__(self, argv, *, input_bytes, timeout, env):
        self.calls += 1
        signature = Path(argv[10]).read_bytes()
        if input_bytes == self.signed and signature == SIGNATURE:
            line = (
                f'Good "{mms.NAMESPACE}" signature for {mms.PRINCIPAL} with '
                f"{self.anchor.key_label} key {self.anchor.fingerprint}\n"
            )
            return RunResult(0, line.encode("ascii"), b"")
        return RunResult(255, b"", b"Could not verify signature.\n")


def bridge_event(agent: str, uuid: str, status: str, payload: dict, overrides: dict) -> dict:
    event = {
        "ts_utc": "2026-10-05T07:00:00.1234567Z",
        "agent": agent,
        "agent_uuid": uuid,
        "type": "decision",
        "task_id": TASK,
        "status": status,
        "severity": "",
        "to": "codex-lead-1",
        "message": "synthetic test event",
        "payload": payload,
    }
    event.update(overrides)
    return event


def rco_event(**overrides) -> dict:
    return bridge_event("claude-rco-1", "rco1-uuid-synthetic", "rco_pass", {"exact_head": HEAD}, overrides)


def lead_event(**overrides) -> dict:
    return bridge_event("codex-lead-1", "lead-uuid-synthetic", "build_consensus_pass", {"head": HEAD}, overrides)


def tools_event(**overrides) -> dict:
    return bridge_event("codex-tools-1", "tools-uuid-synthetic", "build_consensus_pass", {"exact_head": HEAD}, overrides)


def gh_stdout(**overrides) -> bytes:
    data = {
        "baseRefName": "main",
        "headRefOid": HEAD,
        "mergeCommit": {"oid": MERGE_COMMIT},
        "mergedAt": MERGED_AT,
        "number": PR,
        "state": "MERGED",
    }
    data.update(overrides)
    return json.dumps(data).encode("utf-8")


def merge_result(**overrides) -> MergeResultEvidence:
    fields = {"argv": GH_ARGV, "returncode": 0, "stdout": gh_stdout()}
    fields.update(overrides)
    return MergeResultEvidence(**fields)


ABSENT = AutonomousRefusalEvidence(
    state="absent_unknown",
    absence_note="Current autonomous gate emitted no refusal event for this PR (synthetic test).",
)


def build_ledger(root: Path, statement: mms.Statement, digest: str, final: str) -> NonceLedger:
    root.mkdir()
    ledger = NonceLedger(root, clock=lambda: NOW)
    if final == "empty":
        return ledger
    ledger.reserve(
        nonce=statement.nonce,
        statement_sha256=digest,
        pull_request=statement.pull_request,
        head_sha=statement.head_sha,
        base_sha=statement.base_sha,
        batch_id=statement.batch_id,
    )
    path = {
        "reserved": [],
        "executed": ["merge_started", "executed"],
        "reconciled_merged": ["merge_started", "indeterminate", "reconciled_merged"],
        "reconciled_not_merged": ["merge_started", "indeterminate", "reconciled_not_merged"],
    }[final]
    for state in path:
        ledger.transition(nonce=statement.nonce, to_state=state, statement_sha256=digest)
    return ledger


@dataclass
class World:
    tmp: Path
    anchor: mms.TrustAnchor
    statement: mms.Statement
    statement_bytes: bytes
    git: FakeGit
    ssh: FakeSsh
    ledger: NonceLedger
    out_root: Path
    keygen: Path

    def evidence(self, **overrides) -> ReceiptEvidence:
        fields = dict(
            statement_bytes=self.statement_bytes,
            signature_bytes=SIGNATURE,
            canonical_task_id=TASK,
            author_agents=AUTHORS,
            rco_pass_event=rco_event(),
            lead_build_event=lead_event(),
            tools_build_event=tools_event(),
            autonomous_refusal=ABSENT,
            merge_result=merge_result(),
        )
        fields.update(overrides)
        return ReceiptEvidence(**fields)

    def params(self, **overrides) -> dict:
        params = dict(
            repo_root=self.tmp,
            ssh_keygen=self.keygen,
            ledger=self.ledger,
            runner=self.ssh,
            git_runner=self.git,
        )
        params.update(overrides)
        return params

    def write(self, evidence: ReceiptEvidence | None = None, **overrides) -> dict:
        params = self.params(out_root=self.out_root, now_utc=NOW, synthetic_fixture=True)
        params.update(overrides)
        return mmr.write_manual_merge_receipt(evidence or self.evidence(), **params)

    def assess(self, evidence: ReceiptEvidence | None = None, **overrides) -> mmr.ReceiptAssessment:
        return mmr.assess_receipt_evidence(evidence or self.evidence(), **self.params(**overrides))


def make_world(tmp_path: Path, *, ledger_final: str = "executed", ledger_digest: str | None = None,
               **statement_overrides) -> World:
    git = FakeGit()
    anchor = mms.load_trust_anchor(repo_root=tmp_path, trusted_commit=BASE, runner=git)
    fields = dict(
        pull_request=PR,
        head_sha=HEAD,
        base_sha=BASE,
        diff_digest_sha256=DIFF,
        exact_paths=PATHS,
        batch_id="mma-20261005-receipt",
        batch_order=1,
        dependencies=(),
        expires_at_utc=EXPIRY,
        nonce=NONCE,
        allowed_signers_blob_sha=anchor.blob_sha,
        key_fingerprint=anchor.fingerprint,
    )
    fields.update(statement_overrides)
    statement = mms.build_statement(**fields)
    statement_bytes = mms.canonical_statement_bytes(statement)
    digest = ledger_digest or mms.statement_sha256(statement_bytes)
    ledger = build_ledger(tmp_path / "ledger", statement, digest, ledger_final)
    out_root = tmp_path / "receipts"
    out_root.mkdir()
    return World(
        tmp=tmp_path,
        anchor=anchor,
        statement=statement,
        statement_bytes=statement_bytes,
        git=git,
        ssh=FakeSsh(statement_bytes, anchor),
        ledger=ledger,
        out_root=out_root,
        keygen=tmp_path / "ssh-keygen-fake.exe",
    )


def refusal_reason(func, *args, **kwargs) -> str:
    with pytest.raises(ReceiptError) as caught:
        func(*args, **kwargs)
    return caught.value.reason


def tree(path: Path) -> list[str]:
    return sorted(str(p.relative_to(path)) for p in path.rglob("*"))


def magma_json(receipt_dir: Path, kind: str) -> dict:
    return json.loads((receipt_dir / "magma" / f"{kind}-001-manual-merge.json").read_text(encoding="utf-8"))


# --- R01 synthetic receipt: written, complete, labelled -------------------------


def test_synthetic_receipt_is_complete_and_labelled_never_genuine(tmp_path):
    world = make_world(tmp_path)
    result = world.write()
    receipt_dir = Path(result["receipt_dir"])
    assert result["evidence_class"] == "synthetic_unit_mock" and result["genuine"] is False
    assert receipt_dir.parent == world.out_root
    report = mmr.verify_manual_merge_receipt(receipt_dir)
    assert report["ok"] is True and report["complete"] is True, report["errors"]
    assert report["genuine"] is False and report["evidence_class"] == "synthetic_unit_mock"
    payload = magma_json(receipt_dir, "payload")
    assert payload["genuine"] is False and payload["evidence_class"] == "synthetic_unit_mock"
    assert payload["statement"]["verification"]["evidence_class"] == "unit_mock"
    assert set(payload["integration_prerequisites"]) == set(mmr.INTEGRATION_PREREQUISITES)
    assert set(payload["integration_prerequisites"].values()) == {mmr.PREREQUISITE_UNKNOWN}
    evaluation = magma_json(receipt_dir, "evaluation")
    assert evaluation["verdict"] == "abstain" and evaluation["actual_gate"] == "review"
    assert "synthetic_fixture:unit_mock_not_genuine" in evaluation["reason_codes"]
    receipt = magma_json(receipt_dir, "receipt")
    assert receipt["event_id"].startswith("synthetic:manual_merge_a:")
    digest = mms.statement_sha256(world.statement_bytes)
    assert receipt["approval_id"] == f"synthetic:manual_merge_a:statement:{digest}"
    assert verify_manifest(receipt_dir / "magma" / "manifest.json")["ok"] is True
    assert (receipt_dir / "evidence" / "statement.json").read_bytes() == world.statement_bytes
    assert (receipt_dir / "evidence" / "statement.sig").read_bytes() == SIGNATURE
    assert world.ssh.calls == 1


def test_receipt_binds_statement_approvals_merge_and_ledger(tmp_path):
    world = make_world(tmp_path)
    payload = magma_json(Path(world.write()["receipt_dir"]), "payload")
    statement = world.statement
    for field in ("pull_request", "head_sha", "base_sha", "diff_digest_sha256", "nonce", "batch_id"):
        assert payload[field] == getattr(statement, field)
    assert payload["exact_paths"] == list(PATHS)
    assert payload["approvals"]["rco"]["agent"] == "claude-rco-1"
    assert payload["approvals"]["build_lead"]["agent"] == "codex-lead-1"
    assert payload["approvals"]["build_tools"]["agent"] == "codex-tools-1"
    assert payload["merge_result"]["merge_commit_oid"] == MERGE_COMMIT
    assert payload["merge_result"]["raw_sha256"] == hashlib.sha256(gh_stdout()).hexdigest()
    assert payload["nonce_ledger"]["final_state"] == "executed"
    assert [row["to"] for row in payload["nonce_ledger"]["records"]] == ["reserved", "merge_started", "executed"]
    assert payload["autonomous_refusal"]["state"] == "absent_unknown"
    assert payload["autonomous_refusal"]["event_id"] is None


def test_event_ids_match_bridge_compact_view_digest(tmp_path):
    from tools.bridge_compact_view import event_id

    for event in (rco_event(), lead_event(), tools_event(), rco_event(message="tarkastettu äö")):
        assert mmr.bridge_event_id(event) == event_id(event)
    world = make_world(tmp_path)
    receipt_dir = Path(world.write()["receipt_dir"])
    payload = magma_json(receipt_dir, "payload")
    assert payload["approvals"]["rco"]["event_id"] == event_id(rco_event())
    assert payload["approvals"]["build_lead"]["event_id"] == event_id(lead_event())
    assert payload["approvals"]["build_tools"]["event_id"] == event_id(tools_event())
    lines = (receipt_dir / "evidence" / "approval-events.jsonl").read_bytes().split(b"\n")
    assert [hashlib.sha256(line).hexdigest() for line in lines[:3]] == [
        event_id(rco_event()), event_id(lead_event()), event_id(tools_event())
    ]


def test_sshsig_never_enters_the_magma_signature_envelope(tmp_path):
    world = make_world(tmp_path)
    receipt_dir = Path(world.write()["receipt_dir"])
    receipt = magma_json(receipt_dir, "receipt")
    assert receipt["signature_algorithm"] is None
    assert receipt["signature"] is None
    assert receipt["key_id"] is None
    payload = magma_json(receipt_dir, "payload")
    assert payload["statement"]["magma_signature_envelope"] is None
    assert payload["statement"]["signature_format"] == "sshsig_armored_detached"
    for path in (receipt_dir / "magma").iterdir():
        assert b"SSH SIGNATURE" not in path.read_bytes()


def test_raw_byte_and_magma_digest_domains_stay_separate(tmp_path):
    world = make_world(tmp_path)
    receipt_dir = Path(world.write()["receipt_dir"])
    payload = magma_json(receipt_dir, "payload")
    raw = payload["statement"]["raw_sha256"]
    assert raw == {"domain": "raw_bytes", "algorithm": "sha256",
                   "hex": hashlib.sha256(world.statement_bytes).hexdigest()}
    for ref in payload["artifacts"].values():
        assert ref["domain"] == "raw_bytes" and not ref["hex"].startswith("sha256:")
    receipt = magma_json(receipt_dir, "receipt")
    assert receipt["canonical_payload_digest"] == sha256_digest(payload)
    assert receipt["canonical_payload_digest"].startswith("sha256:")


def test_assessment_is_read_only(tmp_path):
    world = make_world(tmp_path)
    before = tree(tmp_path)
    assessment = world.assess()
    assert assessment.evidence_class == "synthetic_unit_mock"
    assert tree(tmp_path) == before


# --- R02 genuine / synthetic mode ------------------------------------------------


def test_unit_mock_evidence_without_synthetic_flag_refuses_and_writes_nothing(tmp_path):
    world = make_world(tmp_path)
    assert refusal_reason(world.write, synthetic_fixture=False) == "evidence_not_genuine"
    assert list(world.out_root.iterdir()) == []


def test_genuine_evidence_refuses_while_integration_prerequisites_unknown():
    unknown = {name: mmr.PREREQUISITE_UNKNOWN for name in mmr.INTEGRATION_PREREQUISITES}
    with pytest.raises(ReceiptError) as caught:
        mmr._receipt_mode_decision(evidence_class="genuine", synthetic_fixture=False, prerequisites=unknown)
    assert caught.value.reason == "integration_prerequisites_unverified"
    for name in mmr.INTEGRATION_PREREQUISITES:
        assert name in caught.value.detail
    partial = dict(unknown, bridge_event_provenance="verified")
    with pytest.raises(ReceiptError) as caught:
        mmr._receipt_mode_decision(evidence_class="genuine", synthetic_fixture=False, prerequisites=partial)
    assert "bridge_event_provenance" not in caught.value.detail


@pytest.mark.parametrize(
    ("evidence_class", "synthetic_fixture"),
    [("genuine", True), ("unit_mock", True), ("", False), ("synthetic_unit_mock", "yes")],
)
def test_mode_decision_rejects_relabelling_and_unknown_classes(evidence_class, synthetic_fixture):
    unknown = {name: mmr.PREREQUISITE_UNKNOWN for name in mmr.INTEGRATION_PREREQUISITES}
    assert refusal_reason(
        mmr._receipt_mode_decision,
        evidence_class=evidence_class,
        synthetic_fixture=synthetic_fixture,
        prerequisites=unknown,
    ) == "receipt_mode_invalid"


def test_assessment_always_reports_prerequisites_unknown(tmp_path):
    assessment = make_world(tmp_path).assess()
    assert dict(assessment.integration_prerequisites) == {
        name: mmr.PREREQUISITE_UNKNOWN for name in mmr.INTEGRATION_PREREQUISITES
    }


# --- R03 statement / signature / anchor ------------------------------------------


def test_statement_for_another_pr_fails_signature(tmp_path):
    world = make_world(tmp_path)
    other = mms.canonical_statement_bytes(
        mms.build_statement(
            pull_request=PR + 1, head_sha=HEAD, base_sha=BASE, diff_digest_sha256=DIFF,
            exact_paths=PATHS, batch_id="mma-20261005-receipt", batch_order=1, dependencies=(),
            expires_at_utc=EXPIRY, nonce=NONCE, allowed_signers_blob_sha=world.anchor.blob_sha,
            key_fingerprint=world.anchor.fingerprint,
        )
    )
    assert refusal_reason(world.assess, world.evidence(statement_bytes=other)) == "statement:signature_invalid"


def test_non_canonical_statement_bytes_refuse(tmp_path):
    world = make_world(tmp_path)
    spaced = world.statement_bytes.replace(b'","', b'", "', 1)
    assert refusal_reason(world.assess, world.evidence(statement_bytes=spaced)) == "statement:non_canonical_bytes"


def test_garbage_signature_refuses(tmp_path):
    world = make_world(tmp_path)
    assert refusal_reason(world.assess, world.evidence(signature_bytes=b"not a signature")) == "statement:signature_malformed"


def test_statement_with_other_anchor_blob_refuses(tmp_path):
    world = make_world(tmp_path, allowed_signers_blob_sha="e" * 40)
    assert refusal_reason(world.assess) == "statement:anchor_blob_mismatch"


def test_statement_with_other_fingerprint_refuses(tmp_path):
    world = make_world(tmp_path, key_fingerprint="SHA256:" + "A" * 43)
    assert refusal_reason(world.assess) == "statement:key_fingerprint_mismatch"


# --- R04 approvals ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"type": "rco_review"}, "approval_type_invalid:rco"),
        ({"status": "rco_pass_withheld"}, "approval_status_invalid:rco"),
        ({"status": "RCO_PASS"}, "approval_status_invalid:rco"),
        ({"status": "pass"}, "approval_status_invalid:rco"),
        ({"agent": "grok-scout-1"}, "approval_agent_invalid:rco"),
        ({"agent": "operator"}, "approval_agent_invalid:rco"),
        ({"task_id": "codex-lead-1/manual-a-20261005"}, "approval_task_mismatch:rco"),
        ({"payload": {}, "message": f"rco_pass at {HEAD}"}, "approval_head_unbound:rco"),
        ({"payload": {"head": HEAD}}, "approval_head_unbound:rco"),
        ({"payload": {"exact_head": OTHER}}, "approval_head_mismatch:rco"),
        ({"payload": {"exact_head": HEAD, "head": OTHER}}, "approval_head_mismatch:rco"),
        ({"payload": {"exact_head": HEAD.upper()}}, "approval_head_mismatch:rco"),
        ({"agent_uuid": ""}, "approval_invalid:rco"),
        ({"ts_utc": "2026-10-05T07:00:00+00:00"}, "approval_invalid:rco"),
        ({"payload": {"exact_head": HEAD, "x": float("nan")}}, "event_not_canonical_json"),
    ],
)
def test_rco_pass_must_be_exact(tmp_path, overrides, reason):
    world = make_world(tmp_path)
    assert refusal_reason(world.assess, world.evidence(rco_pass_event=rco_event(**overrides))) == reason


def test_rco_author_cannot_fill_the_rco_slot(tmp_path):
    world = make_world(tmp_path)
    evidence = world.evidence(author_agents=("claude-rco-1", "codex-lead-1"))
    assert refusal_reason(world.assess, evidence) == "rco_is_author"


def test_backup_rco_pass_is_accepted(tmp_path):
    world = make_world(tmp_path)
    assessment = world.assess(world.evidence(rco_pass_event=rco_event(agent="claude-rco-2")))
    assert assessment.approvals["rco"]["agent"] == "claude-rco-2"


@pytest.mark.parametrize(
    ("slot", "overrides", "reason"),
    [
        ("lead", {"status": "approved"}, "approval_status_invalid:build_lead"),
        ("lead", {"status": "build_consensus"}, "approval_status_invalid:build_lead"),
        ("lead", {"type": "finding"}, "approval_type_invalid:build_lead"),
        ("lead", {"agent": "codex-tools-1"}, "approval_agent_invalid:build_lead"),
        ("tools", {"agent": "codex-lead-1"}, "approval_agent_invalid:build_tools"),
        ("tools", {"agent": "claude-rco-2"}, "approval_agent_invalid:build_tools"),
        ("tools", {"payload": {"note": "x"}, "message": HEAD}, "approval_head_unbound:build_tools"),
        ("tools", {"payload": {"exact_head": OTHER}}, "approval_head_mismatch:build_tools"),
        ("tools", {"task_id": "codex-tools-1/manual-a-20261005"}, "approval_task_mismatch:build_tools"),
    ],
)
def test_both_build_votes_must_be_exact(tmp_path, slot, overrides, reason):
    world = make_world(tmp_path)
    key = "lead_build_event" if slot == "lead" else "tools_build_event"
    event = lead_event(**overrides) if slot == "lead" else tools_event(**overrides)
    assert refusal_reason(world.assess, world.evidence(**{key: event})) == reason


@pytest.mark.parametrize("key", ["rco_pass_event", "lead_build_event", "tools_build_event"])
def test_missing_vote_refuses_even_when_lead_is_an_author(tmp_path, key):
    world = make_world(tmp_path)
    role = {"rco_pass_event": "rco", "lead_build_event": "build_lead", "tools_build_event": "build_tools"}[key]
    assert refusal_reason(world.assess, world.evidence(**{key: None})) == f"approval_missing:{role}"


def test_duplicate_identity_refuses(tmp_path):
    world = make_world(tmp_path)
    evidence = world.evidence(tools_build_event=tools_event(agent_uuid="lead-uuid-synthetic"))
    assert refusal_reason(world.assess, evidence) == "approval_identity_duplicate"


def test_author_set_is_required(tmp_path):
    world = make_world(tmp_path)
    assert refusal_reason(world.assess, world.evidence(author_agents=())) == "author_agents_missing"
    assert refusal_reason(world.assess, world.evidence(author_agents=["fable-5"])) == "author_agents_missing"


# --- R05 autonomous refusal --------------------------------------------------------


def refusal_event(**overrides) -> dict:
    return bridge_event("codex-lead-1", "lead-uuid-synthetic", "autonomous_refused", {"pr_number": PR}, overrides)


def test_recorded_refusal_is_preserved_unchanged(tmp_path):
    world = make_world(tmp_path)
    original = refusal_event(task_id="codex-lead-1/manual-merge-route-20261005")
    refusal = AutonomousRefusalEvidence(state="recorded", event=original)
    receipt_dir = Path(world.write(world.evidence(autonomous_refusal=refusal))["receipt_dir"])
    stored = (receipt_dir / "evidence" / "autonomous-refusal.json").read_bytes()
    assert stored == mmr.bridge_event_bytes(original) + b"\n"
    payload = magma_json(receipt_dir, "payload")
    assert payload["autonomous_refusal"]["event_id"] == mmr.bridge_event_id(original)
    assert payload["autonomous_refusal"]["classification"].startswith("preserved_unchanged")
    assert mmr.verify_manual_merge_receipt(receipt_dir)["complete"] is True


@pytest.mark.parametrize(
    ("refusal", "reason"),
    [
        (AutonomousRefusalEvidence(state="recorded", event=refusal_event(task_id="other", payload={})),
         "refusal_event_scope_mismatch"),
        (AutonomousRefusalEvidence(state="recorded", event=None), "refusal_evidence_contradictory"),
        (AutonomousRefusalEvidence(state="recorded", event=refusal_event(), absence_note="x"),
         "refusal_evidence_contradictory"),
        (AutonomousRefusalEvidence(state="absent_unknown", event=refusal_event(), absence_note="none seen"),
         "refusal_evidence_contradictory"),
        (AutonomousRefusalEvidence(state="absent_unknown", absence_note="  "), "refusal_absence_note_invalid"),
        (AutonomousRefusalEvidence(state="absent_unknown", absence_note="ä missing"), "refusal_absence_note_invalid"),
        (AutonomousRefusalEvidence(state="refused_by_gate", absence_note="x"), "refusal_state_invalid"),
        ({"state": "absent_unknown"}, "refusal_evidence_missing"),
    ],
)
def test_refusal_evidence_is_never_invented(tmp_path, refusal, reason):
    world = make_world(tmp_path)
    assert refusal_reason(world.assess, world.evidence(autonomous_refusal=refusal)) == reason


# --- R06 GitHub merge result --------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"returncode": 1}, "merge_result_ambiguous"),
        ({"stdout": b""}, "merge_result_ambiguous"),
        ({"stdout": gh_stdout(state="OPEN")}, "merge_result_not_merged"),
        ({"stdout": gh_stdout(state="CLOSED")}, "merge_result_not_merged"),
        ({"stdout": gh_stdout(headRefOid=OTHER)}, "merge_result_head_mismatch"),
        ({"stdout": gh_stdout(number=PR + 1)}, "merge_result_pr_mismatch"),
        ({"stdout": gh_stdout(number=str(PR))}, "merge_result_pr_mismatch"),
        ({"stdout": gh_stdout(baseRefName="release")}, "merge_result_base_mismatch"),
        ({"stdout": gh_stdout(mergeCommit=None)}, "merge_result_invalid"),
        ({"stdout": gh_stdout(mergeCommit={"oid": HEAD})}, "merge_result_invalid"),
        ({"stdout": gh_stdout(mergedAt="2026-10-05T12:00:00Z")}, "merged_after_statement_expiry"),
        ({"stdout": gh_stdout(mergedAt="2026-10-05T07:10:00+00:00")}, "merge_result_invalid"),
        ({"stdout": b'{"number":1763,"number":1763}'}, "merge_result_invalid"),
        ({"stdout": b"merged!"}, "merge_result_invalid"),
        ({"stdout": json.dumps({"number": PR}).encode()}, "merge_result_invalid"),
        ({"argv": ["gh", "pr", "view"]}, "merge_result_invalid"),
        ({"returncode": "0"}, "merge_result_invalid"),
    ],
)
def test_merge_result_is_bound_strictly(tmp_path, overrides, reason):
    world = make_world(tmp_path)
    assert refusal_reason(world.assess, world.evidence(merge_result=merge_result(**overrides))) == reason


# --- R07 nonce ledger -------------------------------------------------------------


@pytest.mark.parametrize(
    ("final", "reason"),
    [
        ("reserved", "nonce_not_merged"),
        ("reconciled_not_merged", "nonce_not_merged"),
        ("empty", "nonce_not_reserved"),
    ],
)
def test_nonce_must_be_in_a_merged_terminal_state(tmp_path, final, reason):
    world = make_world(tmp_path, ledger_final=final)
    assert refusal_reason(world.assess) == reason


def test_reconciled_merged_nonce_is_accepted(tmp_path):
    world = make_world(tmp_path, ledger_final="reconciled_merged")
    assert world.assess().ledger_records[-1]["to"] == "reconciled_merged"


def test_ledger_for_another_statement_refuses(tmp_path):
    world = make_world(tmp_path, ledger_digest="e" * 64)
    assert refusal_reason(world.assess) == "ledger_binding_mismatch"


def test_ledger_object_is_required(tmp_path):
    world = make_world(tmp_path)
    assert refusal_reason(world.assess, ledger=object()) == "ledger_missing"


# --- R08 exclusive writing, failure artifacts, tampering -----------------------------


def test_second_write_collides_and_first_receipt_is_untouched(tmp_path):
    world = make_world(tmp_path)
    receipt_dir = Path(world.write()["receipt_dir"])
    before = {p: p.read_bytes() for p in receipt_dir.rglob("*") if p.is_file()}
    assert refusal_reason(world.write) == "receipt_collision"
    assert {p: p.read_bytes() for p in receipt_dir.rglob("*") if p.is_file()} == before
    assert mmr.verify_manual_merge_receipt(receipt_dir)["complete"] is True


def test_failure_after_directory_creation_leaves_unaccepted_artifact(tmp_path, monkeypatch):
    world = make_world(tmp_path)

    def failing(receipt_dir, *, expected_label=None):
        return {"ok": False, "errors": ["injected verification failure"]}

    monkeypatch.setattr(mmr, "verify_receipt_contents", failing)
    with pytest.raises(ReceiptError) as caught:
        world.write()
    assert caught.value.reason == "receipt_write_failed"
    assert "do not retry" in caught.value.detail
    monkeypatch.undo()
    (receipt_dir,) = list(world.out_root.iterdir())
    assert not (receipt_dir / mmr.COMPLETION_MARKER).exists()
    report = mmr.verify_manual_merge_receipt(receipt_dir)
    assert report["complete"] is False and report["ok"] is False
    assert any("unaccepted failure artifact" in error for error in report["errors"])
    assert refusal_reason(world.write) == "receipt_collision"


def _flip_last_byte(path: Path) -> None:
    data = bytearray(path.read_bytes())
    data[-2] = ord("A") if data[-2] != ord("A") else ord("B")
    path.write_bytes(bytes(data))


@pytest.mark.parametrize(
    "tamper",
    ["signature", "extra_evidence", "payload", "marker_removed", "marker_edited", "events"],
)
def test_tampering_after_write_is_detected(tmp_path, tamper):
    world = make_world(tmp_path)
    receipt_dir = Path(world.write()["receipt_dir"])
    if tamper == "signature":
        _flip_last_byte(receipt_dir / "evidence" / "statement.sig")
    elif tamper == "extra_evidence":
        (receipt_dir / "evidence" / "note.txt").write_bytes(b"x")
    elif tamper == "payload":
        path = receipt_dir / "magma" / "payload-001-manual-merge.json"
        path.write_text(path.read_text(encoding="utf-8").replace('"genuine": false', '"genuine": true'), encoding="utf-8")
    elif tamper == "marker_removed":
        (receipt_dir / mmr.COMPLETION_MARKER).unlink()
    elif tamper == "marker_edited":
        path = receipt_dir / mmr.COMPLETION_MARKER
        path.write_bytes(path.read_bytes().replace(b'"genuine":false', b'"genuine":true'))
    elif tamper == "events":
        path = receipt_dir / "evidence" / "approval-events.jsonl"
        path.write_bytes(path.read_bytes().replace(b"rco_pass", b"rco_pasx"))
    report = mmr.verify_manual_merge_receipt(receipt_dir)
    assert report["ok"] is False and report["complete"] is False
    assert report["errors"]


@pytest.mark.parametrize("make_root", [lambda tmp: Path("receipts"), lambda tmp: tmp / "missing"])
def test_out_root_must_be_an_existing_absolute_directory(tmp_path, make_root):
    world = make_world(tmp_path)
    assert refusal_reason(world.write, out_root=make_root(tmp_path)) == "out_root_invalid"


def test_clock_must_be_aware_utc_and_not_before_the_merge(tmp_path):
    world = make_world(tmp_path)
    assert refusal_reason(world.write, now_utc=datetime(2026, 10, 5, 7, 15)) == "invalid_clock"
    early = datetime(2026, 10, 5, 7, 5, tzinfo=timezone.utc)
    assert refusal_reason(world.write, now_utc=early) == "receipt_clock_before_merge"
    assert list(world.out_root.iterdir()) == []


# --- R09 module hygiene ------------------------------------------------------------

_ALLOWED_TOP_LEVEL = {
    "__future__", "dataclasses", "datetime", "hashlib", "json", "os", "pathlib", "re", "sys",
    "typing", "tools", "waggledance",
}
_ALLOWED_PROJECT_IMPORTS = {
    "tools.manual_bridge_merge_statement",
    "tools.verify_magma_receipt",
    "waggledance.core.magma.canonical",
    "waggledance.core.magma.evaluation_result",
    "waggledance.core.magma.receipt",
    "waggledance.core.magma.receipt_bundle",
}


def test_module_imports_only_public_magma_and_statement_apis():
    tree_ = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    top: set[str] = set()
    project: set[str] = set()
    for node in ast.walk(tree_):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top.add(alias.name.split(".")[0])
                if alias.name.split(".")[0] in ("tools", "waggledance"):
                    project.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top.add(module.split(".")[0])
            if module.split(".")[0] in ("tools", "waggledance"):
                project.add(module)
    assert top <= _ALLOWED_TOP_LEVEL, top - _ALLOWED_TOP_LEVEL
    assert project == _ALLOWED_PROJECT_IMPORTS, project ^ _ALLOWED_PROJECT_IMPORTS
    text = MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "gh pr merge", "check_bridge", "check_rco_pass", "idle_consensus",
        "write_bridge_consensus_merge_receipt", "merge_with_bridge_receipt",
        "bridge_accepted_queue_preflight", "Write-AgentEvent", "--admin",
    ):
        assert forbidden not in text, forbidden
    # No process launching here: subprocess is absent from the import set above
    # (the evidence-class literal "subprocess_ssh_keygen" is only a label).
    assert "subprocess" not in top
    # The bridge log itself is never named (the local approval-events.jsonl artifact is fine).
    assert re.search(r"(?<![A-Za-z0-9_-])events\.jsonl", text) is None


def test_module_top_level_has_no_side_effect_statements():
    tree_ = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.ClassDef)
    guards = 0
    for index, node in enumerate(tree_.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.If):
            source = ast.unparse(node)
            assert source == "if str(ROOT) not in sys.path:\n    sys.path.insert(0, str(ROOT))", source
            guards += 1
            continue
        assert isinstance(node, allowed), ast.dump(node)[:120]
    assert guards == 1


def test_import_in_fresh_interpreter_creates_no_files(tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
         "import tools.manual_bridge_merge_receipt as m; print(m.RECEIPT_PAYLOAD_SCHEMA)", str(ROOT)],
        cwd=tmp_path, capture_output=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().strip() == "wd.manual-merge-a.receipt-payload.v1"
    assert sorted(p.name for p in tmp_path.iterdir()) == before


@pytest.mark.skip(
    reason=(
        "NOT_RUN: a genuine receipt needs the operator's public key line in the trusted base, a real "
        "operator signature, a real merge and the later merge slice's integration validators; "
        "agents never synthesize any of these"
    )
)
def test_real_genuine_receipt_not_run():
    raise AssertionError("must stay NOT_RUN until genuine operator evidence exists")
