"""Synthetic contract for the default-off content session API.

No real memory, model, native provider, installed publication or live log is
opened. Existing independently constructed content fixtures exercise the real
source verifier through the session seam; smaller spies isolate dispatch rules.
This seam is an in-process API, not provider startup or live context delivery.
"""
from __future__ import annotations

import hashlib
import builtins
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import wd_bridge_mind_session as session
from tools import wd_mind_content_query_readonly as content
from tools import bridge_compact_view


PROVIDER_KEYS = {"encoder_provider", "index_provider"}
INPUT_KEYS = content._Q4_KEYS - PROVIDER_KEYS
TOKEN = "mind_session_content_refused"


def _situation_inputs():
    def raw(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    def digest(value):
        return hashlib.sha256(value).hexdigest()
    lanes = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
    uuids = {lane: f"{i:08x}-1111-4111-8111-111111111111" for i, lane in enumerate(lanes, 1)}
    fleet = raw({"lanes": [{"agent": lane, "agent_uuid": uuids[lane]} for lane in lanes
                           if lane != "codex-tools-1"],
                 "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": uuids["codex-tools-1"]}})
    commit = "a" * 40
    manifest = raw({"schema_version": 1, "source_commit": commit,
                    "files": {"wd-fleet.json": digest(fleet)}})
    pointer = raw({"schema_version": 1, "source_commit": commit, "final_commit": commit,
                   "active_bundle": "C:\\Python\\wd-reboot-bundles\\" + commit,
                   "manifest_sha256": digest(manifest), "final_manifest_sha256": digest(manifest)})
    event = {"agent": "codex-lead-1", "agent_uuid": uuids["codex-lead-1"], "type": "claim",
             "ts_utc": "2026-11-14T05:29:00Z", "task_id": "lead-synthetic-20261114"}
    ref = bridge_compact_view.event_id(event)
    snapshot = raw([{"ref": ref, "event": event}])
    return {"snapshot_bytes": snapshot, "snapshot_sha256": digest(snapshot),
            "fleet_bytes": fleet, "fleet_sha256": digest(fleet),
            "pointer_bytes": pointer, "pointer_sha256": digest(pointer),
            "manifest_bytes": manifest, "manifest_sha256": digest(manifest),
            "pointer_observed_utc": "2026-11-14T05:29:30Z", "as_of_utc": "2026-11-14T05:30:00Z",
            "ttl_seconds": 900}, ref


def _situation_refused(operation):
    with pytest.raises(ValueError) as caught:
        operation()
    assert type(caught.value).__name__ == "MindSituationSessionRefused"
    assert str(caught.value) == "mind_session_situation_refused"
    assert caught.value.__context__ is None


@pytest.mark.parametrize("enabled", [None, False, 1, "1"])
def test_situation_session_default_off_and_ignores_environment(monkeypatch, enabled):
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("WD_MIND_SITUATION_ENABLED", "1")
    inputs, _ = _situation_inputs()
    _situation_refused(lambda: session.project_situation_session(inputs=inputs))
    _situation_refused(lambda: session.project_situation_session(enabled=enabled, inputs=inputs))


def test_situation_session_refuses_even_well_formed_inputs_until_trust_is_established():
    inputs, _ = _situation_inputs()
    original = dict(inputs)
    _situation_refused(lambda: session.project_situation_session(enabled=True, inputs=inputs))
    assert inputs == original


def test_situation_session_does_not_return_stale_untrusted_input():
    inputs, _ = _situation_inputs()
    inputs["as_of_utc"] = "2026-11-14T06:00:00Z"
    _situation_refused(lambda: session.project_situation_session(enabled=True, inputs=inputs))


@pytest.mark.parametrize("fault", ["missing", "extra", "bad_pin"])
def test_situation_session_rejects_incomplete_or_bad_pins(fault):
    inputs, _ = _situation_inputs()
    if fault == "missing": inputs.pop("fleet_sha256")
    if fault == "extra": inputs["allow_missing_bundle"] = True
    if fault == "bad_pin": inputs["snapshot_sha256"] = "0" * 64
    _situation_refused(lambda: session.project_situation_session(enabled=True, inputs=inputs))


def test_situation_session_missing_packaged_module_is_refused(monkeypatch):
    inputs, _ = _situation_inputs()
    original = builtins.__import__
    def no_projection(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tools" and "wd_mind_situation_view" in fromlist:
            raise ImportError("synthetic private module path")
        return original(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, "__import__", no_projection)
    _situation_refused(lambda: session.project_situation_session(enabled=True, inputs=inputs))


def test_situation_session_refuses_preloaded_fake_module(monkeypatch):
    import tools
    calls = []
    fake = SimpleNamespace(project_situation=lambda **kwargs: calls.append(kwargs) or {
        "schema": "wd.mind-situation-observations.v1", "authority": "none",
        "scope": "bounded_tail_not_current_state_or_gate"})
    monkeypatch.setitem(sys.modules, "tools.wd_mind_situation_view", fake)
    monkeypatch.setattr(tools, "wd_mind_situation_view", fake, raising=False)
    inputs, _ = _situation_inputs()
    _situation_refused(lambda: session.project_situation_session(enabled=True, inputs=inputs))
    assert calls == []


def _situation_cli_args(tmp_path):
    inputs, ref = _situation_inputs()
    args = ["--situation"]
    for name in ("snapshot", "fleet", "pointer", "manifest"):
        path = tmp_path / (name + ".json")
        path.write_bytes(inputs[name + "_bytes"])
        args += ["--" + name, str(path), "--" + name + "-sha256", inputs[name + "_sha256"]]
    args += ["--pointer-observed-utc", inputs["pointer_observed_utc"], "--as-of-utc", inputs["as_of_utc"],
             "--ttl-seconds", str(inputs["ttl_seconds"])]
    return args, ref


def test_situation_cli_separate_process_yields_data_only_source_ref(tmp_path):
    args, ref = _situation_cli_args(tmp_path)
    result = subprocess.run([sys.executable, "-B", "-m", "tools.wd_bridge_mind_session", *args],
                            cwd=str(Path(session.__file__).resolve().parents[1]),
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0 and result.stderr == ""
    view = json.loads(result.stdout)
    assert view["schema"] == "wd.mind-situation-observations.v1" and view["authority"] == "none"
    assert view["source"]["sender_authentication"] == "UNKNOWN"
    assert view["lanes"]["codex-lead-1"]["latest_claim_event"]["event_ref"] == ref


@pytest.mark.parametrize("fault", ["missing_argument", "bad_manifest_anchor", "bad_snapshot_pin", "duplicate_argument"])
def test_situation_cli_refuses_missing_and_mismatched_pins(tmp_path, capsys, fault):
    args, _ = _situation_cli_args(tmp_path)
    if fault == "missing_argument": args = args[:-2]
    if fault == "bad_manifest_anchor": args[args.index("--manifest-sha256") + 1] = "0" * 64
    if fault == "bad_snapshot_pin": args[args.index("--snapshot-sha256") + 1] = "0" * 64
    if fault == "duplicate_argument": args += ["--ttl-seconds", "900"]
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "mind_session_situation_refused", "authority": "none"}


def test_situation_cli_refuses_missing_projector_module(tmp_path, capsys, monkeypatch):
    args, _ = _situation_cli_args(tmp_path)
    original = builtins.__import__
    def no_projection(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "tools" and "wd_mind_situation_view" in fromlist:
            raise ImportError("synthetic private module path")
        return original(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, "__import__", no_projection)
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "synthetic private" not in captured.err
    assert json.loads(captured.err)["error"] == "mind_session_situation_refused"


def test_situation_cli_keeps_review_unknown_for_generic_request(tmp_path, capsys):
    args, _ = _situation_cli_args(tmp_path)
    path = Path(args[args.index("--snapshot") + 1])
    records = json.loads(path.read_bytes())
    event = dict(records[0]["event"], type="request", status="request", request_id="synthetic-request",
                 to="claude-rco-1", payload={"pr": 2137, "head": "b" * 40})
    records.append({"ref": bridge_compact_view.event_id(event), "event": event})
    snapshot = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(snapshot)
    args[args.index("--snapshot-sha256") + 1] = hashlib.sha256(snapshot).hexdigest()
    assert session.main(args) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["request_observations"][0]["requested_review"] == "UNKNOWN"


@pytest.mark.parametrize("fault", ["invented_ref", "wrong_time", "raw_task", "extra_payload", "wrong_bundle_age"])
def test_situation_cli_refuses_projection_with_unbound_or_unfiltered_data(tmp_path, capsys, monkeypatch, fault):
    from tools import wd_mind_situation_view as projection
    args, _ = _situation_cli_args(tmp_path)
    original = projection.project_situation
    def untrusted(**kwargs):
        view = original(**kwargs)
        observation = view["lanes"]["codex-lead-1"]["latest_claim_event"]
        if fault == "invented_ref": observation["event_ref"] = "0" * 64
        if fault == "wrong_time": observation["observed_utc"] = "2026-11-14T05:29:01Z"
        if fault == "raw_task": observation["task_id"] = "customer-private-data"
        if fault == "extra_payload": observation["raw_message"] = "synthetic-private-message"
        if fault == "wrong_bundle_age": view["bundle"]["age_seconds_upper_bound"] = 0
        return view
    monkeypatch.setattr(projection, "project_situation", untrusted)
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "synthetic-private-message" not in captured.err
    assert json.loads(captured.err)["error"] == "mind_session_situation_refused"


def _entry():
    result = getattr(session, "query_content_session", None)
    assert callable(result), "default-off content session seam is not implemented"
    return result


def _refused(operation):
    with pytest.raises(ValueError) as caught:
        operation()
    assert type(caught.value).__name__ == "MindContentSessionRefused"
    assert str(caught.value) == TOKEN
    assert caught.value.__context__ is None


def _dummy_inputs():
    return dict({key: object() for key in INPUT_KEYS}, enabled=True)


def _providers():
    def forbidden(*args, **kwargs):
        pytest.fail("provider callback reached before full source verification")
    return (SimpleNamespace(identity={}, query=forbidden),
            SimpleNamespace(identity={}, load=forbidden))


@pytest.fixture
def dispatch(monkeypatch):
    calls = []

    def query(**inputs):
        calls.append(inputs)
        return {"schema": "wd.q4-content-snapshot-query.v2", "authority": "none",
                "status": "ABSTAIN", "provider_authentication": "UNKNOWN", "results": []}

    monkeypatch.setattr(content, "query_content_snapshot", query)
    return calls


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "1", [], {}])
def test_content_session_requires_exact_true_before_query(enabled, dispatch):
    entry = _entry()
    _refused(lambda: entry(enabled=enabled, inputs=_dummy_inputs(), providers=_providers()))
    assert dispatch == []


def test_content_session_is_disabled_by_default(dispatch):
    entry = _entry()
    _refused(lambda: entry(inputs=_dummy_inputs(), providers=_providers()))
    assert dispatch == []


def test_content_session_environment_cannot_enable_it(monkeypatch, dispatch):
    entry = _entry()
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("WD_MIND_CONTENT_ENABLED", "1")
    _refused(lambda: entry(inputs=_dummy_inputs(), providers=_providers()))
    assert dispatch == []


@pytest.mark.parametrize("inputs", [None, {}, [], (), {"enabled": True}])
def test_content_session_missing_input_surface_has_no_query_callback(inputs, dispatch):
    entry = _entry()
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=_providers()))
    assert dispatch == []


@pytest.mark.parametrize("fault", ["none", "list", "short", "long", "encoder_none", "index_none", "all_none"])
def test_content_session_requires_explicit_two_provider_tuple(fault, dispatch):
    entry = _entry()
    providers = _providers()
    providers = {
        "none": None, "list": list(providers), "short": providers[:1],
        "long": providers + (object(),), "encoder_none": (None, providers[1]),
        "index_none": (providers[0], None), "all_none": (None, None),
    }[fault]
    _refused(lambda: entry(enabled=True, inputs=_dummy_inputs(), providers=providers))
    assert dispatch == []


@pytest.mark.parametrize("fault", ["extra", "encoder_provider", "index_provider", "inner_disabled"])
def test_content_session_input_set_is_closed_and_unambiguous(fault, dispatch):
    entry = _entry()
    inputs = _dummy_inputs()
    if fault == "inner_disabled":
        inputs["enabled"] = False
    else:
        inputs[fault] = object()
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=_providers()))
    assert dispatch == []


@pytest.mark.parametrize("missing", sorted(INPUT_KEYS))
def test_content_session_requires_every_explicit_binding(missing, dispatch):
    entry = _entry()
    inputs = _dummy_inputs()
    inputs.pop(missing)
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=_providers()))
    assert dispatch == []


def test_content_session_forwards_exact_owned_inputs_without_defaults_or_mutation(dispatch):
    entry = _entry()
    inputs, providers = _dummy_inputs(), _providers()
    original = dict(inputs)
    result = entry(enabled=True, inputs=inputs, providers=providers)
    assert result["status"] == "ABSTAIN" and result["authority"] == "none"
    assert result["provider_authentication"] == "UNKNOWN"
    assert inputs == original and len(dispatch) == 1
    forwarded = dispatch[0]
    assert set(forwarded) == content._Q4_KEYS
    assert all(forwarded[key] is value for key, value in original.items())
    assert forwarded["encoder_provider"] is providers[0]
    assert forwarded["index_provider"] is providers[1]


@pytest.mark.parametrize("fault", ["reader_refusal", "unsafe_exception"])
def test_content_session_contains_refusals_without_exception_text_or_context(fault, monkeypatch):
    entry = _entry()

    def fail(**inputs):
        if fault == "reader_refusal":
            raise content.ContentQueryRefused("content_query_refused")
        raise RuntimeError("synthetic-private-provider-detail")

    monkeypatch.setattr(content, "query_content_snapshot", fail)
    _refused(lambda: entry(enabled=True, inputs=_dummy_inputs(), providers=_providers()))


def test_existing_status_cli_stays_status_only(monkeypatch, capsys, tmp_path, dispatch):
    # Positive pre-Source regression control: the old status CLI already works.
    agent, uuid = "codex-tools-1", "00000000-0000-4000-8000-000000000002"
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", agent)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", uuid)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(tmp_path / "synthetic-runtime"))
    monkeypatch.setattr(session, "_fleet_roster", lambda *args: {agent: uuid})
    monkeypatch.setattr(session.bridge_compact_view, "_read_view_rows", lambda *args: ([], "synthetic-cursor", None))
    expected = {"schema": "synthetic-status-projection", "authority": "none", "lane": agent}
    monkeypatch.setattr(session.lane_reader, "lane_read", lambda **kwargs: expected)
    audit_root = tmp_path / ".codex-audit"
    latest = audit_root / "mind-live" / "latest.json"
    latest.parent.mkdir(parents=True)
    latest.write_text("{}", encoding="utf-8")
    result = session.main(["--latest", str(latest),
                           "--audit-root", str(audit_root),
                           "--fleet", str(tmp_path / "synthetic-fleet"), "--fleet-sha256", "0" * 64])
    captured = capsys.readouterr()
    assert result == 0 and captured.err == ""
    output = json.loads(captured.out)
    assert (output["schema"], output["authority"], output["lane"]) == ("synthetic-status-projection", "none", agent)
    assert output["fleet_sha256"] == "0" * 64 and output["source_event_refs"] == []
    assert output["status_observation"]["schema"] == "wd.mind-direct-status.v1"
    assert output["status_observation"]["observation_status"] == "UNKNOWN"
    assert dispatch == []


@pytest.fixture(scope="module")
def source_fixtures():
    # Reuse the existing independently constructed fixture, not the production
    # projector/writer. Explicit path prevents resolving an installed tests package.
    path = Path(__file__).with_name("test_wd_mind_content_query_readonly.py")
    spec = importlib.util.spec_from_file_location("session_content_source_fixtures", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


def _split(case):
    values = dict(case)
    providers = values.pop("encoder_provider"), values.pop("index_provider")
    assert set(values) == INPUT_KEYS
    return values, providers


def test_content_session_uses_real_verification_and_synthetic_query(source_fixtures, tmp_path):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    result = entry(enabled=True, inputs=inputs, providers=providers)
    assert result["schema"] == "wd.q4-content-snapshot-query.v2"
    assert result["status"] == "ANSWER" and result["results"]
    assert result["authority"] == "none" and result["provider_authentication"] == "UNKNOWN"
    assert case["stream"].reads == 1 and len(providers[0].calls) == 1
    assert len(providers[1].calls) == len(case["cell_snapshots"])


@pytest.mark.parametrize("fault", ["source_bytes", "descriptor_pin", "second_cell", "provider_identity", "documents"])
def test_content_session_preserves_source_and_binding_refusal_before_callbacks(source_fixtures, tmp_path, fault):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    if fault == "source_bytes":
        path = Path(inputs["messages"]["source_path"])
        raw = path.read_bytes()
        changed = raw.replace(b"network retry", b"altered retry", 1)
        assert changed != raw and len(changed) == len(raw)
        path.write_bytes(changed)
    elif fault == "descriptor_pin":
        inputs["descriptor_sha256"] = "0" * 64
    elif fault == "second_cell":
        inputs["cell_snapshots"]["system"]["artifacts"]["index"] += b"tampered"
    elif fault == "provider_identity":
        providers[0].identity["encoder"]["revision"] = "synthetic-changed"
    else:
        inputs["documents"] = {}
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=providers))
    assert case["stream"].reads == 0 and providers[0].calls == [] and providers[1].calls == []


def test_content_session_late_source_change_never_returns_partial_content(source_fixtures, tmp_path, capsys):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    path = Path(inputs["messages"]["source_path"])

    def change(index):
        path.write_bytes(path.read_bytes().replace(b"network retry", b"altered retry", 1))

    providers[1].on_load = change
    _refused(lambda: entry(enabled=True, inputs=inputs, providers=providers))
    assert providers[1].calls
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_content_session_does_not_write_files_or_change_environment(source_fixtures, tmp_path):
    entry = _entry()
    case = source_fixtures._semantic_case(content, tmp_path)
    inputs, providers = _split(case)
    before_env = dict(os.environ)
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in tmp_path.rglob("*") if path.is_file()}
    assert entry(enabled=True, inputs=inputs, providers=providers)["status"] == "ANSWER"
    assert dict(os.environ) == before_env
    assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in tmp_path.rglob("*") if path.is_file()} == before
