"""Synthetic contract for the default-off content session API.

No real memory, model, native provider, installed publication or live log is
opened. Existing independently constructed content fixtures exercise the real
source verifier through the session seam; smaller spies isolate dispatch rules.
This seam is an in-process API, not provider startup or live context delivery.
"""
from __future__ import annotations

import hashlib
import builtins
import base64
from datetime import datetime, timezone
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


def _situation_live_cli_args(tmp_path):
    inputs, _ = _situation_inputs()
    root = tmp_path / "runtime"
    log = root / "shared" / "events.jsonl"
    log.parent.mkdir(parents=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    event = json.loads(inputs["snapshot_bytes"])[0]["event"]
    event["ts_utc"] = now
    fleet = json.loads(inputs["fleet_bytes"])
    rco = next(entry for entry in fleet["lanes"] if entry["agent"] == "claude-rco-1")
    rco_event = dict(event, agent="claude-rco-1", agent_uuid=rco["agent_uuid"],
                     message="synthetic-private-rco-text", payload={"private": "synthetic-private-rco-text"})
    log.write_bytes(b"".join(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                             for row in (event, rco_event)))
    args = ["--situation-live", "--bridge-root", str(root)]
    for name in ("fleet", "pointer", "manifest"):
        path = tmp_path / (name + ".json")
        path.write_bytes(inputs[name + "_bytes"])
        args += ["--" + name, str(path), "--" + name + "-sha256", inputs[name + "_sha256"]]
    args += ["--pointer-observed-utc", now, "--ttl-seconds", "900"]
    return args, log, event


def _situation_cursor(log):
    from waggledance.core import bridge_log_reader
    with bridge_log_reader._open_log(log) as stream:
        identity = bridge_log_reader._file_identity(stream)
    data = {"offset": log.stat().st_size, "file_identity": identity, "generation": None}
    return "bc1." + base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def _progress_snapshot_cli_args(tmp_path):
    args, _ = _situation_cli_args(tmp_path)
    path = Path(args[args.index('--snapshot') + 1])
    rows = json.loads(path.read_bytes())
    progress = dict(rows[0]['event'], type='status', status='in_progress',
                    ts_utc='2026-11-14T05:29:30Z', task_id='invented-progress-b',
                    message='invented-private-progress-body')
    rows.append({'ref': bridge_compact_view.event_id(progress), 'event': progress})
    data = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(data)
    args[args.index('--snapshot-sha256') + 1] = hashlib.sha256(data).hexdigest()
    return args, rows


def test_situation_v2_cli_observes_new_progress_and_keeps_v1_default(tmp_path, capsys):
    args, rows = _progress_snapshot_cli_args(tmp_path)
    assert session.main(args) == 0
    old = json.loads(capsys.readouterr().out)
    assert 'latest_observed_task' not in old['lanes']['codex-lead-1']
    assert session.main(args + ['--task-events-v2']) == 0
    captured = capsys.readouterr()
    assert captured.err == '' and 'invented-private-progress-body' not in captured.out
    result = json.loads(captured.out)
    assert result['schema'] == 'wd.mind-situation-observations.v2'
    observed = result['lanes']['codex-lead-1']['latest_observed_task']
    assert observed['event_ref'] == rows[1]['ref'] and observed['activity'] == 'UNKNOWN'


def test_situation_v2_live_cli_uses_existing_reader_and_keeps_unknown_authority(tmp_path, capsys):
    args, log, event = _situation_live_cli_args(tmp_path)
    progress = dict(event, type='status', status='in_progress', message='invented-private-progress')
    log.write_bytes(json.dumps(progress).encode() + b'\n')
    before = {str(path.relative_to(tmp_path)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in tmp_path.rglob('*') if path.is_file()}
    assert session.main(args + ['--task-events-v2']) == 0
    captured = capsys.readouterr()
    assert captured.err == '' and 'invented-private-progress' not in captured.out
    result = json.loads(captured.out)
    assert result['schema'] == 'wd.mind-situation-live.v2'
    assert result['current_state'] == result['continuity'] == 'UNKNOWN'
    observed = result['view']['lanes']['codex-lead-1']['latest_observed_task']
    assert observed['event_ref'] == bridge_compact_view.event_id(progress)
    after = {str(path.relative_to(tmp_path)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in tmp_path.rglob('*') if path.is_file()}
    assert before == after


@pytest.mark.parametrize('fault', ['older', 'unknown', 'wrong_task', 'wrong_time', 'wrong_age',
    'active', 'wrong_status', 'raw_body', 'current_task', 'schema'])
def test_situation_v2_validator_rejects_unbound_or_invented_observation(tmp_path, capsys, monkeypatch, fault):
    from tools import wd_mind_situation_view as projection
    args, rows = _progress_snapshot_cli_args(tmp_path)
    original = projection.project_situation
    called = []
    def untrusted(**kwargs):
        called.append(True)
        result = original(**kwargs)
        lane = result['lanes']['codex-lead-1']
        observed = lane['latest_observed_task']
        if fault == 'older':
            lane['latest_observed_task'] = dict(lane['latest_work_event'], declared_status='UNKNOWN', activity='UNKNOWN')
        if fault == 'unknown': lane['latest_observed_task'] = 'UNKNOWN'
        if fault == 'wrong_task': observed['task_id'] = 'sha256:' + '0' * 64
        if fault == 'wrong_time': observed['observed_utc'] = '2026-11-14T05:29:29Z'
        if fault == 'wrong_age': observed['age_seconds_upper_bound'] = 0
        if fault == 'active': observed['activity'] = 'ACTIVE'
        if fault == 'wrong_status': observed['declared_status'] = 'done'
        if fault == 'raw_body': observed['message'] = 'invented-private-progress-body'
        if fault == 'current_task': lane['current_task'] = observed['task_id']
        if fault == 'schema': result['schema'] = 'wd.mind-situation-observations.v1'
        return result
    monkeypatch.setattr(projection, 'project_situation', untrusted)
    assert session.main(args + ['--task-events-v2']) == 2
    assert called == [True]
    captured = capsys.readouterr()
    assert captured.out == '' and json.loads(captured.err) == {'error': 'mind_session_situation_refused', 'authority': 'none'}


@pytest.mark.parametrize('flags', [['--task-events-v2', '--task-events-v2'], ['--task-events'],
                                 ['--task-events', '--task-events-v2']])
@pytest.mark.parametrize('live', [False, True])
def test_situation_v2_cli_rejects_duplicate_or_abbreviated_flag_before_read(tmp_path, capsys, monkeypatch, flags, live):
    args = _situation_live_cli_args(tmp_path)[0] if live else _situation_cli_args(tmp_path)[0]
    monkeypatch.setattr(session, '_situation_read_pinned', lambda *args: pytest.fail('duplicate flag read input'))
    assert session.main(args + flags) == 2
    assert capsys.readouterr().out == ''


def test_situation_v2_cannot_be_selected_by_projector_without_caller_opt_in(tmp_path, capsys, monkeypatch):
    from tools import wd_mind_situation_view as projection
    args, _ = _progress_snapshot_cli_args(tmp_path)
    original = projection.project_situation
    def upgrade(**kwargs):
        kwargs['include_task_events'] = True
        return original(**kwargs)
    monkeypatch.setattr(projection, 'project_situation', upgrade)
    assert session.main(args) == 2
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('recipients,accepted', [(5000, True), (7000, False)])
def test_situation_v2_serialized_64k_byte_limit(tmp_path, capsys, recipients, accepted):
    from tools import wd_mind_situation_view as projection
    args, rows = _progress_snapshot_cli_args(tmp_path)
    request = dict(rows[0]['event'], type='request', request_id='invented-request',
                   to=['fable-5'] * recipients)
    rows.append({'ref': bridge_compact_view.event_id(request), 'event': request})
    data = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    path = Path(args[args.index('--snapshot') + 1])
    path.write_bytes(data)
    args[args.index('--snapshot-sha256') + 1] = hashlib.sha256(data).hexdigest()
    source, _ = _situation_inputs()
    source.update(snapshot_bytes=data, snapshot_sha256=hashlib.sha256(data).hexdigest())
    result = projection.project_situation(**source, include_task_events=True)
    serialized = json.dumps(result, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    assert (len(serialized) <= 65536) is accepted
    assert session.main(args + ['--task-events-v2']) == (0 if accepted else 2)
    captured = capsys.readouterr()
    if accepted:
        assert captured.err == '' and captured.out.strip().encode('ascii') == serialized
    else:
        assert captured.out == ''
        assert json.loads(captured.err) == {'error': 'mind_session_situation_refused', 'authority': 'none'}


def _v2_validator_case(events, **options):
    from tools import wd_mind_situation_view as projection
    source, _ = _situation_inputs()
    data = json.dumps([{'ref': bridge_compact_view.event_id(event), 'event': event} for event in events],
                      sort_keys=True, separators=(',', ':')).encode()
    source.update(snapshot_bytes=data, snapshot_sha256=hashlib.sha256(data).hexdigest(), include_task_events=True)
    source.update(options)
    result = projection.project_situation(**source)
    session._situation_validate_output(result, source)
    return result, source


def _validator_input_case():
    source, _ = _situation_inputs()
    event = dict(json.loads(source['snapshot_bytes'])[0]['event'], type='status', status='in_progress')
    return _v2_validator_case([event])


def _bind_validator_input(result, source, name, raw, pin=None):
    source[name + '_bytes'] = raw
    source[name + '_sha256'] = hashlib.sha256(raw).hexdigest() if pin is None else pin
    group = 'source' if name in ('snapshot', 'fleet') else 'bundle'
    result[group][name + '_sha256'] = source[name + '_sha256']


@pytest.mark.parametrize('name', ['snapshot', 'fleet', 'pointer', 'manifest'])
def test_situation_v2_input_hashes_checked_before_any_parse(monkeypatch, name):
    result, source = _validator_input_case()
    _bind_validator_input(result, source, name, source[name + '_bytes'], '0' * 64)
    with pytest.raises(ValueError):
        session._situation_validate_output(result, source)
    monkeypatch.setattr(session.json, 'loads', lambda *a, **k: pytest.fail('parsed before checking all byte pins'))
    with pytest.raises(ValueError):
        session._situation_validate_output(result, source)


@pytest.mark.parametrize('name', ['snapshot', 'fleet', 'pointer', 'manifest'])
def test_situation_v2_input_duplicate_keys_refused_with_correct_hash(monkeypatch, name):
    result, source = _validator_input_case()
    raw = source[name + '_bytes']
    before, after = {
        'snapshot': (b'"type":"status"', b'"type":"claim","type":"status"'),
        'fleet': (b'"lanes":', b'"lanes":[],"lanes":'),
        'pointer': (b'"schema_version":1', b'"schema_version":0,"schema_version":1'),
        'manifest': (b'"schema_version":1', b'"schema_version":0,"schema_version":1'),
    }[name]
    assert raw.count(before) == 1
    changed = raw.replace(before, after)
    assert json.loads(changed) == json.loads(raw)
    _bind_validator_input(result, source, name, changed)
    monkeypatch.setattr(bridge_compact_view, 'event_id', lambda *a: pytest.fail('rederived event before strict input parsing'))
    with pytest.raises(ValueError):
        session._situation_validate_output(result, source)


@pytest.mark.parametrize('token', [b'NaN', b'Infinity', b'-Infinity'])
@pytest.mark.parametrize('name', ['snapshot', 'fleet', 'pointer', 'manifest'])
def test_situation_v2_input_nonfinite_constants_refused_before_events(monkeypatch, name, token):
    result, source = _validator_input_case()
    raw = source[name + '_bytes']
    marker = b'"event":{' if name == 'snapshot' else b'{'
    changed = raw.replace(marker, marker + b'"invented_unused":' + token + b',', 1)
    _bind_validator_input(result, source, name, changed)
    monkeypatch.setattr(bridge_compact_view, 'event_id', lambda *a: pytest.fail('rederived event before strict input parsing'))
    with pytest.raises(ValueError):
        session._situation_validate_output(result, source)


@pytest.mark.parametrize('raw', [b'{"invented":', b'{"invented":"\xff"}'])
def test_situation_v2_input_manifest_syntax_checked_before_events(monkeypatch, raw):
    result, source = _validator_input_case()
    _bind_validator_input(result, source, 'manifest', raw)
    monkeypatch.setattr(bridge_compact_view, 'event_id', lambda *a: pytest.fail('rederived event before manifest parsing'))
    with pytest.raises(ValueError):
        session._situation_validate_output(result, source)


@pytest.mark.parametrize('name,limit', [('snapshot', 8 * 1024 * 1024), ('manifest', 8 * 1024 * 1024),
                                      ('fleet', 1024 * 1024), ('pointer', 1024 * 1024)])
def test_situation_v2_input_direct_byte_caps_checked_before_parse(monkeypatch, name, limit):
    result, source = _validator_input_case()
    _bind_validator_input(result, source, name, source[name + '_bytes'].ljust(limit + 1, b' '))
    monkeypatch.setattr(session.json, 'loads', lambda *a, **k: pytest.fail('parsed oversized bytes'))
    with pytest.raises(ValueError):
        session._situation_validate_output(result, source)


@pytest.mark.parametrize('raw', ['[]', bytearray(b'[]'), memoryview(b'[]')])
def test_situation_v2_input_requires_exact_bytes_before_parse(monkeypatch, raw):
    result, source = _validator_input_case()
    source['snapshot_bytes'] = raw
    monkeypatch.setattr(session.json, 'loads', lambda *a, **k: pytest.fail('parsed non-bytes'))
    with pytest.raises(ValueError):
        session._situation_validate_output(result, source)


def test_situation_v2_input_fake_projector_duplicate_event_refused(tmp_path, capsys, monkeypatch):
    from tools import wd_mind_situation_view as projection
    args, _ = _progress_snapshot_cli_args(tmp_path)
    assert session.main(args + ['--task-events-v2']) == 0
    assert capsys.readouterr().err == ''
    path = Path(args[args.index('--snapshot') + 1])
    raw = path.read_bytes().replace(b'"type":"status"', b'"type":"claim","type":"status"')
    path.write_bytes(raw)
    args[args.index('--snapshot-sha256') + 1] = hashlib.sha256(raw).hexdigest()
    original = projection.project_situation
    calls = []
    def permissive_projector(**kwargs):
        calls.append(True)
        clean = json.dumps(json.loads(kwargs['snapshot_bytes']), sort_keys=True, separators=(',', ':')).encode()
        parsed_inputs = dict(kwargs, snapshot_bytes=clean, snapshot_sha256=hashlib.sha256(clean).hexdigest())
        result = original(**parsed_inputs)
        result['source']['snapshot_sha256'] = kwargs['snapshot_sha256']
        return result
    monkeypatch.setattr(projection, 'project_situation', permissive_projector)
    assert session.main(args + ['--task-events-v2']) == 2
    assert calls == [True]
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == '{"error":"mind_session_situation_refused","authority":"none"}\n'


@pytest.mark.parametrize('task_events', [False, True])
def test_situation_v2_input_bom_unicode_and_legacy_still_validate(task_events):
    from tools import wd_mind_situation_view as projection
    source, _ = _situation_inputs()
    def raw(value):
        return b'\xef\xbb\xbf' + json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    fleet = raw(json.loads(source['fleet_bytes']))
    manifest = json.loads(source['manifest_bytes'])
    manifest['files']['wd-fleet.json'] = hashlib.sha256(fleet).hexdigest()
    manifest = raw(manifest)
    pointer = json.loads(source['pointer_bytes'])
    pointer['manifest_sha256'] = pointer['final_manifest_sha256'] = hashlib.sha256(manifest).hexdigest()
    event = dict(json.loads(source['snapshot_bytes'])[0]['event'], message='invented ää雪 {} "quoted"')
    values = {'fleet': fleet, 'manifest': manifest, 'pointer': raw(pointer),
              'snapshot': raw([{'ref': bridge_compact_view.event_id(event), 'event': event}])}
    for name, data in values.items():
        source[name + '_bytes'] = data
        source[name + '_sha256'] = hashlib.sha256(data).hexdigest()
    source['include_task_events'] = task_events
    result = projection.project_situation(**source)
    session._situation_validate_output(result, source)
    assert result['schema'] == 'wd.mind-situation-observations.v' + ('2' if task_events else '1')
    assert ('latest_observed_task' in result['lanes']['codex-lead-1']) is task_events


@pytest.mark.parametrize('fault', ['tie_done', 'tie_release', 'invalid_newest', 'message_progress', 'newer_other_task'])
@pytest.mark.parametrize('reverse', [False, True])
def test_situation_v2_validator_rederives_full_candidate_set(fault, reverse):
    source, _ = _situation_inputs()
    claim = json.loads(source['snapshot_bytes'])[0]['event']
    progress = dict(claim, type='status', status='in_progress', task_id='invented-progress',
                    ts_utc='2026-11-14T05:29:30.0000001Z')
    single, _ = _v2_validator_case([progress])
    forged = single['lanes']['codex-lead-1']['latest_observed_task']
    if fault in ('tie_done', 'tie_release'):
        events = [progress, dict(progress, type=fault.removeprefix('tie_'))]
    elif fault == 'invalid_newest':
        events = [progress, dict(progress, ts_utc='2026-11-14T05:29:30.0000002Z', task_id=None)]
    elif fault == 'newer_other_task':
        events = [progress, dict(progress, ts_utc='2026-11-14T05:29:30.0000002Z', task_id='invented-other-task')]
    else:
        progress = dict(progress, type='message')
        events = [claim, progress]
        forged = dict(forged, event_ref=bridge_compact_view.event_id(progress), event_type='message')
    result, source = _v2_validator_case(list(reversed(events)) if reverse else events)
    result['lanes']['codex-lead-1']['latest_observed_task'] = forged
    with pytest.raises(ValueError):
        session._situation_validate_output(result, source)


@pytest.mark.parametrize('time,fresh', [('2026-11-14T05:29:59.0000001Z', True), ('2026-11-14T05:29:59Z', False)])
def test_situation_v2_validator_checks_one_tick_ttl(time, fresh):
    source, _ = _situation_inputs()
    event = dict(json.loads(source['snapshot_bytes'])[0]['event'], type='status', status='in_progress', ts_utc=time)
    result, source = _v2_validator_case([event], ttl_seconds=1)
    observed = result['lanes']['codex-lead-1']['latest_observed_task']
    assert (isinstance(observed, dict)) is fresh
    if fresh:
        assert observed['age_seconds_upper_bound'] == 1
    else:
        valid, _ = _v2_validator_case([event], ttl_seconds=2)
        result['lanes']['codex-lead-1']['latest_observed_task'] = valid['lanes']['codex-lead-1']['latest_observed_task']
        with pytest.raises(ValueError):
            session._situation_validate_output(result, source)


def test_situation_live_uses_canonical_reader_without_raw_leak_or_file_writes(tmp_path, capsys):
    args, log, event = _situation_live_cli_args(tmp_path)
    before = {str(path.relative_to(tmp_path)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in tmp_path.rglob("*") if path.is_file()}
    assert session.main(args) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and "synthetic-private-rco-text" not in captured.out
    envelope = json.loads(captured.out)
    assert envelope["schema"] == "wd.mind-situation-live.v1" and envelope["partial_final_record"] is False
    assert envelope["current_state"] == envelope["continuity"] == "UNKNOWN"
    view = envelope["view"]
    assert view["authority"] == "none" and view["source"]["sender_authentication"] == "UNKNOWN"
    assert view["lanes"]["codex-lead-1"]["latest_work_event"]["event_ref"] == bridge_compact_view.event_id(event)
    after = {str(path.relative_to(tmp_path)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after and log.exists()


def test_situation_live_runs_as_separate_synthetic_process(tmp_path):
    args, _, event = _situation_live_cli_args(tmp_path)
    result = subprocess.run([sys.executable, "-B", "-m", "tools.wd_bridge_mind_session", *args],
                            cwd=str(Path(session.__file__).resolve().parents[1]),
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0 and result.stderr == "" and "synthetic-private-rco-text" not in result.stdout
    envelope = json.loads(result.stdout)
    assert envelope["partial_final_record"] is False
    view = envelope["view"]
    assert view["lanes"]["codex-lead-1"]["latest_work_event"]["event_ref"] == bridge_compact_view.event_id(event)


@pytest.mark.parametrize("fault", ["missing_pin", "bad_manifest_pin", "duplicate_option", "missing_log"])
def test_situation_live_refuses_missing_or_unpinned_inputs(tmp_path, capsys, fault):
    args, log, _ = _situation_live_cli_args(tmp_path)
    if fault == "missing_pin":
        index = args.index("--pointer-sha256")
        del args[index:index + 2]
    if fault == "bad_manifest_pin": args[args.index("--manifest-sha256") + 1] = "0" * 64
    if fault == "duplicate_option": args += ["--ttl-seconds", "900"]
    if fault == "missing_log": log.unlink()
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "mind_session_situation_refused", "authority": "none"}


def test_situation_live_marks_partial_final_record_without_including_it(tmp_path, capsys):
    args, log, event = _situation_live_cli_args(tmp_path)
    with log.open("ab") as stream: stream.write(b'{"type":"synthetic-private-incomplete"')
    before = log.read_bytes()
    assert session.main(args) == 0
    captured = capsys.readouterr()
    assert captured.err == "" and "synthetic-private-incomplete" not in captured.out
    envelope = json.loads(captured.out)
    assert envelope["partial_final_record"] is True
    assert envelope["current_state"] == envelope["continuity"] == "UNKNOWN"
    assert envelope["view"]["lanes"]["codex-lead-1"]["latest_work_event"]["event_ref"] == bridge_compact_view.event_id(event)
    assert log.read_bytes() == before


def test_situation_live_only_partial_record_returns_unknown_state(tmp_path, capsys):
    args, log, _ = _situation_live_cli_args(tmp_path)
    log.write_bytes(b'{"type":"synthetic-private-incomplete"')
    assert session.main(args) == 0
    captured = capsys.readouterr()
    assert "synthetic-private-incomplete" not in captured.out
    envelope = json.loads(captured.out)
    assert envelope["partial_final_record"] is True and envelope["current_state"] == "UNKNOWN"
    assert envelope["view"]["lanes"]["codex-lead-1"]["latest_work_event"] == "UNKNOWN"


@pytest.mark.parametrize("fault", ["malformed", "reader_failure", "oversize"])
def test_situation_live_refuses_malformed_rotation_and_oversize(tmp_path, capsys, monkeypatch, fault):
    args, log, event = _situation_live_cli_args(tmp_path)
    if fault == "malformed":
        with log.open("ab") as stream: stream.write(b'{malformed}\n')
    if fault == "reader_failure":
        def fail(*unused): raise bridge_compact_view.CompactViewError("generation_changed_during_read")
        monkeypatch.setattr(session.bridge_compact_view, "_read_view_rows", fail)
    if fault == "oversize":
        event["message"] = "x" * (8 * 1024 * 1024)
        cursor = _situation_cursor(log)
        monkeypatch.setattr(session.bridge_compact_view, "_read_view_rows",
                            lambda *unused: ([event], cursor, {"unconsumed_bytes": 0}))
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "mind_session_situation_refused", "authority": "none"}


def test_situation_live_uses_last_5000_rows_and_reads_clock_after_snapshot(tmp_path, capsys, monkeypatch):
    args, log, event = _situation_live_cli_args(tmp_path)
    old = dict(event, task_id="lead-older-20261114")
    rows = [old] + [event] * 5000
    read_done = []
    cursor = _situation_cursor(log)
    def reader(*unused):
        read_done.append(True)
        return rows, cursor, {"unconsumed_bytes": 0}
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert read_done
            return datetime.now(tz)
    monkeypatch.setattr(session.bridge_compact_view, "_read_view_rows", reader)
    monkeypatch.setattr(session, "datetime", Clock)
    assert session.main(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["view"]["lanes"]["codex-lead-1"]["latest_work_event"]["event_ref"] == bridge_compact_view.event_id(event)


def test_situation_live_refuses_canonical_reader_rotation(tmp_path, capsys, monkeypatch):
    from waggledance.core import bridge_log_reader
    args, _, _ = _situation_live_cli_args(tmp_path)
    monkeypatch.setattr(bridge_log_reader, "read_bridge_log_tail_lines",
                        lambda *unused, **kwargs: bridge_log_reader.BridgeLineReadResult(
                            bridge_log_reader.BridgeReadStatus.RETRY, "file_identity_changed_during_read"))
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err)["error"] == "mind_session_situation_refused"


def test_situation_live_refuses_real_path_rotation_during_open_read(tmp_path, capsys, monkeypatch):
    from waggledance.core import bridge_log_reader
    args, log, event = _situation_live_cli_args(tmp_path)
    original_open = bridge_log_reader._open_log
    rotated = []
    def open_then_rotate(path):
        handle = original_open(path)
        if not rotated:
            rotated.append(True)
            log.rename(log.with_name("synthetic-prior-events.jsonl"))
            replacement = dict(event, task_id="lead-synthetic-replacement")
            log.write_bytes(json.dumps(replacement, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        return handle
    monkeypatch.setattr(bridge_log_reader, "_open_log", open_then_rotate)
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err)["error"] == "mind_session_situation_refused"


def test_situation_live_allows_same_file_append_during_read(tmp_path, capsys, monkeypatch):
    from waggledance.core import bridge_log_reader
    args, log, event = _situation_live_cli_args(tmp_path)
    original_identity = bridge_log_reader._file_identity
    calls = []
    def append_on_second_identity(handle):
        result = original_identity(handle)
        calls.append(True)
        if len(calls) == 2:
            appended = dict(event, task_id="lead-synthetic-appended")
            with log.open("ab") as stream:
                stream.write(json.dumps(appended, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        return result
    monkeypatch.setattr(bridge_log_reader, "_file_identity", append_on_second_identity)
    assert session.main(args) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["current_state"] == envelope["continuity"] == "UNKNOWN"


def test_situation_live_refuses_unavailable_post_read_identity(tmp_path, capsys, monkeypatch):
    from waggledance.core import bridge_log_reader
    args, _, _ = _situation_live_cli_args(tmp_path)
    original_identity = bridge_log_reader._file_identity
    calls = []
    def fail_third_identity(handle):
        calls.append(True)
        if len(calls) == 3: raise OSError("synthetic private path")
        return original_identity(handle)
    monkeypatch.setattr(bridge_log_reader, "_file_identity", fail_third_identity)
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "synthetic private path" not in captured.err
    assert json.loads(captured.err)["error"] == "mind_session_situation_refused"


def test_situation_live_refuses_malformed_cursor(tmp_path, capsys, monkeypatch):
    args, _, event = _situation_live_cli_args(tmp_path)
    monkeypatch.setattr(session.bridge_compact_view, "_read_view_rows",
                        lambda *unused: ([event], "bc1.invalid", {"unconsumed_bytes": 0}))
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err)["error"] == "mind_session_situation_refused"


def test_situation_live_real_bounded_tail_selects_newest_event(tmp_path, capsys):
    args, log, event = _situation_live_cli_args(tmp_path)
    with log.open("wb") as stream:
        for index in range(5002):
            row = dict(event, task_id=f"lead-synthetic-{index}")
            stream.write(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    assert session.main(args) == 0
    view = json.loads(capsys.readouterr().out)["view"]
    last = dict(event, task_id="lead-synthetic-5001")
    assert view["source"]["coverage"] == "bounded_tail"
    assert view["lanes"]["codex-lead-1"]["latest_work_event"]["event_ref"] == bridge_compact_view.event_id(last)


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


def test_situation_cli_refuses_older_event_as_latest(tmp_path, capsys, monkeypatch):
    from tools import wd_mind_situation_view as projection
    args, _ = _situation_cli_args(tmp_path)
    path = Path(args[args.index("--snapshot") + 1])
    records = json.loads(path.read_bytes())
    event = dict(records[0]["event"], type="done", ts_utc="2026-11-14T05:29:30Z")
    records.append({"ref": bridge_compact_view.event_id(event), "event": event})
    snapshot = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(snapshot)
    args[args.index("--snapshot-sha256") + 1] = hashlib.sha256(snapshot).hexdigest()
    original = projection.project_situation
    def wrong_latest(**kwargs):
        view = original(**kwargs)
        lane = view["lanes"]["codex-lead-1"]
        lane["latest_work_event"] = dict(lane["latest_claim_event"])
        return view
    monkeypatch.setattr(projection, "project_situation", wrong_latest)
    assert session.main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"] == "mind_session_situation_refused"


def test_situation_cli_stale_latest_keeps_real_reference(tmp_path, capsys):
    args, ref = _situation_cli_args(tmp_path)
    args[args.index("--as-of-utc") + 1] = "2026-11-14T06:00:00Z"
    assert session.main(args) == 0
    observation = json.loads(capsys.readouterr().out)["lanes"]["codex-lead-1"]["latest_work_event"]
    assert observation["event_ref"] == ref
    assert observation["status"] == observation["event_type"] == "UNKNOWN"


def test_situation_cli_equal_time_uses_last_snapshot_event(tmp_path, capsys):
    args, _ = _situation_cli_args(tmp_path)
    path = Path(args[args.index("--snapshot") + 1])
    records = json.loads(path.read_bytes())
    event = dict(records[0]["event"], type="done")
    ref = bridge_compact_view.event_id(event)
    records.append({"ref": ref, "event": event})
    snapshot = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(snapshot)
    args[args.index("--snapshot-sha256") + 1] = hashlib.sha256(snapshot).hexdigest()
    assert session.main(args) == 0
    observation = json.loads(capsys.readouterr().out)["lanes"]["codex-lead-1"]["latest_work_event"]
    assert observation["event_ref"] == ref and observation["event_type"] == "done"


def test_situation_cli_orders_seventh_fractional_digit(tmp_path, capsys):
    args, _ = _situation_cli_args(tmp_path)
    path = Path(args[args.index("--snapshot") + 1])
    records = json.loads(path.read_bytes())
    newer = dict(records[0]["event"], type="done", ts_utc="2026-11-14T05:29:00.0000002Z")
    older = dict(records[0]["event"], ts_utc="2026-11-14T05:29:00.0000001Z")
    newest_ref = bridge_compact_view.event_id(newer)
    records = [{"ref": newest_ref, "event": newer}, {"ref": bridge_compact_view.event_id(older), "event": older}]
    snapshot = json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(snapshot)
    args[args.index("--snapshot-sha256") + 1] = hashlib.sha256(snapshot).hexdigest()
    assert session.main(args) == 0
    observation = json.loads(capsys.readouterr().out)["lanes"]["codex-lead-1"]["latest_work_event"]
    assert observation["event_ref"] == newest_ref


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
