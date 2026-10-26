"""Synthetic contract tests; author Tools, execution held by Lead under D15."""
from __future__ import annotations

import ast
import copy
import importlib
import io
import json
from pathlib import Path

import pytest


def utility():
    # A missing production module is the intended first genuine RED.
    return importlib.import_module("tools.bridge_evidence_metadata_readonly")


def event():
    return {
        "ts_utc": "2026-10-26T12:00:01Z", "agent": "codex-tools-1",
        "type": "message", "task_id": "synthetic/task", "status": "answered",
        "severity": "", "to": "codex-lead-1", "message": "synthetic only",
        "paths": [], "write_scope": [], "run_id": "synthetic-run",
        "role": "tools-tests", "agent_uuid": "11111111-1111-1111-1111-111111111111",
        "session_id": "synthetic-session", "capabilities": [], "pid": 1,
        "cwd": "C:\\synthetic", "payload": {},
    }


def bound_pair():
    reply = event()
    request = {
        "ts_utc": "2026-10-26T12:00:00Z", "agent": "codex-lead-1",
        "agent_uuid": "22222222-2222-2222-2222-222222222222",
        "session_id": "synthetic-lead", "run_id": "synthetic-lead-run",
        "task_id": reply["task_id"], "request_id": "synthetic-request",
        "request_digest": "a" * 64, "payload": {"nonce": "synthetic-nonce"},
        "expected_responders": {"codex-tools-1": {
            key: reply[key] for key in ("agent_uuid", "session_id", "run_id")
        }},
    }
    reply["payload"] = {
        "in_reply_to_request_id": request["request_id"],
        "in_reply_to_request_digest": request["request_digest"],
        "in_reply_to_requester": {key: request[key] for key in (
            "agent", "agent_uuid", "session_id", "run_id")},
        "nonce": "synthetic-nonce",
    }
    # The actual schema requires matching top-level copies of these contract fields.
    reply.update({key: copy.deepcopy(reply["payload"][key]) for key in (
        "in_reply_to_request_id", "in_reply_to_request_digest", "in_reply_to_requester",
    )})
    return request, reply


def test_dictionary_and_diff_aliases_are_explicit():
    mod = utility()
    assert "diff_sha256" in mod.FIELD_DICTIONARY
    assert "net_diff_sha256" in mod.FIELD_DICTIONARY
    for key in ("diff_sha256", "net_diff_sha256"):
        source = {key: "a" * 64}
        before = copy.deepcopy(source)
        assert mod.normalize_metadata(source) == {
            "diff_sha256": "a" * 64, "net_diff_sha256": "a" * 64,
        }
        assert source == before
    with pytest.raises(TypeError):
        mod.FIELD_DICTIONARY["invented"] = "mutable"


def test_conflicting_or_invalid_aliases_fail_closed():
    mod = utility()
    for source in (
        {"diff_sha256": "a" * 64, "net_diff_sha256": "b" * 64},
        {"diff_sha256": None}, {"diff_sha256": "A" * 64},
        {"diff_sha256": 123}, {"net_diff_sha256": "short"},
    ):
        with pytest.raises(ValueError):
            mod.normalize_metadata(source)


@pytest.mark.parametrize("key", [
    "creation_time_ticks", "last_write_time_ticks", "last_access_time_ticks",
    "st_mtime_ns", "st_atime_ns", "st_ctime_ns",
])
@pytest.mark.parametrize("value", [0, 638971699234567891, "9223372036854775807"])
def test_tick_and_stat_values_are_exact_decimal_json_strings(key, value):
    mod = utility()
    result = mod.normalize_metadata({"stat": {key: value}})
    assert json.loads(json.dumps(result))["stat"][key] == str(value)
    assert type(result["stat"][key]) is str


@pytest.mark.parametrize("bad", [
    True, False, None, -1, 1.0, 1.25, "01", "+1", "-1", "1.0", "1e3",
    " 1", "1 ", "", "\u0661", 9223372036854775808, "9223372036854775808",
])
def test_malformed_or_ambiguous_ticks_are_refused(bad):
    with pytest.raises(ValueError):
        utility().normalize_metadata({"last_write_time_ticks": bad})


def test_unsafe_outgoing_json_is_not_silently_rounded_or_coerced():
    mod = utility()
    for source in (
        {"unknown_integer": 9007199254740992}, {"bad": float("nan")},
        {"bad": float("inf")}, {1: "not a string key"}, {"bad": object()},
    ):
        with pytest.raises(ValueError):
            mod.normalize_metadata(source)
    assert mod.normalize_metadata({"ordinary": 9007199254740991})["ordinary"] == 9007199254740991


def test_real_schema_format_floor_is_used_not_stubbed(monkeypatch):
    from tools import bridge_v2_event_schema as schema
    original = schema.validate_event_for_write
    seen = []

    def observe(candidate, *, provenance=None):
        seen.append(candidate)
        return original(candidate, provenance=provenance)

    monkeypatch.setattr(schema, "validate_event_for_write", observe)
    result = utility().dry_run(event())
    assert seen == [event()]
    assert result["schema_valid"] is True
    assert result["status"] == "HOLD"
    assert result["merge_authorized"] is False
    assert "BINDING_MISSING" in result["reasons"]
    assert "ENROLLMENT_UNKNOWN" in result["reasons"]


def test_reserved_label_and_headless_approval_are_refused_by_actual_schema():
    mod = utility()
    for change in (
        {"agent": "operator", "role": "operator"},
        {"type": "decision", "status": "build_consensus_pass"},
    ):
        candidate = event()
        candidate.update(change)
        result = mod.dry_run(candidate)
        assert result["status"] == "REFUSED"
        assert result["schema_valid"] is False
        assert "EVENT_SCHEMA" in result["reasons"]
        assert result["merge_authorized"] is False


def test_real_correlation_and_untrusted_enrollment_never_imply_permission():
    request, reply = bound_pair()
    mod = utility()
    before = copy.deepcopy((request, reply))
    for enrollment in (None, {}, {"status": "synthetic"}, {"verified": True}):
        result = mod.dry_run(reply, request=request, target="codex-tools-1", enrollment=enrollment)
        assert result["binding_valid"] is True
        assert result["schema_valid"] is True
        assert result["status"] == "HOLD"
        assert result["merge_authorized"] is False
        assert result["enrollment_status"] in {"UNKNOWN", "UNVERIFIED"}
    assert (request, reply) == before
    reply["payload"]["nonce"] = "wrong"
    result = mod.dry_run(reply, request=request, target="codex-tools-1")
    assert result["status"] == "REFUSED"
    assert "BINDING_MISMATCH" in result["reasons"]


def test_missing_binding_and_injected_fake_provenance_fail_closed():
    mod = utility()
    request, reply = bound_pair()
    result = mod.dry_run(reply, request=request)
    assert result["status"] == "REFUSED"
    assert "TARGET_MISSING" in result["reasons"]
    operator = event()
    operator.update(agent="operator", role="operator")
    result = mod.dry_run(operator, provenance={"agent": "operator", "verified": True})
    assert result["status"] == "REFUSED"
    assert "EVENT_SCHEMA" in result["reasons"]


def test_repeated_cli_calls_use_python_boundary_and_leave_inputs_untouched():
    mod = utility()
    body = json.dumps({"event": event(), "enrollment": {"verified": True}})
    answers = []
    for _ in range(2):
        output = io.StringIO()
        code = mod.main(["--dry-run"], stdin=io.StringIO(body), stdout=output)
        assert code == 3  # HOLD, never a green authorization exit.
        answers.append(json.loads(output.getvalue()))
    assert answers[0] == answers[1]
    assert answers[0]["merge_authorized"] is False
    output = io.StringIO()
    assert mod.main(["--dry-run"], stdin=io.StringIO("{}"), stdout=output) == 2
    assert json.loads(output.getvalue())["status"] == "REFUSED"


def test_static_module_has_no_writer_network_subprocess_or_installed_context():
    path = Path(__file__).resolve().parents[2] / "tools/bridge_evidence_metadata_readonly.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {"subprocess", "socket", "urllib", "http", "requests", "ctypes", "winreg",
                 "bridge_event_writer", "bridge_rco_enrollment", "idle_consensus_auto_merge"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(set(alias.name.split(".")) & forbidden for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not set((node.module or "").split(".")) & forbidden
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            assert name not in {"eval", "exec", "__import__", "write_text", "write_bytes", "open",
                                "load_installed_context", "load_rco_enrollment_context"}

@pytest.mark.parametrize("key", ["last_write_utc_ticks", "creation_utc_ticks"])
def test_actual_snapshot_utc_tick_fields_preserve_exact_names_and_values(key):
    result = utility().normalize_metadata({key: 638907716200000000})
    assert result == {key: "638907716200000000"}
    assert json.loads(json.dumps(result)) == result


def test_actual_snapshot_nested_stat_shape_preserves_original_utc_tick_names():
    stat = {
        "length": 123, "last_write_utc_ticks": 638907716200000000,
        "creation_utc_ticks": 638907716200000000, "attributes": 32,
    }
    source = {"stat_before": copy.deepcopy(stat), "stat_after": copy.deepcopy(stat)}
    original = copy.deepcopy(source)
    result = utility().normalize_metadata(source)
    for name in ("stat_before", "stat_after"):
        assert result[name] == {
            "length": 123, "last_write_utc_ticks": "638907716200000000",
            "creation_utc_ticks": "638907716200000000", "attributes": 32,
        }
    assert source == original
    assert json.loads(json.dumps(result)) == result

@pytest.mark.parametrize("key", [
    "in_reply_to_request_id", "in_reply_to_request_digest", "in_reply_to_requester",
])
def test_former_payload_only_reply_is_still_refused_by_real_schema(key):
    from tools import bridge_v2_event_schema as schema
    _, broken = bound_pair()
    del broken[key]
    with pytest.raises(ValueError, match=f"payload contract field {key} requires top-level field"):
        schema.validate_event_for_write(broken)
    result = utility().dry_run(broken)
    assert result["status"] == "REFUSED"
    assert result["schema_valid"] is False
    assert result["merge_authorized"] is False
    assert "EVENT_SCHEMA" in result["reasons"]

