"""Only fake rows: preserve event identity or drop the complete row."""
import hashlib
import hmac
import json

import pytest

from tools import wd_mind_source_snapshot as subject


def raw(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


ROSTER = {
    "codex-lead-1": "00000000-0000-4000-8000-000000000001",
    "codex-tools-1": "00000000-0000-4000-8000-000000000002",
    "fable-5": "00000000-0000-4000-8000-000000000003",
    "claude-rco-1": "00000000-0000-4000-8000-000000000004",
    "claude-rco-2": "00000000-0000-4000-8000-000000000005",
}


@pytest.fixture
def config():
    roster = raw({"schema": "wd.q4-roster-snapshot.v1", "lanes": [
        {"agent": name, "agent_uuid": ROSTER[name]}
        for name in sorted(set(ROSTER) - {"codex-tools-1"})],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    policy = raw({"schema": "wd.mind-content-policy.v3", "lanes": ["codex-lead-1", "codex-tools-1", "fable-5"],
                  "types": ["message"], "task_scope": "all_tasks", "removed_source_refs": [],
                  "max_message_chars": 4096, "max_chunk_chars": 300})
    identifiers = raw({"schema": "wd.mind-identifier-policy.v1", "identifiers": ["Synthetic Private Person"]})
    key = b"synthetic-test-key-00000000000000000"
    return dict(enabled=True, roster_bytes=roster, roster_sha256=digest(roster),
                policy_bytes=policy, policy_sha256=digest(policy),
                identifier_policy_binding=(identifiers, key, hmac.new(key, identifiers, hashlib.sha256).hexdigest()),
                excluded_tasks=())


@pytest.fixture
def event():
    return {"ts_utc": "2026-01-01T00:00:00Z", "agent": "codex-lead-1", "agent_uuid": ROSTER["codex-lead-1"],
            "type": "message", "task_id": "task7", "message": "Review found a fix", "to": "codex-tools-1",
            "payload": {"summary": "Parser fixed", "items": [{"detail": "Ready for review"}]}}


def line(event):
    return raw(event) + b"\n"


def test_clean_whole_event_keeps_original_line_and_canonical_ref(config, event):
    # Noncanonical whitespace establishes that identity is the canonical event,
    # while the complete original line is not rewritten.
    source = json.dumps(event, ensure_ascii=False, indent=None).encode() + b"\n"
    result = subject.screen_line(source, **config)
    assert result["status"] == "accepted"
    assert result["line"] == source
    assert result["source_event_ref"] == digest(raw(event))
    assert result["authority"] == "none"
    assert result["source_membership"] == result["privacy_coverage"] == "UNKNOWN"


@pytest.mark.parametrize("field,value", [
    ("message", "Look at https://example.invalid/info"),
    ("message", "Read C:\\Users\\Synthetic\\notes.txt"),
    ("message", "Value 0123456789abcdef0123456789abcdef"),
    ("message", "Contact Synthetic Private Person"),
    ("message", "sealed item"),
    ("message", "heldout item"),
    ("to", "claude-rco-1"),
    ("agent", "claude-rco-1"),
    ("unexpected", "anything"),
])
def test_refuses_whole_row_and_never_returns_content(config, event, field, value):
    event[field] = value
    result = subject.screen_line(line(event), **config)
    assert result["status"] == "excluded"
    assert "line" not in result and "source_event_ref" not in result
    assert value not in repr(result)


@pytest.mark.parametrize("payload", [
    {"summary": "Parser fixed", "items": [{"detail": "password: synthetic-secret"}]},
    {"summary": "Parser fixed", "items": [{"detail": "gold material"}]},
    {"summary": "Parser fixed", "items": [{"detail": "judge material"}]},
    {"summary": "Parser fixed", "items": [{"detail": "Synthetic Private Person"}]},
    {"summary": "Parser fixed", "items": [{"token": "abc"}]},
    {"summary": "Parser fixed", "items": [{"detail": "https://example.invalid/path"}]},
    {"summary": "Parser fixed", "items": [{"detail": "C:\\Users\\Fake\\file.txt"}]},
    {"summary": "Parser fixed", "items": [{"detail": "Review done", "to": "claude-rco-1"}]},
    {"summary": "Parser fixed", "items": [{"detail": 1234}]},
])
def test_nested_payload_is_screened_not_only_message(config, event, payload):
    event["payload"] = payload
    result = subject.screen_line(line(event), **config)
    assert result["status"] == "excluded"
    assert "line" not in result


def test_unknown_private_names_are_not_claimed_as_detected(config, event):
    event["message"] = "Unlisted Example Person noted a parser issue"
    result = subject.screen_line(line(event), **config)
    assert result["status"] == "accepted"
    assert result["privacy_coverage"] == "UNKNOWN"


def test_task_and_source_ref_exclusions(config, event):
    assert subject.screen_line(line(event), **dict(config, excluded_tasks=("task7",)))["status"] == "excluded"
    policy = json.loads(config["policy_bytes"])
    policy["removed_source_refs"] = [digest(raw(event))]
    policy_bytes = raw(policy)
    assert subject.screen_line(line(event), **dict(config, policy_bytes=policy_bytes,
                               policy_sha256=digest(policy_bytes)))["status"] == "excluded"


@pytest.mark.parametrize("source", [b"{}", b'{}\n{}\n', b'{"x": 1, "x": 2}\n', b'\xff\n', b'[]\n', b'\n'])
def test_incomplete_duplicate_and_nonobject_rows_fail_without_echo(config, source):
    result = subject.screen_line(source, **config)
    assert result["status"] == "excluded"
    assert "line" not in result


def test_disabled_and_invalid_pins_refuse(config, event):
    for change in ({"enabled": False}, {"roster_sha256": "0" * 64}, {"policy_sha256": "0" * 64},
                   {"identifier_policy_binding": (b"{}", b"x" * 32, "0" * 64)}):
        with pytest.raises(subject.SourceScreenRefused, match="^source_screen_refused$"):
            subject.screen_line(line(event), **dict(config, **change))


def test_screen_is_in_memory_only(config, event, monkeypatch):
    import builtins
    import pathlib
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("I/O attempted")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(pathlib.Path, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert subject.screen_line(line(event), **config)["status"] == "accepted"


def test_exact_message_policy_limit_applies_to_message_only(config, event):
    policy = json.loads(config["policy_bytes"])
    policy["max_message_chars"] = 8
    policy_bytes = raw(policy)
    bound = dict(config, policy_bytes=policy_bytes, policy_sha256=digest(policy_bytes))
    event["message"] = "Fix pass"  # exactly eight, payload text remains independently screened
    assert subject.screen_line(line(event), **bound)["status"] == "accepted"
    event["message"] = "Fixed bug"  # nine characters
    result = subject.screen_line(line(event), **bound)
    assert result["status"] == "excluded"
    assert "line" not in result


@pytest.mark.parametrize("timestamp", [
    "2026-01-01T00:00:00.12345678Z",
    "2026-01-01T00:00:00,123Z",
    "2026-01-01 00:00:00Z",
    "2026-01-01T00:00:00+01:00",
])
def test_timestamp_refuses_non_bridge_utc_or_unbounded_fraction(config, event, timestamp):
    event["ts_utc"] = timestamp
    assert subject.screen_line(line(event), **config)["status"] == "excluded"


@pytest.mark.parametrize("timestamp", [
    "2026-01-01T00:00:00Z",
    "2026-01-01T00:00:00.1Z",
    "2026-01-01T00:00:00.1234567Z",
    "2026-01-01T00:00:00+00:00",
])
def test_timestamp_accepts_bridge_utc_with_bounded_fraction(config, event, timestamp):
    event["ts_utc"] = timestamp
    assert subject.screen_line(line(event), **config)["status"] == "accepted"


@pytest.fixture
def fleet_config(config):
    fleet = {"schema_version": 2, "lanes": [
        {"agent": name, "agent_uuid": ROSTER[name], "worktree": "C:\\Work\\" + name}
        for name in sorted(set(ROSTER) - {"codex-tools-1"})],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"],
                             "worktree": "C:\\Work\\codex-tools-1"}}
    fleet_bytes = raw(fleet)
    return dict(config, fleet_bytes=fleet_bytes, fleet_sha256=digest(fleet_bytes))


@pytest.mark.parametrize("agent", ["codex-lead-1", "codex-tools-1", "fable-5"])
def test_pinned_exact_build_lane_worktree_preserves_line_and_reference(fleet_config, event, agent):
    event.update(agent=agent, agent_uuid=ROSTER[agent], cwd="C:\\Work\\" + agent)
    source = line(event)
    result = subject.screen_line(source, **fleet_config)
    assert result["status"] == "accepted"
    assert result["line"] == source and result["source_event_ref"] == digest(raw(event))
    assert result["authority"] == "none" and result["source_membership"] == "UNKNOWN"


@pytest.mark.parametrize("cwd", ["C:\\Work\\fable-5", "C:\\Work\\codex-lead-1\\child",
                                   "c:\\Work\\codex-lead-1", "C:/Work/codex-lead-1",
                                   "C:\\Work\\.\\codex-lead-1", "https://example.invalid/cwd",
                                   "", "cwd", "C:\\Work\\..\\Work\\codex-lead-1"])
def test_unmatched_cwd_never_uses_fleet_exception(fleet_config, event, cwd):
    event["cwd"] = cwd
    result = subject.screen_line(line(event), **fleet_config)
    assert result["status"] == "excluded" and "line" not in result


def test_cwd_exception_requires_fleet_and_independent_digest(config, fleet_config, event):
    event["cwd"] = "C:\\Work\\codex-lead-1"
    assert subject.screen_line(line(event), **config)["status"] == "excluded"
    with pytest.raises(subject.SourceScreenRefused):
        subject.screen_line(line(event), **dict(fleet_config, fleet_sha256="0" * 64))
    with pytest.raises(subject.SourceScreenRefused):
        subject.screen_line(line(event), **dict(fleet_config, fleet_sha256=None))
    event["cwd"] = "cwd"
    assert subject.screen_line(line(event), **config)["status"] == "excluded"


@pytest.mark.parametrize("mutation", ["duplicate", "uuid", "unknown_lane", "invalid_schema", "no_worktree"])
def test_fleet_must_match_independent_roster_and_schema(fleet_config, event, mutation):
    event["cwd"] = "C:\\Work\\codex-lead-1"
    fleet = json.loads(fleet_config["fleet_bytes"])
    if mutation == "duplicate": fleet["lanes"].append(dict(fleet["lanes"][0]))
    elif mutation == "uuid": fleet["lanes"][0]["agent_uuid"] = "00000000-0000-4000-8000-000000000099"
    elif mutation == "unknown_lane": fleet["lanes"][0]["agent"] = "untrusted"
    elif mutation == "invalid_schema": fleet["schema_version"] = 1
    elif mutation == "no_worktree": del fleet["lanes"][0]["worktree"]
    bad = raw(fleet)
    with pytest.raises(subject.SourceScreenRefused):
        subject.screen_line(line(event), **dict(fleet_config, fleet_bytes=bad, fleet_sha256=digest(bad)))


@pytest.mark.parametrize("path", ["C:\\Work\\Synthetic Private Person", "C:\\Work\\sealed",
                                    "C:\\Work\\sk-123456789abcdef", "https://example.invalid/cwd",
                                    "C:\\Work\\abcdefghijklmnop", "C:\\Work\\0123456789abcdef0123456789abcdef"])
def test_content_unsafe_or_url_fleet_worktree_never_accepted(fleet_config, event, path):
    fleet = json.loads(fleet_config["fleet_bytes"])
    entry = next(item for item in fleet["lanes"] if item["agent"] == event["agent"])
    entry["worktree"] = path
    event["cwd"] = path
    bad = raw(fleet)
    try:
        result = subject.screen_line(line(event), **dict(fleet_config, fleet_bytes=bad, fleet_sha256=digest(bad)))
        assert result["status"] == "excluded" and "line" not in result
    except subject.SourceScreenRefused:
        pass


@pytest.mark.parametrize("field", ["message", "task_id", "run_id", "role", "session_id", "paths", "write_scope"])
def test_fleet_exception_never_covers_other_fields(fleet_config, event, field):
    value = "C:\\Work\\codex-lead-1"
    event["cwd"] = value
    event[field] = [value] if field in ("paths", "write_scope") else value
    result = subject.screen_line(line(event), **fleet_config)
    assert result["status"] == "excluded" and "line" not in result


def test_fleet_exception_never_covers_nested_payload(fleet_config, event):
    event["cwd"] = "C:\\Work\\codex-lead-1"
    event["payload"] = {"summary": "C:\\Work\\codex-lead-1"}
    assert subject.screen_line(line(event), **fleet_config)["status"] == "excluded"


def test_rco_authored_or_addressed_rows_remain_excluded(fleet_config, event):
    event.update(agent="claude-rco-1", agent_uuid=ROSTER["claude-rco-1"], cwd="C:\\Work\\claude-rco-1")
    assert subject.screen_line(line(event), **fleet_config)["status"] == "excluded"
    event.update(agent="codex-lead-1", agent_uuid=ROSTER["codex-lead-1"], cwd="C:\\Work\\codex-lead-1",
                 to="claude-rco-1")
    assert subject.screen_line(line(event), **fleet_config)["status"] == "excluded"


def test_fleet_bound_screen_remains_in_memory(fleet_config, event, monkeypatch):
    import builtins
    import pathlib
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        raise AssertionError("I/O attempted")

    event["cwd"] = "C:\\Work\\codex-lead-1"
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(pathlib.Path, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert subject.screen_line(line(event), **fleet_config)["status"] == "accepted"


@pytest.fixture
def reviewed_fleet(config, monkeypatch):
    from pathlib import Path
    common = "waggledance-agent-worktrees"
    tools_name = "codex-tools-1-post-reboot-tools-sandbox-followup-20260731"
    fable_name = "fable-5-hex-capability-probe-v1-20260730"
    paths = {"codex-lead-1": "C:\\Public\\lead", "codex-tools-1": "C:\\Public\\" + common + "\\" + tools_name,
             "fable-5": "C:\\Public\\" + common + "\\" + fable_name,
             "claude-rco-1": "C:\\Public\\rco1", "claude-rco-2": "C:\\Public\\rco2"}
    fleet = {"schema_version": 2, "lanes": [
        {"agent": name, "agent_uuid": ROSTER[name], "worktree": paths[name]}
        for name in sorted(set(ROSTER) - {"codex-tools-1"})],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"],
                             "worktree": paths["codex-tools-1"]}}
    fleet_bytes = raw(fleet)
    # Test replaces the trusted source constant, never an API caller's claimed hash.
    monkeypatch.setattr(subject, "_REVIEWED_FLEET_SHA256", digest(fleet_bytes), raising=False)
    monkeypatch.setattr(subject, "_REVIEWED_WORKTREES", {name: paths[name] for name in ("codex-tools-1", "fable-5")}, raising=False)
    source = Path(subject.content.__file__).read_bytes()
    assert digest(source) == subject._REVIEWED_CONTENT_SOURCE_SHA256
    return dict(config, fleet_bytes=fleet_bytes, fleet_sha256=digest(fleet_bytes),
                reviewed_policy="wd.mind-reviewed-fleet-cwd.v1", reviewed_detector_source=source), paths


@pytest.mark.parametrize("agent", ["codex-tools-1", "fable-5"])
def test_reviewed_fleet_literal_preserves_original_event(reviewed_fleet, event, agent):
    cfg, paths = reviewed_fleet
    event.update(agent=agent, agent_uuid=ROSTER[agent], cwd=paths[agent])
    original = line(event)
    result = subject.screen_line(original, **cfg)
    assert result["status"] == "accepted"
    assert result["line"] == original and result["source_event_ref"] == digest(raw(event))
    assert result["authority"] == "none" and result["source_membership"] == "UNKNOWN"


@pytest.mark.parametrize("mutant", ["different_lane", "prefix", "suffix", "case", "separator", "component_offset"])
def test_reviewed_literals_not_a_general_path_or_length_exception(reviewed_fleet, event, mutant):
    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"])
    if mutant == "different_lane": event.update(agent="codex-tools-1", agent_uuid=ROSTER["codex-tools-1"])
    elif mutant == "prefix": event["cwd"] = event["cwd"].replace("fable-5-hex", "xfable-5-hex")
    elif mutant == "suffix": event["cwd"] += "x"
    elif mutant == "case": event["cwd"] = event["cwd"].replace("fable-5-hex", "Fable-5-hex")
    elif mutant == "separator": event["cwd"] = event["cwd"].replace("\\", "/")
    elif mutant == "component_offset": event["cwd"] = event["cwd"].replace("C:\\Public\\", "C:\\")
    assert subject.screen_line(line(event), **cfg)["status"] == "excluded"


def test_caller_repinned_changed_fleet_does_not_grant_exception(reviewed_fleet, event):
    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"])
    fleet = json.loads(cfg["fleet_bytes"])
    fleet["extra"] = "unapproved new fleet"
    changed = raw(fleet)
    result = subject.screen_line(line(event), **dict(cfg, fleet_bytes=changed, fleet_sha256=digest(changed)))
    assert result["status"] == "excluded" and "line" not in result


@pytest.mark.parametrize("change", [
    {"reviewed_policy": None}, {"reviewed_policy": "wd.mind-reviewed-fleet-cwd.v2"},
    {"reviewed_detector_source": None}, {"reviewed_detector_source": b"wrong source"},
    {"reviewed_detector_source": ["not bytes"]}, {"reviewed_detector_source": b"x" * (2 * 1024 * 1024 + 1)},
])
def test_reviewed_exception_requires_exact_policy_and_detector_source(reviewed_fleet, event, change):
    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"])
    result = subject.screen_line(line(event), **dict(cfg, **change))
    assert result["status"] == "excluded" and "line" not in result


def test_reviewed_exception_rejects_changed_loaded_screen_code(reviewed_fleet, event, monkeypatch):
    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"])
    monkeypatch.setattr(subject.content, "_screen", lambda *args, **kwargs: None)
    result = subject.screen_line(line(event), **cfg)
    assert result["status"] == "excluded" and "line" not in result


def test_reviewed_exception_rejects_same_code_with_foreign_globals(reviewed_fleet, event, monkeypatch):
    import types
    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"])
    original = subject.content._screen
    forged = types.FunctionType(original.__code__, dict(original.__globals__), original.__name__,
                                original.__defaults__, original.__closure__)
    forged.__kwdefaults__ = original.__kwdefaults__
    monkeypatch.setattr(subject.content, "_screen", forged)
    result = subject.screen_line(line(event), **cfg)
    assert result["status"] == "excluded" and "line" not in result


@pytest.mark.parametrize("detector", ["credential", "personal", "identifier", "sealed", "script", "other_phone"])
def test_reviewed_fable_never_skips_other_raw_content_detectors(reviewed_fleet, event, monkeypatch, detector):
    import re
    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"])
    c = subject.content
    if detector == "credential": monkeypatch.setattr(c, "_CREDENTIAL", c._CREDENTIAL + (re.compile("fable-5-hex"),))
    elif detector == "personal": monkeypatch.setattr(c, "_PERSONAL", c._PERSONAL + (re.compile("fable-5-hex"),))
    elif detector == "identifier":
        identifiers = raw({"schema": "wd.mind-identifier-policy.v1", "identifiers": ["fable-5-hex"]})
        key = b"synthetic-test-key-00000000000000000"
        cfg = dict(cfg, identifier_policy_binding=(identifiers, key, hmac.new(key, identifiers, hashlib.sha256).hexdigest()))
    elif detector == "sealed": monkeypatch.setattr(c, "_has_marker", lambda value: "fable-5-hex" in value)
    elif detector == "script":
        original = c._script
        def check(value):
            if "fable-5-hex" in value: raise c._Drop("mixed_script")
            return original(value)
        monkeypatch.setattr(c, "_script", check)
    elif detector == "other_phone": monkeypatch.setattr(c, "_PHONE_RE", re.compile(r"1-20260730|20260730"))
    result = subject.screen_line(line(event), **cfg)
    assert result["status"] == "excluded" and "line" not in result


def test_reviewed_literal_does_not_exempt_other_fields_or_rco(reviewed_fleet, event):
    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"], message=paths["fable-5"])
    assert subject.screen_line(line(event), **cfg)["status"] == "excluded"
    event.update(agent="claude-rco-1", agent_uuid=ROSTER["claude-rco-1"], cwd=paths["claude-rco-1"], message="Review")
    assert subject.screen_line(line(event), **cfg)["status"] == "excluded"


@pytest.mark.parametrize("message", ["sk-123456789abcdef", "sealed material", "Synthetic Private Person",
                                      "https://example.invalid/path", "0123456789abcdef0123456789abcdef"])
def test_reviewed_cwd_does_not_exempt_other_field_policy(reviewed_fleet, event, message):
    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"], message=message)
    result = subject.screen_line(line(event), **cfg)
    assert result["status"] == "excluded" and "line" not in result


def test_fable_exception_does_not_modify_shared_phone_detector(reviewed_fleet, event):
    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"])
    phone = subject.content._PHONE_RE
    private = subject.content._load_identifier_policy(*cfg["identifier_policy_binding"])
    with pytest.raises(subject.content._Drop) as before:
        subject.content._screen(event["cwd"], identifier_policy=private)
    assert before.value.token == "personal"
    assert subject.screen_line(line(event), **cfg)["status"] == "accepted"
    assert subject.content._PHONE_RE is phone
    with pytest.raises(subject.content._Drop) as after:
        subject.content._screen(event["cwd"], identifier_policy=private)
    assert after.value.token == "personal"


def test_reviewed_exception_runs_without_file_or_network_io(reviewed_fleet, event, monkeypatch):
    import builtins
    import pathlib
    import socket
    import subprocess

    cfg, paths = reviewed_fleet
    event.update(agent="fable-5", agent_uuid=ROSTER["fable-5"], cwd=paths["fable-5"])
    subject._reviewed_screen_code.cache_clear()
    def forbidden(*args, **kwargs):
        raise AssertionError("I/O attempted")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(pathlib.Path, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert subject.screen_line(line(event), **cfg)["status"] == "accepted"
