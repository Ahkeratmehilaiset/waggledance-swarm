"""Synthetic-only tests for the independently pinned source anchor issuer."""

import hashlib
import json
import ntpath
import os
from dataclasses import replace

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from waggledance.core.bridge_log_reader import BridgeCursor, read_bridge_log
from tools import wd_mind_anchor_issuer as issuer


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def fields(tmp_path):
    source = tmp_path / "synthetic.jsonl"
    mind_state = tmp_path / "mind-state"
    mind_state.mkdir()
    generation = mind_state / "generation.json"
    source.write_bytes(b"")
    generation.write_bytes(raw({"generation": "synthetic-generation"}))
    cursor = read_bridge_log(source, generation_path=generation).candidate_cursor
    assert type(cursor) is BridgeCursor
    agents = [("codex-lead-1", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
              ("claude-rco-1", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
              ("claude-rco-2", "cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
              ("fable-5", "dddddddd-dddd-4ddd-8ddd-dddddddddddd")]
    policy = raw({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": ["synthetic-task"]})
    roster = raw({"schema": "wd.q4-roster-snapshot.v1", "lanes": [
        {"agent": a, "agent_uuid": u} for a, u in agents], "tools_supervisor": {
        "agent": "codex-tools-1", "agent_uuid": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}})
    public = raw({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["synthetic-task"],
                  "public_types": ["message"]})
    return dict(source_path=str(source), generation_path=str(generation), cursor=cursor,
                policy_bytes=policy, policy_sha256=digest(policy), roster_bytes=roster,
                roster_sha256=digest(roster), public_policy_bytes=public, public_policy_sha256=digest(public))


def test_default_off_and_no_live_io(fields, monkeypatch):
    with pytest.raises(ValueError):
        issuer.issue_anchor(**fields)
    monkeypatch.setattr("builtins.open", lambda *a, **k: pytest.fail("issuer must not open paths"))
    output, sha = issuer.issue_anchor(enabled=True, **fields)
    assert digest(output) == sha
    assert json.loads(output)["status_mode"] == "public-v1"


def test_anchor_is_accepted_by_existing_reader(fields):
    output, sha = issuer.issue_anchor(enabled=True, **fields)
    result = snapshot.read_anchored_delta(fields["source_path"], fields["cursor"], frozenset({"synthetic-task"}),
        anchor_bytes=output, anchor_sha256=sha, generation_path=fields["generation_path"], max_bytes=4096,
        max_rows=20, **{k: fields[k] for k in ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256",
                                            "public_policy_bytes", "public_policy_sha256")})
    assert result["candidate_cursor"] == fields["cursor"]


@pytest.mark.parametrize("key", ["policy_sha256", "roster_sha256", "public_policy_sha256"])
def test_invalid_or_mismatched_pins_refused(fields, key):
    for invalid in ("0" * 64, "a" * 63, "G" * 64, None):
        with pytest.raises(ValueError):
            issuer.issue_anchor(enabled=True, **{**fields, key: invalid})


@pytest.mark.parametrize("cursor", [None, BridgeCursor(0, "identity", None), BridgeCursor(-1, "identity", "gen"),
                                    BridgeCursor(0, "identity", "invalid generation"),
                                    BridgeCursor(True, "identity", "gen"), BridgeCursor(0, "", "gen")])
def test_invalid_cursor_refused(fields, cursor):
    with pytest.raises(ValueError):
        issuer.issue_anchor(enabled=True, **{**fields, "cursor": cursor})


def test_offset_outside_signed_64bit_is_refused(fields):
    cursor = replace(fields["cursor"], offset=2**63)
    with pytest.raises(ValueError, match="^anchor_input_invalid$"):
        issuer.issue_anchor(enabled=True, **{**fields, "cursor": cursor})


@pytest.mark.parametrize("which", ["relative", "dot", "same"])
def test_noncanonical_paths_and_lexically_identical_paths_refused(fields, which):
    bad = fields.copy()
    if which == "relative":
        bad["source_path"] = "source.jsonl"
    elif which == "dot":
        bad["source_path"] = os.path.join(os.path.dirname(bad["source_path"]), ".", "synthetic.jsonl")
    else:
        bad["generation_path"] = bad["source_path"]
    with pytest.raises(ValueError):
        issuer.issue_anchor(enabled=True, **bad)


def test_generation_in_source_directory_is_refused(fields):
    source_parent = os.path.dirname(fields["source_path"])
    generation = os.path.join(source_parent, "mind-epoch.json")
    with pytest.raises(ValueError, match="^anchor_input_invalid$"):
        issuer.issue_anchor(enabled=True, **{**fields, "generation_path": generation})


@pytest.mark.skipif(os.name != "nt", reason="Windows path names are case-insensitive")
def test_source_directory_case_variant_is_still_refused(fields):
    source_parent = os.path.dirname(fields["source_path"]).swapcase()
    with pytest.raises(ValueError, match="^anchor_input_invalid$"):
        issuer.issue_anchor(enabled=True, **{**fields, "generation_path": os.path.join(source_parent, "epoch.json")})


def test_generation_in_separate_directory_is_accepted(fields):
    assert issuer.issue_anchor(enabled=True, **fields)[0]


@pytest.mark.parametrize("name", ["events.generation.json", pytest.param("EVENTS.GENERATION.JSON",
    marks=pytest.mark.skipif(os.name != "nt", reason="Windows path names are case-insensitive"))])
def test_reserved_bridge_generation_basename_refused_in_any_directory(fields, tmp_path, name):
    state = tmp_path / "other-mind-state"
    state.mkdir()
    with pytest.raises(ValueError, match="^anchor_input_invalid$"):
        issuer.issue_anchor(enabled=True, **{**fields, "generation_path": str(state / name)})


@pytest.mark.parametrize("path", [r"\\server\share\events.jsonl", r"\\?\C:\logs\events.jsonl",
    r"\\.\C:\logs\events.jsonl", r"C:\logs\events.jsonl:stream", r"C:\logs\CON.jsonl",
    "C:\\logs\\events.jsonl.", "C:\\logs\\events.jsonl ", r"C:logs\events.jsonl",
    r"C:\logs\even?ts.jsonl", "C:\\"])
def test_windows_lexical_policy_rejects_unc_device_ads_and_unsupported_forms(path):
    with pytest.raises(ValueError, match="^anchor_input_invalid$"):
        issuer._windows_path(path)


@pytest.mark.parametrize("path", [r"C:\logs\events.jsonl", r"D:\data\generation.json"])
def test_windows_lexical_policy_accepts_canonical_drive_paths_without_io(path):
    assert issuer._windows_path(path) == path


@pytest.mark.skipif(os.name != "nt", reason="Windows drive-path input validation")
@pytest.mark.parametrize("field", ["source_path", "generation_path"])
@pytest.mark.parametrize("path", [r"\\server\share\events.jsonl", r"\\?\C:\logs\events.jsonl",
                                   r"\\.\C:\logs\events.jsonl", r"C:\logs\events.jsonl:stream"])
def test_windows_issuer_rejects_unsafe_forms_in_both_path_fields(fields, field, path):
    with pytest.raises(ValueError, match="^anchor_input_invalid$"):
        issuer.issue_anchor(enabled=True, **{**fields, field: path})


@pytest.mark.parametrize("name", ["NUL .txt", "CONIN$", "CONOUT$", "COM\u00b9.txt", "LPT\u00b2.txt"])
@pytest.mark.parametrize("field", ["source_path", "generation_path"])
def test_windows_reserved_device_variants_refuse_both_fields(fields, field, name):
    path = "C:\\logs\\" + name
    if hasattr(ntpath, "isreserved"):
        assert ntpath.isreserved(path)
    if os.name == "nt":
        with pytest.raises(ValueError, match="^anchor_input_invalid$"):
            issuer.issue_anchor(enabled=True, **{**fields, field: path})
    else:
        with pytest.raises(ValueError, match="^anchor_input_invalid$"):
            issuer._windows_path(path)


def test_public_policy_must_be_subset_of_nonreview_policy(fields):
    public = raw({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["unapproved"],
                  "public_types": ["message"]})
    with pytest.raises(ValueError):
        issuer.issue_anchor(enabled=True, **{**fields, "public_policy_bytes": public,
                                           "public_policy_sha256": digest(public)})
