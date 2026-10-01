"""F19 W2: the advisory reader loads ONLY caller-explicit files, once each, and never invents a profile,
a subject, a signature or readiness (Lead 20:10:11Z)."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tools.wd_routing_reader as wr
from tools.wd_composer_select import digest

NOW = datetime(2026, 9, 30, 20, 0, 0, tzinfo=timezone.utc)
CODEX_SUBJECT = "b" * 64


def _write(path: Path, value=None, raw: bytes | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw if raw is not None else (json.dumps(value, indent=2) + "\n").encode("utf-8"))
    return str(path)


def _stamp(when: datetime) -> str:
    """The checkpoint writer's .NET 'o' format: seven fraction digits and +00:00."""
    return when.strftime("%Y-%m-%dT%H:%M:%S") + ".0000000+00:00"


def _checkpoint(agent: str, updated: datetime = NOW, **extra) -> dict:
    record = {"schema": "wd.lane-current.v1", "updated_at_utc": _stamp(updated), "agent": agent,
              "task_id": "codex-lead-1/example", "status": "working", "worktree": "C:/w", "branch": "b",
              "head": "0" * 40, "write_scope": [], "dirty_paths": [], "tests": [], "bridge_evidence": [],
              "blockers": [], "next_action": "continue", "next_wakeup_utc": None}
    record.update(extra)
    return record


def _codex_row() -> dict:
    return {"schema": "wd.capacity-observation.v1", "provider": "codex", "auth_context_id": CODEX_SUBJECT,
            "freshness": "fresh", "reason": None}


def _documents(root: Path, **overrides) -> dict:
    values = {
        "task": {"task_id": "t-1", "revision": 3, "input_digest": "a" * 64, "scope": ["tools/x.py"],
                 "task_class": "impl"},
        "capacity_status": {"schema": "wd.capacity-status.v1", "observed_at": NOW.isoformat(),
                            "execution_allowed": False, "observations": [_codex_row()]},
        "paced": {"windows": []},
        "prepared_artifacts": [],
        "routing_policy": {"policy": "example"},
    }
    values.update(overrides)
    return {name: _write(root / f"{name}.json", value) for name, value in values.items()}


def _paths(root: Path, lanes: dict | None = None, **overrides) -> dict:
    paths = _documents(root, **overrides)
    paths["lanes"] = lanes if lanes is not None else {
        "fable-5": _write(root / "lanes/fable-5.json", _checkpoint("fable-5")),
        "codex-lead-1": _write(root / "lanes/codex-lead-1.json", _checkpoint("codex-lead-1"))}
    return paths


def _spy_opens(monkeypatch) -> list[str]:
    opened: list[str] = []
    real = open

    def spy(file, *args, **kwargs):
        opened.append(os.fspath(file))
        return real(file, *args, **kwargs)

    monkeypatch.setattr(wr, "open", spy, raising=False)
    return opened


def _lane(result: dict, worker: str) -> dict:
    reports = [lane for lane in result["lanes"] if lane["worker"] == worker]
    assert len(reports) == 1
    return reports[0]


def _advisory(result: dict) -> None:
    assert result["schema"] == wr.SCHEMA
    assert result["authority"] == "none" and result["execution_allowed"] is False
    assert result["composed"]["authority"] == "none" and result["composed"]["execution_allowed"] is False
    assert result["assembled"]["inputs"]["shadow_weights"] is None
    assert result["assembled"]["provenance"]["signed_policy"]["signature_verified"] is False


# --- red fixtures ---------------------------------------------------------------------------------

def test_a_case_variant_agent_is_foreign_never_the_lane(tmp_path):
    lanes = {"fable-5": _write(tmp_path / "lanes/a.json", _checkpoint("FABLE-5")),
             "FABLE-5": _write(tmp_path / "lanes/b.json", _checkpoint("FABLE-5"))}
    result = wr.read_routing_inputs(_paths(tmp_path, lanes), NOW)
    _advisory(result)
    assert "checkpoint_agent_mismatch" in _lane(result, "fable-5")["reasons"]
    assert _lane(result, "FABLE-5")["reasons"] == ["foreign_worker"]
    assert result["assembled"]["inputs"]["workers"] == [] and result["assembled"]["inputs"]["subjects"] == {}


@pytest.mark.parametrize(("updated", "reason"), [
    (NOW - timedelta(seconds=901), "lane_evidence_stale"),
    (NOW + timedelta(seconds=1), "lane_evidence_future"),
])
def test_stale_and_future_checkpoints_are_reported_never_refreshed(tmp_path, updated, reason):
    lanes = {"fable-5": _write(tmp_path / "lanes/fable-5.json", _checkpoint("fable-5", updated))}
    lane = _lane(wr.read_routing_inputs(_paths(tmp_path, lanes), NOW), "fable-5")
    assert lane["checkpoint_fresh"] is False and reason in lane["reasons"]


def test_a_checkpoint_900s_old_is_fresh_but_still_yields_no_worker(tmp_path):
    lanes = {"fable-5": _write(tmp_path / "lanes/fable-5.json", _checkpoint("fable-5", NOW - timedelta(seconds=900)))}
    data = Path(lanes["fable-5"]).read_bytes()
    result = wr.read_routing_inputs(_paths(tmp_path, lanes), NOW)
    assert _lane(result, "fable-5") == {"worker": "fable-5", "checkpoint_fresh": True,
                                        "checkpoint_byte_sha256": hashlib.sha256(data).hexdigest(),
                                        "checkpoint_size": len(data),
                                        "reasons": ["profile_unproven", "subject_unbound"]}
    assert result["assembled"]["inputs"]["workers"] == []


def test_each_lane_report_is_bound_to_the_bytes_it_read_and_only_those(tmp_path):
    # RCO2 N1 (20:30:13Z): checkpoint_fresh must be bound to the exact checkpoint bytes it was judged on.
    lanes = {"fable-5": _write(tmp_path / "lanes/f.json", _checkpoint("fable-5")),
             "codex-lead-1": _write(tmp_path / "lanes/c.json", _checkpoint("FABLE-5")),
             "codex-tools-1": str(tmp_path / "lanes/absent.json"),
             "claude-rco-1": _write(tmp_path / "lanes/r.json", raw=b'{"schema": "wd.lane-current.v1",'),
             "FABLE-5": _write(tmp_path / "lanes/x.json", _checkpoint("FABLE-5"))}
    result = wr.read_routing_inputs(_paths(tmp_path, lanes), NOW)
    for worker in ("fable-5", "codex-lead-1"):
        data = Path(lanes[worker]).read_bytes()
        assert _lane(result, worker)["checkpoint_byte_sha256"] == hashlib.sha256(data).hexdigest()
        assert _lane(result, worker)["checkpoint_size"] == len(data)
    assert _lane(result, "fable-5")["checkpoint_fresh"] is True
    assert _lane(result, "codex-lead-1")["checkpoint_fresh"] is False
    for worker in ("codex-tools-1", "claude-rco-1", "FABLE-5"):        # unread, refused or never opened
        report = _lane(result, worker)
        assert report["checkpoint_fresh"] is False
        assert report["checkpoint_byte_sha256"] is None and report["checkpoint_size"] is None
    assert str(tmp_path) not in json.dumps(result["lanes"])


def test_profile_stays_unproven_despite_a_catalog_default_and_a_claimed_profile(tmp_path, monkeypatch):
    # A catalog default and a desired-profile record sit next to the checkpoint; the checkpoint itself even
    # carries profile fields. None of them is the running profile, and nothing un-listed is opened.
    _write(tmp_path / "configs/lane_profile_catalog.json",
           {"lanes": {"fable-5": {"default": "opus-high", "allowed_profiles": ["opus-high"]}}})
    _write(tmp_path / "lane_profiles/fable-5.json", {"desired": {"profile_id": "opus-high"}, "launched": None})
    lanes = {"fable-5": _write(tmp_path / "lanes/fable-5.json",
                               _checkpoint("fable-5", profile_id="opus-high", model="claude-opus"))}
    paths = _paths(tmp_path, lanes)
    opened = _spy_opens(monkeypatch)
    result = wr.read_routing_inputs(paths, NOW)
    assert "profile_unproven" in _lane(result, "fable-5")["reasons"]
    assert result["assembled"]["inputs"]["workers"] == []
    listed = sorted([paths[name] for name in wr.DOCUMENT_PATHS if name in paths] + list(lanes.values()))
    assert sorted(opened) == listed


def test_an_unverified_pool_decision_with_a_subject_is_never_read_or_bound(tmp_path, monkeypatch):
    decision = _write(tmp_path / "decision.json", {"schema": "wd.pool-binding-decision.v1", "provider": "codex",
                                                    "subject_kind": "auth_context", "subject_id": CODEX_SUBJECT,
                                                    "pool_identity_state": "unverified"})
    paths = _paths(tmp_path)
    paths["pool_decision"] = decision
    opened = _spy_opens(monkeypatch)
    result = wr.read_routing_inputs(paths, NOW)
    assert "path_key_unsupported" in result["reasons"]
    assert decision not in opened
    assert result["assembled"]["inputs"]["subjects"] == {}
    assert "subject_unbound" in _lane(result, "codex-lead-1")["reasons"]


@pytest.mark.parametrize("status", [
    {"schema": "wd.capacity-status.v2", "execution_allowed": False, "observations": []},
    {"schema": "wd.capacity-status.v1", "execution_allowed": True, "observations": []},
    {"schema": "wd.capacity-status.v1", "observations": []},
    {"schema": "wd.capacity-status.v1", "execution_allowed": False, "observations": {"codex": []}},
    [{"schema": "wd.capacity-observation.v1"}],
])
def test_a_capacity_status_of_another_shape_gives_no_rows(tmp_path, status):
    result = wr.read_routing_inputs(_paths(tmp_path, capacity_status=status), NOW)
    assert "capacity_status_malformed" in result["reasons"]
    assert result["assembled"]["inputs"]["rows"] is None
    assert result["assembled"]["provenance"]["rows"]["caller_path"] is None


def test_the_rows_hash_is_labelled_as_the_container_file_hash(tmp_path):
    # RCO2 N2 (20:30:13Z): rows come from inside the capacity-status file, so their byte hash is that file's.
    status = {"schema": "wd.capacity-status.v1", "observed_at": NOW.isoformat(), "execution_allowed": False,
              "observations": [_codex_row()]}
    result = wr.read_routing_inputs(_paths(tmp_path, capacity_status=status), NOW)
    assert result["rows_basis"] == wr.ROWS_BASIS
    rows = result["assembled"]["provenance"]["rows"]
    assert rows["caller_byte_sha256"] == result["reads"]["capacity_status"]["byte_sha256"]
    assert rows["document_digest"] == digest([_codex_row()])
    assert result["reads"]["capacity_status"]["document_digest"] == digest(status) != rows["document_digest"]
    malformed = wr.read_routing_inputs(_paths(tmp_path / "m", capacity_status={"schema": "other"}), NOW)
    assert malformed["rows_basis"] is None


def test_a_codex_row_without_a_lane_mapping_binds_no_subject(tmp_path):
    result = wr.read_routing_inputs(_paths(tmp_path), NOW)
    assert result["assembled"]["inputs"]["rows"] == [_codex_row()]      # passed through unchanged
    assert result["assembled"]["inputs"]["subjects"] == {}
    assert CODEX_SUBJECT not in json.dumps(result["lanes"])
    assert "subject_unbound" in _lane(result, "codex-lead-1")["reasons"]


def test_no_row_of_either_provider_becomes_a_subject_for_any_lane(tmp_path):
    # RCO2 V3: compose matches a subject against rows of ANY provider, so the reader must bind none at all.
    claude_row = {"schema": "wd.capacity-observation.v1", "provider": "claude", "native_thread_id": "session-1",
                  "freshness": "fresh", "reason": None}
    status = {"schema": "wd.capacity-status.v1", "observed_at": NOW.isoformat(), "execution_allowed": False,
              "observations": [_codex_row(), claude_row]}
    lanes = {name: _write(tmp_path / f"lanes/{name}.json", _checkpoint(name))
             for name in ("fable-5", "codex-lead-1", "codex-tools-1")}
    result = wr.read_routing_inputs(_paths(tmp_path, lanes, capacity_status=status), NOW)
    assert result["assembled"]["inputs"]["subjects"] == {} and result["assembled"]["inputs"]["workers"] == []
    assert all(entry["reasons"][-2:] == ["profile_unproven", "subject_unbound"] for entry in result["lanes"])
    assert any("RCO2 V3" in item for item in result["prerequisites"])


@pytest.mark.parametrize("raw", [
    b'{"policy": 1, "policy": 1}',
    b'{"outer": {"a": 1, "a": 2}}',
    b'{"policy": NaN}',
    b'{"policy": Infinity}',
    b'{"policy": -Infinity}',
    b'{"policy": 1e400}',
    b'{"policy": [-1e400]}',
])
def test_duplicate_keys_and_non_finite_numbers_are_refused(tmp_path, raw):
    paths = _paths(tmp_path)
    paths["routing_policy"] = _write(tmp_path / "bad-policy.json", raw=raw)
    result = wr.read_routing_inputs(paths, NOW)
    assert result["assembled"]["inputs"]["routing_policy"] is None
    assert "routing_policy_unreadable:not_strict_json" in result["reasons"]
    assert result["reads"]["routing_policy"]["byte_sha256"] is None


def test_byte_sha256_and_document_digest_are_distinct_and_from_the_same_single_read(tmp_path, monkeypatch):
    policy = {"policy": "example", "weights": [1, 2]}
    compact = json.dumps(policy, separators=(",", ":")).encode("utf-8")
    spaced = json.dumps(policy, indent=4).encode("utf-8") + b"\n\n"
    first, second = _paths(tmp_path / "one"), _paths(tmp_path / "two")
    first["routing_policy"] = _write(tmp_path / "compact.json", raw=compact)
    second["routing_policy"] = _write(tmp_path / "spaced.json", raw=spaced)
    opened = _spy_opens(monkeypatch)
    one, two = wr.read_routing_inputs(first, NOW), wr.read_routing_inputs(second, NOW)
    assert opened.count(first["routing_policy"]) == 1 and opened.count(second["routing_policy"]) == 1
    read_one, read_two = one["reads"]["routing_policy"], two["reads"]["routing_policy"]
    assert read_one["byte_sha256"] == hashlib.sha256(compact).hexdigest()
    assert read_two["byte_sha256"] == hashlib.sha256(spaced).hexdigest()
    assert read_one["byte_sha256"] != read_two["byte_sha256"]
    assert read_one["document_digest"] == read_two["document_digest"] == digest(policy)
    assert read_one["size"] == len(compact)
    provenance = one["assembled"]["provenance"]["routing_policy"]
    assert provenance["caller_byte_sha256"] == read_one["byte_sha256"]
    assert provenance["document_digest"] == read_one["document_digest"]
    assert provenance["caller_path"] == first["routing_policy"]


def test_truncated_non_utf8_and_bom_documents_are_refused(tmp_path):
    for name, raw, kind in (("task", b'{"task_id": "t-1", "revision"', "not_strict_json"),
                            ("paced", b'{"windows": "\xff"}', "not_utf8"),
                            ("prepared_artifacts", b"\xef\xbb\xbf[]", "not_strict_json")):
        paths = _paths(tmp_path / name)
        paths[name] = _write(tmp_path / name / "bad.json", raw=raw)
        result = wr.read_routing_inputs(paths, NOW)
        assert f"{name}_unreadable:{kind}" in result["reasons"]
        assert result["reads"][name]["document_digest"] is None


def test_oversized_files_are_refused_before_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "MAX_BYTES", 64)
    paths = _paths(tmp_path)
    paths["routing_policy"] = _write(tmp_path / "exact.json", raw=b'{"p": "' + b"x" * 55 + b'"}')
    assert len(Path(paths["routing_policy"]).read_bytes()) == 64
    paths["paced"] = _write(tmp_path / "over.json", raw=b'{"p": "' + b"x" * 56 + b'"}')
    result = wr.read_routing_inputs(paths, NOW)
    assert result["assembled"]["inputs"]["routing_policy"] == {"p": "x" * 55}
    assert "paced_unreadable:oversized" in result["reasons"]
    assert result["assembled"]["inputs"]["paced"] is None


@pytest.mark.parametrize(("value", "kind"), [
    (None, "path_invalid"), (7, "path_invalid"), ("", "path_invalid"), ("a\x00b", "path_invalid"),
    ("x" * 1025, "path_invalid"),
])
def test_path_values_that_are_not_bounded_text_are_refused(tmp_path, value, kind):
    paths = _paths(tmp_path)
    paths["task"] = value
    result = wr.read_routing_inputs(paths, NOW)
    assert f"task_unreadable:{kind}" in result["reasons"]
    assert result["assembled"]["inputs"]["task"] is None


def test_missing_files_directories_and_links_are_refused(tmp_path):
    paths = _paths(tmp_path)
    paths["task"] = str(tmp_path / "absent.json")
    paths["paced"] = str(tmp_path)
    target = Path(paths["routing_policy"])
    link = tmp_path / "link.json"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        link = None
    if link is not None:
        paths["routing_policy"] = str(link)
    result = wr.read_routing_inputs(paths, NOW)
    assert "task_unreadable:missing_or_unreadable" in result["reasons"]
    assert "paced_unreadable:not_a_regular_file" in result["reasons"]
    if link is not None:
        assert "routing_policy_unreadable:not_a_regular_file" in result["reasons"]


def test_diagnostics_never_carry_path_text_or_file_content(tmp_path):
    paths = _paths(tmp_path)
    paths["routing_policy"] = _write(tmp_path / "SECRET-PATH-TOKEN.json", raw=b'{"k": "SECRET-CONTENT-TOKEN", ')
    paths["lanes"] = {"fable-5": _write(tmp_path / "lanes/SECRET-LANE-PATH.json", raw=b'{"agent": "SECRET-LANE"')}
    text = json.dumps(wr.read_routing_inputs(paths, NOW))
    for token in ("SECRET-PATH-TOKEN", "SECRET-CONTENT-TOKEN", "SECRET-LANE-PATH", "SECRET-LANE"):
        assert token not in text


def test_malformed_mappings_and_now_give_a_refused_advisory_result(tmp_path):
    result = wr.read_routing_inputs(["not", "a", "mapping"], datetime(2026, 9, 30, 20, 0, 0))
    _advisory(result)
    assert "paths_malformed" in result["reasons"] and "now_invalid" in result["reasons"]
    assert result["now_utc"] is None
    paths = _paths(tmp_path)
    paths["lanes"] = ["fable-5"]
    assert "lanes_malformed" in wr.read_routing_inputs(paths, NOW)["reasons"]


# --- success fixtures ----------------------------------------------------------------------------

def test_all_unknown_is_still_an_advisory_success(tmp_path):
    paths = {name: str(tmp_path / f"absent-{name}.json") for name in wr.DOCUMENT_PATHS}
    paths["lanes"] = {"fable-5": str(tmp_path / "absent-lane.json")}
    result = wr.read_routing_inputs(paths, NOW)
    _advisory(result)
    for name in ("task", "capacity_status", "paced", "prepared_artifacts", "routing_policy", "signed_policy"):
        assert f"{name}_unreadable:missing_or_unreadable" in result["reasons"]
    assert _lane(result, "fable-5")["reasons"] == ["lane_unreadable:missing_or_unreadable",
                                                   "profile_unproven", "subject_unbound"]
    assert result["assembled"]["inputs"]["rows"] is None
    assert list(result["prerequisites"]) == list(wr.PREREQUISITES)


def test_grok_is_listed_only_by_the_caller_unranked_with_no_subject(tmp_path):
    without = wr.read_routing_inputs(_paths(tmp_path), NOW)
    assert without["grok"] == {"listed": False}
    assert all(worker["worker"] != "grok" for worker in without["assembled"]["inputs"]["workers"])
    listed = wr.read_routing_inputs(_paths(tmp_path), NOW, grok_profile_id="grok-4.7")
    assert listed["grok"] == {"listed": True, "ranked": False, "subject": None, "basis": wr.GROK_BASIS}
    workers = listed["assembled"]["inputs"]["workers"]
    assert [worker["worker"] for worker in workers] == ["grok"] and "grok" not in listed["assembled"]["inputs"]["subjects"]
    capacity = {entry["worker"]: entry for entry in listed["composed"]["capacity"]}
    assert "no_measured_grok_capacity" in capacity["grok"]["reasons"]
    refused = wr.read_routing_inputs(_paths(tmp_path), NOW, grok_profile_id=" grok ")
    assert "grok_profile_malformed" in refused["reasons"] and refused["grok"] == {"listed": False}
    naive = wr.read_routing_inputs(_paths(tmp_path), datetime(2026, 9, 30, 20, 0, 0), grok_profile_id="grok-4.7")
    assert naive["grok"] == {"listed": False} and "grok_profile_malformed" not in naive["reasons"]
    assert "now_invalid" in naive["reasons"] and naive["assembled"]["inputs"]["workers"] == []


def test_the_task_reaches_compose_unchanged(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    loaded = json.loads(Path(paths["task"]).read_bytes())
    seen = {}
    real = wr.compose

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(wr, "compose", spy)
    result = wr.read_routing_inputs(paths, NOW)
    assert seen["task"] == loaded and seen["now"] is NOW and seen["shadow_weights"] is None
    assert result["reads"]["task"]["document_digest"] == digest(loaded)


def test_signed_policy_is_optional_and_never_verified(tmp_path):
    paths = _paths(tmp_path)
    absent = wr.read_routing_inputs(paths, NOW)
    assert absent["assembled"]["inputs"]["signed_policy"] is None and absent["reads"]["signed_policy"] is None
    paths["signed_policy"] = _write(tmp_path / "signed.json", {"policy": "p", "signature": "zz"})
    present = wr.read_routing_inputs(paths, NOW)
    _advisory(present)
    assert present["assembled"]["inputs"]["signed_policy"] == {"policy": "p", "signature": "zz"}


def test_the_same_files_give_the_same_result_and_the_environment_is_ignored(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    first = wr.read_routing_inputs(paths, NOW)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("WD_CAPACITY_STATUS", str(tmp_path / "elsewhere.json"))
    assert wr.read_routing_inputs(paths, NOW) == first
