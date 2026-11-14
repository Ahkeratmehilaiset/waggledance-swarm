"""Synthetic-only projection witness contracts; no real bridge data."""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from tools import wd_mind_content_query_readonly as content


def _fixture_module():
    path = Path(__file__).with_name("test_wd_mind_content_query_readonly.py")
    spec = importlib.util.spec_from_file_location("witness_synthetic_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _subject():
    try:
        from tools import wd_mind_projection_witness
    except ImportError:
        pytest.fail("projection witness implementation missing")
    return wd_mind_projection_witness


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _case(tmp_path, rows=None):
    fixtures = _fixture_module()
    if rows is None:
        rows = [fixtures._row()]
    return fixtures, fixtures._all_tasks_case(content, tmp_path, rows)


def test_build_and_verify_rederive_the_complete_exact_projection(tmp_path):
    subject = _subject()
    _, case = _case(tmp_path)
    result = subject.build_projection_witness(**case)
    expected = content.rederive_message_projection(**case)
    projection_bytes = content._serialized(expected)
    assert result["projection_bytes"] == projection_bytes
    assert result["projection_sha256"] == _sha(projection_bytes)
    assert result["witness_sha256"] == _sha(result["witness_bytes"])
    witness = json.loads(result["witness_bytes"])
    assert witness["projection_sha256"] == result["projection_sha256"]
    assert witness["source_anchor"]["file_identity"] == case["messages"]["end_cursor"].file_identity
    assert witness["claims"]["source_consistency"] == "MATCH_AT_REDERIVATION"
    assert witness["claims"]["sender_authentication"] == "UNKNOWN"
    assert witness["claims"]["coverage"] == witness["claims"]["same_user_aba"] == "UNKNOWN"
    assert witness["source_anchor"]["event_time_cutoff_utc"] is None
    assert witness["claims"]["source_cutoff"] == "UNKNOWN"
    assert subject.verify_projection_witness(**result, **case)["status"] == "MATCH_AT_REDERIVATION"


def test_no_raw_copy_review_mentions_allowed_and_rco_and_secret_rows_excluded(tmp_path):
    subject = _subject()
    f = _fixture_module()
    clean = f._row("Review found a fix", cwd="C:\\Synthetic\\private-location",
                   run_id="synthetic-internal-run-not-for-output", payload={"summary": "nested-private-synthetic"})
    rco_authored = f._row("RCO-authored synthetic excluded", agent="claude-rco-1", agent_uuid=f.ROSTER["claude-rco-1"])
    rco_recipient = f._row("RCO-addressed synthetic excluded", to="claude-rco-1")
    credential = f._row("sk-123456789abcdef")
    heldout = f._row("heldout synthetic marker")
    _, case = _case(tmp_path, [clean, rco_authored, rco_recipient, credential, heldout])
    result = subject.build_projection_witness(**case)
    projection = json.loads(result["projection_bytes"])
    witness = json.loads(result["witness_bytes"])
    records = projection["messages"]["records"]
    assert records and any("Review" in row["stored_text"] for row in records)
    assert witness["source_event_refs"] == sorted({row["provenance"]["source_event_ref"] for row in records})
    serialized = result["projection_bytes"] + result["witness_bytes"]
    for forbidden in (b"private-location", b"synthetic-internal-run-not-for-output", b"nested-private-synthetic",
                      b"RCO-authored synthetic excluded", b"RCO-addressed synthetic excluded", b"sk-123456789abcdef",
                      b"heldout synthetic marker", f._raw(clean)):
        assert forbidden not in serialized
    assert f._sha(f._raw(clean)) in witness["source_event_refs"]


@pytest.mark.parametrize("kind", ["witness", "projection", "repinned_witness", "repinned_projection"])
def test_tampered_artifacts_never_validate_even_when_caller_repins(tmp_path, kind):
    subject = _subject()
    _, case = _case(tmp_path)
    result = subject.build_projection_witness(**case)
    result = dict(result)
    key = "witness" if "witness" in kind else "projection"
    value = json.loads(result[key + "_bytes"])
    if key == "witness": value["source_event_refs"] = ["0" * 64]
    else: value["messages"]["records"][0]["stored_text"] = "forged"
    altered = json.dumps(value, sort_keys=True, indent=1).encode()
    result[key + "_bytes"] = altered
    if kind.startswith("repinned_"): result[key + "_sha256"] = _sha(altered)
    with pytest.raises(subject.ProjectionWitnessRefused, match="^projection_witness_refused$"):
        subject.verify_projection_witness(**result, **case)


@pytest.mark.parametrize("fault", ["generation", "identity", "offset", "prefix_digest", "source_missing"])
def test_anchor_changes_refuse_without_partial_output(tmp_path, fault):
    subject = _subject()
    f, case = _case(tmp_path)
    if fault == "generation": Path(case["messages"]["generation_path"]).write_text('{"generation":"g2"}')
    elif fault == "identity":
        end = case["messages"]["end_cursor"]
        case["messages"]["end_cursor"] = dataclasses.replace(end, file_identity="wrong")
    elif fault == "offset":
        end = case["messages"]["end_cursor"]
        case["messages"]["end_cursor"] = dataclasses.replace(end, offset=end.offset - 1)
    elif fault == "prefix_digest":
        decision = json.loads(case["decision_bytes"])
        decision["source_prefix"]["sha256"] = "0" * 64
        f._edit_decision(case, source_prefix=decision["source_prefix"])
    elif fault == "source_missing": Path(case["messages"]["source_path"]).unlink()
    with pytest.raises(subject.ProjectionWitnessRefused, match="^projection_witness_refused$"):
        subject.build_projection_witness(**case)


def test_source_change_between_derivation_and_final_check_refuses(tmp_path, monkeypatch):
    subject = _subject()
    _, case = _case(tmp_path)
    original = content.validate_content_snapshot
    def rotate_generation(**inputs):
        Path(case["messages"]["generation_path"]).write_text('{"generation":"g2"}')
        return original(**inputs)
    monkeypatch.setattr(content, "validate_content_snapshot", rotate_generation)
    with pytest.raises(subject.ProjectionWitnessRefused):
        subject.build_projection_witness(**case)


def test_duplicate_canonical_event_has_one_source_reference(tmp_path):
    subject = _subject()
    f = _fixture_module()
    row = f._row()
    _, case = _case(tmp_path, [row, copy.deepcopy(row)])
    result = subject.build_projection_witness(**case)
    assert json.loads(result["witness_bytes"])["source_event_refs"] == [f._sha(f._raw(row))]


def test_removed_source_ref_and_nested_rco_route_never_enter_projection(tmp_path):
    subject = _subject()
    f = _fixture_module()
    removed = f._row("Approved message to remove")
    nested_rco = f._row("RCO routing must remain excluded", payload={"metadata": {"to": "claude-rco-1"}})
    _, case = _case(tmp_path, [removed, nested_rco])
    policy = json.loads(case["messages"]["policy_bytes"])
    policy["removed_source_refs"] = [f._sha(f._raw(removed))]
    policy_bytes = f._raw(policy)
    case["messages"].update(policy_bytes=policy_bytes, policy_sha256=f._sha(policy_bytes))
    f._edit_decision(case, message_policy_sha256=case["messages"]["policy_sha256"])
    result = subject.build_projection_witness(**case)
    projection, witness = json.loads(result["projection_bytes"]), json.loads(result["witness_bytes"])
    assert projection["messages"]["records"] == []
    assert witness["record_count"] == 0 and witness["source_event_refs"] == []
    # MATCH demonstrates source consistency only; the consumer must separately
    # enforce a nonzero/usefulness requirement before declaring adoption.
    assert subject.verify_projection_witness(**result, **case)["status"] == "MATCH_AT_REDERIVATION"


def test_build_and_verify_each_do_two_complete_rederivations(tmp_path, monkeypatch):
    subject = _subject()
    _, case = _case(tmp_path)
    original = content._projection
    calls = []
    def counted(context):
        calls.append(1)
        return original(context)
    monkeypatch.setattr(content, "_projection", counted)
    result = subject.build_projection_witness(**case)
    assert len(calls) == 2
    subject.verify_projection_witness(**result, **case)
    assert len(calls) == 4


def test_rotation_after_build_prevents_later_verification(tmp_path):
    subject = _subject()
    _, case = _case(tmp_path)
    result = subject.build_projection_witness(**case)
    source = Path(case["messages"]["source_path"])
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(source.read_bytes())
    os.replace(replacement, source)
    with pytest.raises(subject.ProjectionWitnessRefused):
        subject.verify_projection_witness(**result, **case)


def test_mutated_source_after_build_prevents_later_verification(tmp_path):
    subject = _subject()
    _, case = _case(tmp_path)
    result = subject.build_projection_witness(**case)
    source = Path(case["messages"]["source_path"])
    original = source.read_bytes()
    source.write_bytes(original.replace(b"parser", b"PARSeR"))
    with pytest.raises(subject.ProjectionWitnessRefused):
        subject.verify_projection_witness(**result, **case)


def test_build_writes_no_raw_or_other_files(tmp_path, monkeypatch):
    import builtins
    import socket
    import subprocess

    subject = _subject()
    _, case = _case(tmp_path)
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    builtin_open, path_open, os_open = builtins.open, Path.open, os.open
    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            raise AssertionError("unexpected write")
        return builtin_open(file, mode, *args, **kwargs)
    def guarded_path_open(path, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            raise AssertionError("unexpected write")
        return path_open(path, mode, *args, **kwargs)
    def guarded_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise AssertionError("unexpected write")
        return os_open(path, flags, *args, **kwargs)
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected external operation")
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = subject.build_projection_witness(**case)
    assert result["projection_bytes"] and result["witness_bytes"]
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
