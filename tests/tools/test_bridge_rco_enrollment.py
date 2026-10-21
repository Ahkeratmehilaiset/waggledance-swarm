"""Synthetic-only enrollment attestations; no fixture represents a real session."""

import hashlib
import json

import pytest

from tools import bridge_rco_enrollment as enrollment


GENERATION = "a" * 40
ROLE_UUID = "3c7f49d1-5ac4-4ca3-8ff5-6c97c07bcd50"
SYNTHETIC_NATIVE_UUID = "11111111-2222-4333-8444-555555555555"


def record():
    return {
        "schema": "wd.rco-enrollment.v1",
        "agent": "claude-rco-3",
        "agent_uuid": ROLE_UUID,
        "generation": GENERATION,
        "session_id": "synthetic-session-not-a-real-session",
        "run_id": "synthetic-run-not-a-real-run",
        "native_conversation_id": SYNTHETIC_NATIVE_UUID,
        "cli_kind": "claude.exe",
        "native_pid": 4242,
        "launcher_pid": 4243,
        "native_process_start_utc": "2026-10-21T01:00:00.1234567Z",
        "observed_at_utc": "2026-10-21T01:00:01+00:00",
        "token_integrity": "Medium",
        "elevated": False,
        "fresh_history_free": True,
        "no_mind_authorship": True,
        "no_measurement_case_access": True,
    }


def manifest(value=...):
    result = {"schema_version": 1, "source_commit": GENERATION, "files": {}}
    if value is not ...:
        result["rco_enrollments"] = value
    return result


def publish(tmp_path, value=None, *, raw=None, root_name=GENERATION):
    root = tmp_path / root_name
    root.mkdir(exist_ok=True)
    data = raw if raw is not None else json.dumps(
        manifest({"claude-rco-3": record()}) if value is None else value
    ).encode("utf-8")
    (root / "deployment-manifest.json").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return root, digest


def context(tmp_path, value=None, **kwargs):
    root, digest = publish(tmp_path, value, **kwargs)
    return enrollment.load_rco_enrollment_context(
        bundle_root=root, expected_manifest_sha256=digest
    )


def event(ctx):
    rec = record()
    return {
        "agent": rec["agent"], "agent_uuid": rec["agent_uuid"],
        "session_id": rec["session_id"], "run_id": rec["run_id"],
        "payload": {"execution_evidence": {
            "observed_agent": rec["agent"],
            **{key: rec[key] for key in (
                "native_conversation_id", "native_pid", "native_process_start_utc",
                "launcher_pid", "cli_kind", "generation",
            )},
            "manifest_sha256": ctx["manifest_sha256"],
            "observed_at_utc": "2026-10-21T01:00:01Z",
            "pin_status": "manifest_and_launcher_verified",
            "pin_error": None, "ancestry_error": None,
        }},
    }


def test_valid_operator_attestation_and_bound_event(tmp_path):
    ctx = context(tmp_path)
    assert isinstance(ctx, dict)
    assert enrollment.rco_enrollment_status("claude-rco-3", context=ctx) == "enrolled"
    assert enrollment.rco_event_enrollment_matches(event(ctx), context=ctx)
    assert "marker" not in json.dumps(ctx)


@pytest.mark.parametrize("agent", ["claude-rco-1", "claude-rco-2"])
def test_legacy_identity_unchanged_without_context(agent):
    assert enrollment.rco_enrollment_status(agent, context={}) == "not_required"
    assert enrollment.rco_event_enrollment_matches({"agent": agent}, context={})


def test_unknown_non_rco_event_is_not_an_enrolled_reviewer():
    assert not enrollment.rco_event_enrollment_matches({"agent": "claude-rco-4"}, context={})


@pytest.mark.parametrize("value", [manifest(), manifest({}), manifest({"other": {}})])
def test_verified_missing_record_is_not_provisioned(tmp_path, value):
    ctx = context(tmp_path, value)
    assert enrollment.rco_enrollment_status("claude-rco-3", context=ctx) == "not_provisioned"
    assert not enrollment.rco_event_enrollment_matches(event(ctx), context=ctx)


@pytest.mark.parametrize("value", [None, [], "", False, 0])
def test_malformed_enrollment_container_is_unknown(tmp_path, value):
    ctx = context(tmp_path, manifest(value))
    assert enrollment.rco_enrollment_status("claude-rco-3", context=ctx) == "unknown"


@pytest.mark.parametrize("field", list(record()))
def test_missing_record_field_is_unknown(tmp_path, field):
    rec = record()
    del rec[field]
    assert context(tmp_path, manifest({"claude-rco-3": rec}))["status"] == "unknown"


@pytest.mark.parametrize("field,value", [
    ("schema", "wrong"), ("agent", "claude-rco-1"), ("agent_uuid", SYNTHETIC_NATIVE_UUID),
    ("generation", "b" * 40), ("session_id", ""), ("session_id", "   "),
    ("session_id", None), ("run_id", False), ("native_conversation_id", ROLE_UUID),
    ("native_conversation_id", "not-a-uuid"), ("cli_kind", "claude.cmd"),
    ("native_pid", True), ("native_pid", 0), ("native_pid", "4242"),
    ("launcher_pid", False), ("launcher_pid", -1),
    ("native_process_start_utc", "2026-10-21T01:00:02Z"),
    ("native_process_start_utc", "2026-10-21T01:00:00"),
    ("observed_at_utc", "not-a-time"), ("observed_at_utc", "2026-02-30T01:00:00Z"),
    ("token_integrity", "High"), ("elevated", True), ("elevated", 0),
    ("fresh_history_free", 1), ("no_mind_authorship", False),
    ("no_measurement_case_access", None), ("extra", True),
])
def test_malformed_record_is_unknown(tmp_path, field, value):
    rec = record()
    rec[field] = value
    assert context(tmp_path, manifest({"claude-rco-3": rec}))["status"] == "unknown"


def test_time_order_retains_sub_microsecond_precision(tmp_path):
    rec = record()
    rec["native_process_start_utc"] = "2026-10-21T01:00:00.1234568Z"
    rec["observed_at_utc"] = "2026-10-21T01:00:00.1234567Z"
    assert context(tmp_path, manifest({"claude-rco-3": rec}))["status"] == "unknown"


@pytest.mark.parametrize("raw", [
    b'{"source_commit":"' + GENERATION.encode() + b'","source_commit":"' + GENERATION.encode() + b'"}',
    b'{"source_commit":"' + GENERATION.encode() + b'","x":NaN}',
    b'{"source_commit":"' + GENERATION.encode() + b'","x":1e9999}',
    b"[]", b"null", b"\xff", b"{", b"",
])
def test_bad_pinned_json_is_unknown(tmp_path, raw):
    assert context(tmp_path, raw=raw)["status"] == "unknown"


def test_pin_mismatch_precedes_json_parse(tmp_path, monkeypatch):
    root, _ = publish(tmp_path, raw=b"not json")
    def forbidden_parse(*args, **kwargs):
        raise AssertionError("unpinned JSON must not be parsed")
    monkeypatch.setattr(enrollment.json, "loads", forbidden_parse)
    ctx = enrollment.load_rco_enrollment_context(bundle_root=root, expected_manifest_sha256="0" * 64)
    assert ctx["status"] == "unknown"


def test_one_mib_bound(tmp_path):
    data = json.dumps(manifest()).encode()
    assert context(tmp_path, raw=data + b" " * (1048576 - len(data)))["status"] == "not_provisioned"
    assert context(tmp_path, raw=data + b" " * (1048577 - len(data)))["status"] == "unknown"


def test_wrong_generation_root_is_unknown(tmp_path):
    assert context(tmp_path, root_name="b" * 40)["status"] == "unknown"


@pytest.mark.parametrize("root,pin", [("", None), (None, ""), (None, "bad"), (None, False)])
def test_invalid_explicit_input_never_falls_back(tmp_path, monkeypatch, root, pin):
    good_root, good_pin = publish(tmp_path)
    monkeypatch.setenv("WD_BRIDGE_BUNDLE_ROOT", str(good_root))
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", good_pin)
    assert enrollment.load_rco_enrollment_context(bundle_root=root, expected_manifest_sha256=pin)["status"] == "unknown"


def test_only_documented_environment_defaults(tmp_path, monkeypatch):
    root, digest = publish(tmp_path)
    monkeypatch.setenv("WD_BRIDGE_BUNDLE_ROOT", str(root))
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", digest)
    assert enrollment.load_rco_enrollment_context()["status"] == "enrolled"
    monkeypatch.delenv("WD_BRIDGE_BUNDLE_ROOT")
    monkeypatch.setenv("WD_BRIDGE_CODE_ROOT", str(root))
    assert enrollment.load_rco_enrollment_context()["status"] == "unknown"


def test_missing_manifest_is_unknown(tmp_path):
    ctx = enrollment.load_rco_enrollment_context(bundle_root=tmp_path / GENERATION, expected_manifest_sha256="0" * 64)
    assert ctx["status"] == "unknown"


def test_serialized_or_forged_context_is_never_an_attestation(tmp_path):
    ctx = context(tmp_path)
    for forged in (dict(ctx), json.loads(json.dumps(ctx)), {"status": "enrolled"}, False, []):
        assert enrollment.rco_enrollment_status("claude-rco-3", context=forged) == "unknown"
        assert not enrollment.rco_event_enrollment_matches(event(ctx), context=forged)


def test_context_mutation_cannot_promote_or_rebind(tmp_path):
    ctx = context(tmp_path)
    with pytest.raises(TypeError):
        ctx["status"] = "enrolled"
    with pytest.raises(TypeError):
        ctx["enrollment"]["session_id"] = "forged"
    # Explicit base-class mutation is also detected; this is not an OS/process security boundary.
    dict.__setitem__(ctx, "status", "unknown")
    assert enrollment.rco_enrollment_status("claude-rco-3", context=ctx) == "unknown"
    assert not enrollment.rco_event_enrollment_matches(event(ctx), context=ctx)


@pytest.mark.parametrize("where,key,value", [
    ("top", "agent_uuid", SYNTHETIC_NATIVE_UUID), ("top", "session_id", ""),
    ("top", "run_id", None), ("proof", "observed_agent", None),
    ("proof", "native_conversation_id", ROLE_UUID), ("proof", "native_pid", True),
    ("proof", "native_pid", 0), ("proof", "launcher_pid", False),
    ("proof", "cli_kind", "claude.cmd"), ("proof", "generation", "b" * 40),
    ("proof", "manifest_sha256", "0" * 64), ("proof", "pin_status", "unknown"),
    ("proof", "pin_error", "error"), ("proof", "pin_error", False),
    ("proof", "ancestry_error", "error"),
    ("proof", "native_process_start_utc", "2026-10-21T01:00:02Z"),
    ("proof", "observed_at_utc", None), ("proof", "observed_at_utc", ""),
])
def test_event_binding_mismatch_refuses(tmp_path, where, key, value):
    ctx = context(tmp_path)
    candidate = event(ctx)
    target = candidate if where == "top" else candidate["payload"]["execution_evidence"]
    target[key] = value
    assert not enrollment.rco_event_enrollment_matches(candidate, context=ctx)


@pytest.mark.parametrize("key", list(event({"manifest_sha256": "0" * 64})["payload"]["execution_evidence"]))
def test_missing_required_event_evidence_refuses(tmp_path, key):
    ctx = context(tmp_path)
    candidate = event(ctx)
    del candidate["payload"]["execution_evidence"][key]
    if key not in {"pin_error", "ancestry_error"}:
        assert not enrollment.rco_event_enrollment_matches(candidate, context=ctx)


def test_event_without_proof_or_self_asserted_enrollment_refuses(tmp_path):
    ctx = context(tmp_path)
    candidate = event(ctx)
    candidate["payload"] = {"enrollment": True, "handshake": True, "profile": "rco3"}
    assert not enrollment.rco_event_enrollment_matches(candidate, context=ctx)
    assert not enrollment.rco_event_enrollment_matches(None, context=ctx)


def test_equivalent_utc_instants_match(tmp_path):
    ctx = context(tmp_path)
    candidate = event(ctx)
    candidate["payload"]["execution_evidence"]["native_process_start_utc"] = "2026-10-21T01:00:00.123456700+00:00"
    assert enrollment.rco_event_enrollment_matches(candidate, context=ctx)


def test_record_input_is_not_retained_as_mutable_alias(tmp_path):
    rec = record()
    ctx = context(tmp_path, manifest({"claude-rco-3": rec}))
    rec["session_id"] = "changed"
    assert enrollment.rco_event_enrollment_matches(event(ctx), context=ctx)


def test_observation_receipt_survives_new_process_and_bridge_run(tmp_path):
    # The operator pins an actually observed conversation in future M BEFORE its
    # cold launch. Old registration PIDs/run labels cannot pin the future launch.
    ctx = context(tmp_path)
    candidate = event(ctx)
    candidate.update(session_id="synthetic-current-session", run_id="synthetic-current-run")
    candidate["payload"]["execution_evidence"].update(
        native_pid=5252, launcher_pid=5253,
        native_process_start_utc="2026-10-21T02:00:00Z",
        observed_at_utc="2026-10-21T02:00:01Z",
    )
    assert enrollment.rco_event_enrollment_matches(candidate, context=ctx)


def test_current_lifetime_order_retains_seventh_fractional_digit(tmp_path):
    ctx = context(tmp_path)
    candidate = event(ctx)
    candidate["payload"]["execution_evidence"].update(
        native_process_start_utc="2026-10-21T01:00:00.1234568Z",
        observed_at_utc="2026-10-21T01:00:00.1234567Z",
    )
    assert not enrollment.rco_event_enrollment_matches(candidate, context=ctx)


def test_unknown_report_cannot_be_promoted_by_dict_mutation(tmp_path):
    ctx = context(tmp_path, manifest(None))
    dict.__setitem__(ctx, "status", "enrolled")
    dict.__setitem__(ctx, "enrollment", record())
    assert enrollment.rco_enrollment_status("claude-rco-3", context=ctx) == "unknown"
    assert not enrollment.rco_event_enrollment_matches(event(ctx), context=ctx)
