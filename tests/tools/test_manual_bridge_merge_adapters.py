# SPDX-License-Identifier: BUSL-1.1
"""MANUAL-A G3a adapter tests: trusted-base pin, registry, snapshot loop, D1 controls, T12.

Fixtures are local only: a temporary git repository built with ``git init`` +
one commit (no remote, no fetch) and temporary JSONL bridge logs.  Nothing
reads the canonical bridge log, the network, GitHub or a provider.
"""
from __future__ import annotations

import ast
import builtins
import copy
import dataclasses
import json
import os
from pathlib import Path
import socket
import subprocess

import pytest

from tools import manual_bridge_merge_adapters as ad
from tools.manual_bridge_merge_receipt import bridge_event_id
from waggledance.core.bridge_log_reader import BridgeCursor, BridgeReadResult, BridgeReadStatus

ROOT = Path(__file__).resolve().parents[2]
REG = {
    "claude-rco-1": "11111111-1111-4111-8111-111111111111",
    "claude-rco-2": "22222222-2222-4222-8222-222222222222",
    "codex-lead-1": "33333333-3333-4333-8333-333333333333",
    "codex-tools-1": "44444444-4444-4444-8444-444444444444",
    "fable-5": "55555555-5555-4555-8555-555555555555",
}
OTHER_UUID = "66666666-6666-4666-8666-666666666666"
TASK = "fable-5/manual-a-20261005"
PR = 1763
HEAD = "a" * 40
SCOPE = {"task_id": TASK, "pull_request": PR, "head_sha": HEAD, "merging_agent": "operator", "author_agent": "fable-5"}


# --------------------------------------------------------------------------- fixtures


def _git(cwd: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    done = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "user.name=g3a", "-c", "user.email=g3a@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, env=env, capture_output=True, check=True,
    )
    return done.stdout.decode("ascii").strip()


def _registry_bytes(identities=None) -> bytes:
    doc = {"schema_version": 1, "identities": dict(REG if identities is None else identities)}
    return json.dumps(doc, indent=2).encode("utf-8")


def _make_repo(root: Path, *, registry: bytes | None = None, overrides: dict | None = None, omit=()) -> str:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    files = {path: (ROOT / path).read_bytes() for path in ad.PINNED_SOURCE_PATHS}
    files[ad.REGISTRY_PATH] = _registry_bytes() if registry is None else registry
    files.update(overrides or {})
    for path, data in files.items():
        if path in omit:
            continue
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "trusted base fixture")
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def trusted(tmp_path_factory):
    root = tmp_path_factory.mktemp("g3a") / "repo"
    return root, _make_repo(root)


def _ev(agent, type_, status, ts, *, uuid=None, task=TASK, **payload):
    return {
        "ts_utc": ts, "agent": agent, "agent_uuid": REG.get(agent, OTHER_UUID) if uuid is None else uuid,
        "type": type_, "status": status, "task_id": task, "to": "", "message": "", "payload": payload,
    }


def _ts(second: int) -> str:
    return f"2026-10-07T13:00:{second:02d}.0000000Z"


def _write_log(path: Path, events) -> Path:
    path.write_bytes(b"".join(json.dumps(event, ensure_ascii=True).encode("ascii") + b"\n" for event in events))
    return path


def _evidence(trusted, tmp_path, events):
    root, commit = trusted
    sources = ad.verify_trusted_sources(root, commit)
    registry = ad.load_trusted_registry(root, commit)
    snapshot = ad.read_bridge_snapshot(_write_log(tmp_path / "events.jsonl", events))
    return snapshot, registry, sources


def _controls(trusted, tmp_path, events):
    return ad.assess_controls(*_evidence(trusted, tmp_path, events), **SCOPE)


def _block(agent="claude-rco-1", second=1, **kw):
    return _ev(agent, "decision", "changes_requested", _ts(second), **kw)


def _clear(block, *, agent=None, second=5, status="changes_requested_retracted", type_="decision", uuid=None,
           ref=None, head=HEAD):
    agent = agent or block["agent"]
    return _ev(agent, type_, status, _ts(second), uuid=uuid,
               retracts_event_id=bridge_event_id(block) if ref is None else ref, exact_head=head)


# --------------------------------------------------------------------------- trusted-base source pin


def test_source_pin_matches_trusted_commit_and_is_issued(trusted):
    root, commit = trusted
    evidence = ad.verify_trusted_sources(root, commit)
    assert evidence.provenance == ad.PROVENANCE_LOCAL_READ
    assert ad.is_issued(evidence)
    assert [path for path, _ in evidence.matches] == list(ad.PINNED_SOURCE_PATHS)
    assert evidence.trusted_commit == commit


def test_source_pin_refuses_a_different_trusted_blob(tmp_path):
    path = "tools/check_bridge_changes_requested.py"
    commit = _make_repo(tmp_path / "r", overrides={path: (ROOT / path).read_bytes() + b"# drift\n"})
    with pytest.raises(ad.AdapterError) as err:
        ad.verify_trusted_sources(tmp_path / "r", commit)
    assert err.value.reason == "loaded_source_differs_from_trusted_base"


def test_source_pin_refuses_a_missing_trusted_object(tmp_path):
    commit = _make_repo(tmp_path / "r", omit={"tools/bridge_named_mutex.py"})
    with pytest.raises(ad.AdapterError) as err:
        ad.verify_trusted_sources(tmp_path / "r", commit)
    assert err.value.reason == "trusted_blob_missing"


@pytest.mark.parametrize(
    ("commit", "reason"),
    [("b" * 40, "trusted_commit_missing"), ("B" * 40, "trusted_commit_invalid"), ("abc", "trusted_commit_invalid"),
     (None, "trusted_commit_invalid"), ("HEAD", "trusted_commit_invalid")],
)
def test_source_pin_refuses_unknown_or_invalid_commit(trusted, commit, reason):
    with pytest.raises(ad.AdapterError) as err:
        ad.verify_trusted_sources(trusted[0], commit)
    assert err.value.reason == reason


def test_source_pin_accepts_only_exact_crlf_normalization(tmp_path, trusted):
    root, commit = trusted
    source_root = tmp_path / "src"
    for path in ad.PINNED_SOURCE_PATHS:
        target = source_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        blob = _git(root, "cat-file", "-s", f"{commit}:{path}")
        data = subprocess.run(["git", "-C", str(root), "cat-file", "blob", f"{commit}:{path}"], capture_output=True, check=True).stdout
        assert int(blob) == len(data)
        target.write_bytes(data.replace(b"\n", b"\r\n"))
    evidence = ad.verify_trusted_sources(root, commit, source_root=source_root)
    assert {kind for _, kind in evidence.matches} <= {"crlf_normalized", "exact"}
    first = source_root / ad.PINNED_SOURCE_PATHS[1]
    first.write_bytes(first.read_bytes().replace(b"\r\n", b"\n", 1).replace(b"def ", b"def  ", 1))
    with pytest.raises(ad.AdapterError) as err:
        ad.verify_trusted_sources(root, commit, source_root=source_root)
    assert err.value.reason == "loaded_source_differs_from_trusted_base"


def test_injected_git_runner_is_unit_mock(trusted):
    root, commit = trusted
    real = ad._subprocess_runner
    evidence = ad.verify_trusted_sources(root, commit, runner=lambda argv, **kw: real(argv, **kw))
    assert evidence.provenance == ad.PROVENANCE_UNIT_MOCK
    assert not ad.counts_as_local_read(evidence, ad.SourceOriginEvidence)


# --------------------------------------------------------------------------- trusted-base registry


def test_registry_from_trusted_commit_binds_uuid_only(trusted):
    root, commit = trusted
    registry = ad.load_trusted_registry(root, commit)
    assert dict(registry.identities) == REG
    assert registry.blob_sha1 == _git(root, "rev-parse", f"{commit}:{ad.REGISTRY_PATH}")  # the Git object id
    assert registry.provenance == ad.PROVENANCE_LOCAL_READ
    assert not hasattr(registry, "session_id")
    with pytest.raises(TypeError):
        registry.identities["fable-5"] = OTHER_UUID  # read-only mapping


@pytest.mark.parametrize(
    "registry",
    [
        b'{"identities": {"fable-5": "55555555-5555-4555-8555-555555555555", "fable-5": "55555555-5555-4555-8555-555555555555"}}',
        b'{"identities": {"fable-5": "not-a-uuid"}}',
        b'{"identities": {}}',
        b'{"identities": {"fable-5": "55555555-5555-4555-8555-555555555555", "codex-lead-1": "55555555-5555-4555-8555-555555555555"}}',
        b'{"identities": {"fable-5": "aaaaaaaa-5555-4555-8555-555555555555", "codex-lead-1": "AAAAAAAA-5555-4555-8555-555555555555"}}',
        b'{"identities": {"fable-5": NaN}}',
        b'["identities"]',
        b"\xff\xfe",
    ],
)
def test_registry_malformed_refuses(tmp_path, registry):
    commit = _make_repo(tmp_path / "r", registry=registry)
    with pytest.raises(ad.AdapterError) as err:
        ad.load_trusted_registry(tmp_path / "r", commit)
    assert err.value.reason == "registry_invalid"


def test_registry_missing_at_trusted_commit_refuses(tmp_path):
    commit = _make_repo(tmp_path / "r", omit={ad.REGISTRY_PATH})
    with pytest.raises(ad.AdapterError) as err:
        ad.load_trusted_registry(tmp_path / "r", commit)
    assert err.value.reason == "trusted_blob_missing"


# --------------------------------------------------------------------------- bridge snapshot


def test_snapshot_loops_to_complete_end_of_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ad, "SNAPSHOT_MAX_ROWS", 1)
    events = [_block(second=1), _ev("codex-lead-1", "message", "info", _ts(2)), _ev("fable-5", "status", "x", _ts(3))]
    snapshot = ad.read_bridge_snapshot(_write_log(tmp_path / "e.jsonl", events))
    assert [dict(event) for event in snapshot.events] == events
    assert snapshot.read_calls == 4  # three single-row reads and one end-of-file read
    assert snapshot.provenance == ad.PROVENANCE_LOCAL_READ
    assert snapshot.snapshot_length == (tmp_path / "e.jsonl").stat().st_size


@pytest.mark.parametrize(
    "tail",
    [b'{"ts_utc": "x"', b"not json\n", b'{"a": 1, "a": 2}\n', b"[1]\n"],
)
def test_snapshot_refuses_partial_or_malformed_rows(tmp_path, tail):
    log = _write_log(tmp_path / "e.jsonl", [_block()])
    log.write_bytes(log.read_bytes() + tail)
    with pytest.raises(ad.AdapterError) as err:
        ad.read_bridge_snapshot(log)
    assert err.value.reason == "bridge_snapshot_unknown"


def test_snapshot_refuses_missing_log_and_row_limit(tmp_path):
    with pytest.raises(ad.AdapterError):
        ad.read_bridge_snapshot(tmp_path / "absent.jsonl")
    log = _write_log(tmp_path / "e.jsonl", [_block(second=1), _block(second=2)])
    with pytest.raises(ad.AdapterError) as err:
        ad.read_bridge_snapshot(log, max_total_rows=1)
    assert err.value.reason == "bridge_snapshot_unknown"


def test_snapshot_refuses_identity_change_and_marks_injected_reader_mock(tmp_path):
    log = _write_log(tmp_path / "e.jsonl", [_block()])
    calls = []

    def moving(path, **kwargs):
        calls.append(kwargs.get("cursor"))
        if len(calls) == 1:
            return BridgeReadResult(BridgeReadStatus.OK, "", rows=({"a": 1},), candidate_cursor=BridgeCursor(10, "id-1"), snapshot_length=20)
        return BridgeReadResult(BridgeReadStatus.IDLE, "partial_record", candidate_cursor=BridgeCursor(20, "id-2"), snapshot_length=20)

    with pytest.raises(ad.AdapterError) as err:
        ad.read_bridge_snapshot(log, reader=moving)
    assert "identity" in err.value.detail
    from waggledance.core.bridge_log_reader import read_bridge_log

    mock = ad.read_bridge_snapshot(log, reader=read_bridge_log)
    assert mock.provenance == ad.PROVENANCE_UNIT_MOCK


# --------------------------------------------------------------------------- forgery and mutation


def test_caller_built_copied_or_mutated_evidence_never_counts(trusted, tmp_path):
    snapshot, registry, sources = _evidence(trusted, tmp_path, [])
    assert ad.assess_controls(snapshot, registry, sources, **SCOPE)["status"] == ad.CHECK_PASS
    forged = ad.SnapshotEvidence((), "id", None, 0, 1, "0" * 64, ad.PROVENANCE_LOCAL_READ)
    forged_registry = ad.RegistryEvidence(registry.trusted_commit, dict(REG), registry.blob_sha1, "local_read")
    for args in (
        (forged, registry, sources),
        (dataclasses.replace(snapshot), registry, sources),
        (copy.copy(snapshot), registry, sources),
        (snapshot, copy.copy(registry), sources),
        (snapshot, forged_registry, sources),
        (snapshot, registry, dataclasses.replace(sources, provenance=ad.PROVENANCE_LOCAL_READ)),
        (snapshot, registry, None),
        (registry, snapshot, sources),
    ):
        result = ad.assess_controls(*args, **SCOPE)
        assert result["status"] == ad.CHECK_UNKNOWN
        assert result["reason"] == "evidence_not_issued_local_read"


def test_mutating_an_issued_snapshot_payload_revokes_it(trusted, tmp_path):
    snapshot, registry, sources = _evidence(trusted, tmp_path, [_block()])
    assert ad.assess_controls(snapshot, registry, sources, **SCOPE)["status"] == ad.CHECK_REFUSE
    snapshot.events[0]["payload"]["note"] = "edited"  # nested mapping stays a plain dict
    assert not ad.is_issued(snapshot)
    assert ad.assess_controls(snapshot, registry, sources, **SCOPE)["status"] == ad.CHECK_UNKNOWN


def test_registry_and_sources_must_share_the_trusted_commit(trusted, tmp_path):
    other_root = tmp_path / "other"
    other_commit = _make_repo(other_root, registry=_registry_bytes() + b"\n")
    snapshot, registry, _ = _evidence(trusted, tmp_path, [])
    sources = ad.verify_trusted_sources(other_root, other_commit)
    assert ad.assess_controls(snapshot, registry, sources, **SCOPE)["reason"] == "trusted_commit_mismatch"


# --------------------------------------------------------------------------- D1 controls


def test_no_controls_passes_only_the_controls_check(trusted, tmp_path):
    result = _controls(trusted, tmp_path, [_ev("codex-lead-1", "message", "requested", _ts(1))])
    assert result["status"] == ad.CHECK_PASS
    assert result["canonical_decision"] == "clear"


def test_block_then_later_pass_stays_blocked(trusted, tmp_path):
    block = _block()
    later_pass = _ev("claude-rco-1", "decision", "rco_pass", _ts(9), exact_head=HEAD)
    other_pass = _ev("claude-rco-2", "decision", "rco_pass", _ts(9), exact_head=HEAD)
    result = _controls(trusted, tmp_path, [block, later_pass, other_pass])
    assert result["status"] == ad.CHECK_REFUSE
    assert result["local_blocking_ids"] == [bridge_event_id(block)]
    assert result["canonical_decision"] == "clear"  # the canonical gate alone would have cleared


def test_canonical_clear_with_exact_local_reference_passes(trusted, tmp_path):
    block = _block()
    for status in sorted(ad.LOCAL_CLEAR_STATUSES):
        for type_ in ("decision", "rco_review"):
            result = _controls(trusted, tmp_path, [block, _clear(block, status=status, type_=type_)])
            assert result["status"] == ad.CHECK_PASS, (status, type_, result)
            assert result["local_blocking_ids"] == []


def test_finding_typed_block_needs_the_same_exact_clear(trusted, tmp_path):
    block = _ev("claude-rco-2", "finding", "changes_requested", _ts(1))
    assert _controls(trusted, tmp_path, [block])["status"] == ad.CHECK_REFUSE
    assert _controls(trusted, tmp_path, [block, _clear(block)])["status"] == ad.CHECK_PASS


@pytest.mark.parametrize(
    "variant",
    [
        "no_reference", "wrong_reference", "other_rco", "earlier_time", "equal_time", "unparseable_time",
        "wrong_head", "missing_head", "finding_type", "g2_vocabulary", "free_text", "block_retracted",
        "non_canonical_clear", "clear_before_block", "foreign_uuid", "payload_not_object",
    ],
)
def test_withdrawal_twins_never_clear(trusted, tmp_path, variant):
    block = _block(second=5)
    clear = _clear(block, second=9)
    events = [block, clear]
    if variant == "no_reference":
        del clear["payload"]["retracts_event_id"]
    elif variant == "wrong_reference":
        clear["payload"]["retracts_event_id"] = "f" * 64
    elif variant == "other_rco":
        events = [block, _clear(block, agent="claude-rco-2", second=9)]
    elif variant == "earlier_time":
        clear["ts_utc"] = _ts(4)
    elif variant == "equal_time":
        clear["ts_utc"] = _ts(5)
    elif variant == "unparseable_time":
        clear["ts_utc"] = "2026-10-07 13:00:09"
    elif variant == "wrong_head":
        clear["payload"]["exact_head"] = "b" * 40
    elif variant == "missing_head":
        del clear["payload"]["exact_head"]
    elif variant == "finding_type":
        clear["type"] = "finding"
    elif variant == "g2_vocabulary":
        clear["status"] = "finding_retracted"
    elif variant == "free_text":
        clear["status"] = "retracted after discussion"
    elif variant == "block_retracted":
        clear["status"] = "block_retracted"
    elif variant == "non_canonical_clear":
        clear["status"] = "changes_requested_resolved_ci_green"  # canonical clear, not a local clear status
    elif variant == "clear_before_block":
        events = [clear, block]
    elif variant == "foreign_uuid":
        clear["agent_uuid"] = OTHER_UUID
    elif variant == "payload_not_object":
        clear["payload"] = "retracts " + bridge_event_id(block)
    result = _controls(trusted, tmp_path, events)
    assert result["status"] == ad.CHECK_REFUSE, result


def test_local_strictness_kills_canonical_only_clears(trusted, tmp_path):
    # Canonical gate clears each of these (order-based, same agent, verified clear); the local rule does not.
    block_unverified = _block(second=5, uuid=OTHER_UUID)  # latches canonically although unverified
    verified_clear = _clear(block_unverified, second=9, uuid=REG["claude-rco-1"])
    result = _controls(trusted, tmp_path, [block_unverified, verified_clear])
    assert result["canonical_decision"] == "clear"
    assert result["status"] == ad.CHECK_REFUSE
    block = _block(second=5)
    earlier_stamp = _clear(block, second=4)  # later in file order, earlier timestamp
    result = _controls(trusted, tmp_path, [block, earlier_stamp])
    assert result["canonical_decision"] == "clear"
    assert result["status"] == ad.CHECK_REFUSE
    wrong_ref = _clear(block, second=9, ref="e" * 64)
    result = _controls(trusted, tmp_path, [block, wrong_ref])
    assert result["canonical_decision"] == "clear"
    assert result["status"] == ad.CHECK_REFUSE
    wrong_head = _clear(block, second=9, head="c" * 40)
    result = _controls(trusted, tmp_path, [block, wrong_head])
    assert result["canonical_decision"] == "clear"
    assert result["status"] == ad.CHECK_REFUSE


def test_local_clear_needs_the_trusted_registry_uuid(trusted, tmp_path):
    # Canonical: the verified unreferenced clear lifts the unverified block (the unverified clear is ignored).
    # Local: only the unverified clear references the block, and its uuid is not the registry uuid.
    block = _block(second=5, uuid=OTHER_UUID)
    verified_unreferenced = _clear(block, second=7, uuid=REG["claude-rco-1"], ref="d" * 64)
    unverified_referenced = _clear(block, second=9, uuid=OTHER_UUID)
    result = _controls(trusted, tmp_path, [block, verified_unreferenced, unverified_referenced])
    assert result["canonical_decision"] == "clear"
    assert result["status"] == ad.CHECK_REFUSE
    assert result["local_blocking_ids"] == [bridge_event_id(block)]


def test_canonical_block_refuses_even_when_local_rule_is_clear(trusted, tmp_path):
    peer_block = _ev("codex-tools-1", "decision", "changes_requested", _ts(3))  # not an RCO: local ignores it
    result = _controls(trusted, tmp_path, [peer_block])
    assert result["local_blocking_ids"] == []
    assert result["canonical_decision"] == "blocked"
    assert result["status"] == ad.CHECK_REFUSE


def test_registry_disagreeing_with_event_uuid_refuses(trusted, tmp_path):
    block = _block(second=5, uuid=OTHER_UUID)
    clear = _clear(block, second=9, uuid=OTHER_UUID)  # same agent and uuid, but not the registry uuid
    assert _controls(trusted, tmp_path, [block, clear])["status"] == ad.CHECK_REFUSE


def test_replayed_block_reopens_and_order_changes_digest(trusted, tmp_path):
    block = _block()
    clear = _clear(block)
    reopened = _controls(trusted, tmp_path, [block, clear, block])
    assert reopened["status"] == ad.CHECK_REFUSE
    forward = _controls(trusted, tmp_path, [block, clear])
    backward = _controls(trusted, tmp_path, [clear, block])
    assert forward["status"] == ad.CHECK_PASS and backward["status"] == ad.CHECK_REFUSE
    assert forward["ordered_controls_sha256"] != backward["ordered_controls_sha256"]


@pytest.mark.parametrize("status", [None, 7, "", "changes requested", "CHANGES_REQUESTED_RETRACTED"])
def test_malformed_or_unknown_rco_control_blocks(trusted, tmp_path, status):
    event = _ev("claude-rco-1", "decision", status, _ts(1))
    assert _controls(trusted, tmp_path, [event])["status"] == ad.CHECK_REFUSE


@pytest.mark.parametrize(
    "scope",
    [{"task": "unrelated/task", "pr": 1763}, {"task": "unrelated/task", "pr": "PR-1763"}, {"task": "unrelated/task", "pr": 1763.0},
     {"task": "fable-5-manual-a-20261005"}, {"task": "review of #1763"}],
)
def test_block_scope_union_is_conservative(trusted, tmp_path, scope):
    payload = {"pr": scope["pr"]} if "pr" in scope else {}
    event = _ev("claude-rco-2", "decision", "changes_requested", _ts(1), task=scope["task"], **payload)
    assert _controls(trusted, tmp_path, [event])["status"] == ad.CHECK_REFUSE


def test_out_of_scope_block_is_not_a_control(trusted, tmp_path):
    event = _ev("claude-rco-2", "decision", "changes_requested", _ts(1), task="unrelated/task", pr=17630)
    assert _controls(trusted, tmp_path, [event])["status"] == ad.CHECK_PASS


@pytest.mark.parametrize("bad", [{"pull_request": 0}, {"pull_request": "1763"}, {"head_sha": "A" * 40}, {"task_id": ""}])
def test_invalid_scope_refuses(trusted, tmp_path, bad):
    snapshot, registry, sources = _evidence(trusted, tmp_path, [])
    assert ad.assess_controls(snapshot, registry, sources, **{**SCOPE, **bad})["status"] == ad.CHECK_REFUSE


# --------------------------------------------------------------------------- G3-T12 combined


def test_t12_every_read_check_succeeds_and_the_route_stays_unknown(trusted, tmp_path):
    block = _block()
    snapshot, registry, sources = _evidence(trusted, tmp_path, [block, _clear(block)])
    report = ad.assess_g3a(snapshot, registry, sources, **SCOPE)
    by_name = {check["check"]: check for check in report["checks"]}
    for name in ("trusted_source_pin", "trusted_registry", "bridge_snapshot_observed", "rco_controls"):
        assert by_name[name]["status"] == ad.CHECK_PASS, by_name[name]
    for name, reason in ad.TRUST_GAPS:
        assert by_name[name] == {"check": name, "status": ad.CHECK_UNKNOWN, "reason": reason}
    assert report["verdict"] == ad.VERDICT_UNKNOWN
    assert report["admitted"] is False and ad.ADMIT_AVAILABLE is False
    assert set(check["status"] for check in report["checks"]) <= {ad.CHECK_PASS, ad.CHECK_UNKNOWN}
    text = json.dumps(report)
    assert "admitted\": true" not in text and "rco_pass" not in text and "build_consensus_pass" not in text


def test_t12_caller_provenance_and_injected_runner_stay_unknown(trusted, tmp_path):
    root, commit = trusted
    real = ad._subprocess_runner
    sources = ad.verify_trusted_sources(root, commit, runner=lambda argv, **kw: real(argv, **kw))
    registry = ad.load_trusted_registry(root, commit)
    snapshot = ad.read_bridge_snapshot(_write_log(tmp_path / "e.jsonl", []))
    report = ad.assess_g3a(snapshot, registry, sources, **SCOPE)
    statuses = {check["check"]: check["status"] for check in report["checks"]}
    assert statuses["trusted_source_pin"] == ad.CHECK_UNKNOWN
    assert statuses["rco_controls"] == ad.CHECK_UNKNOWN
    assert report["verdict"] == ad.VERDICT_UNKNOWN and report["admitted"] is False


def test_t12_any_refusal_is_the_verdict(trusted, tmp_path):
    report = ad.assess_g3a(*_evidence(trusted, tmp_path, [_block()]), **SCOPE)
    assert report["verdict"] == ad.VERDICT_REFUSED and report["admitted"] is False


# --------------------------------------------------------------------------- boundary and effects


@pytest.mark.parametrize(
    "args",
    [
        ["fetch", "origin"], ["cat-file", "blob", "a" * 40 + ":README.md"], ["cat-file", "-p", "a" * 40 + ":" + ad.REGISTRY_PATH],
        ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], ["rev-parse", "--verify", "--quiet", "a" * 40 + "^{commit}", "x"],
        ["cat-file", "blob", "HEAD:" + ad.REGISTRY_PATH], ["-c", "core.fsmonitor=x", "rev-parse"],
    ],
)
def test_git_allowlist_refuses_everything_else(args):
    with pytest.raises(ad.AdapterError) as err:
        ad.require_read_only_git(["git", "-C", "repo", "--no-replace-objects", *args])
    assert err.value.reason == "effect_refused"
    with pytest.raises(ad.AdapterError):
        ad.require_read_only_git(["git", "-C", "repo", "rev-parse", "--verify", "--quiet", "a" * 40 + "^{commit}", "x"])
    with pytest.raises(ad.AdapterError):
        ad.require_read_only_git(["git", "-C", "repo", "--no-replace-objects", "cat-file", "blob", 7])


def test_import_boundary_and_unwired():
    module = ROOT / "tools" / "manual_bridge_merge_adapters.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    allowed_roots = {
        "__future__", "dataclasses", "datetime", "hashlib", "json", "os", "pathlib", "re", "subprocess", "sys",
        "types", "typing", "weakref", "tools", "waggledance",
    }
    allowed_internal = {
        "tools.check_bridge_changes_requested", "tools.manual_bridge_merge_receipt",
        "tools.manual_bridge_merge_statement", "waggledance.core.bridge_identity_registry",
        "waggledance.core.bridge_log_reader",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in allowed_roots - {"tools", "waggledance"} for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module.split(".")[0] in allowed_roots, node.module
            if node.module.split(".")[0] in {"tools", "waggledance"}:
                assert node.module in allowed_internal, node.module
                assert not any(alias.name.startswith("_") for alias in node.names), node.module
    for route in ("manual_bridge_merge.py", "manual_bridge_merge_statement.py", "manual_bridge_merge_receipt.py"):
        assert "manual_bridge_merge_adapters" not in (ROOT / "tools" / route).read_text(encoding="utf-8")


def test_pinned_paths_close_over_their_repository_imports():
    pinned = set(ad.PINNED_SOURCE_PATHS)
    for path in ad.PINNED_SOURCE_PATHS:
        tree = ast.parse((ROOT / path).read_bytes())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                if name.split(".")[0] in {"tools", "waggledance"}:
                    parts = name.split(".")
                    candidates = {"/".join(parts) + ".py", "/".join(parts) + "/__init__.py"}
                    assert candidates & pinned, (path, name)
            assert not (isinstance(node, ast.ImportFrom) and node.level), (path, "relative import")


def test_live_flow_has_no_network_write_or_unlisted_process(trusted, tmp_path, monkeypatch):
    block = _block()
    log = _write_log(tmp_path / "e.jsonl", [block, _clear(block)])
    seen: list[list[str]] = []
    real_run = subprocess.run

    def run_spy(argv, *args, **kwargs):
        ad.require_read_only_git(list(argv))
        seen.append(list(argv))
        return real_run(argv, *args, **kwargs)

    def no_socket(*_args, **_kwargs):
        raise AssertionError("network attempted")

    real_open, real_os_open = builtins.open, os.open

    def read_only_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            raise AssertionError(f"write attempted: {file}")
        return real_open(file, mode, *args, **kwargs)

    def read_only_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
            raise AssertionError(f"write attempted: {path}")
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(ad.subprocess, "run", run_spy)
    monkeypatch.setattr(socket, "socket", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    monkeypatch.setattr(builtins, "open", read_only_open)
    monkeypatch.setattr(os, "open", read_only_os_open)
    root, commit = trusted
    report = ad.assess_g3a(
        ad.read_bridge_snapshot(log), ad.load_trusted_registry(root, commit), ad.verify_trusted_sources(root, commit), **SCOPE
    )
    assert report["verdict"] == ad.VERDICT_UNKNOWN
    assert seen and {argv[4] for argv in seen} == {"rev-parse", "cat-file"}
