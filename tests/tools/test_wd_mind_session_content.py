"""Synthetic contract for the default-off content session API.

No real memory, model, native provider, installed publication or live log is
opened. Existing independently constructed content fixtures exercise the real
source verifier through the session seam; smaller spies isolate dispatch rules.
This seam is an in-process API, not provider startup or live context delivery.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tools import wd_bridge_mind_session as session
from tools import wd_mind_content_query_readonly as content


PROVIDER_KEYS = {"encoder_provider", "index_provider"}
INPUT_KEYS = content._Q4_KEYS - PROVIDER_KEYS
TOKEN = "mind_session_content_refused"


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
