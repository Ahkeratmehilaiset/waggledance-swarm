"""Reporting must never impersonate an RCO or claim a failed write succeeded."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[2] / 'tools' / 'wd_agent_value_metric.py'


def load_metric():
    spec = importlib.util.spec_from_file_location('metric_under_test', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke_post(module, monkeypatch, fail=False):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        if fail and kwargs.get('check'):
            raise module.subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=1 if fail else 0, stdout='{}', stderr='')
    monkeypatch.setattr(module.subprocess, 'run', run)
    if hasattr(module, 'post_summary'):
        monkeypatch.setattr(module, 'verified_writer', lambda *_: Path('pinned/Write-AgentEvent.ps1'))
        module.post_summary('summary', '20260924', 'runtime', 'bundle', 'a'*64)
    else:
        # Run only the old reporting branch, without GitHub access or model work.
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        branch = main.body[-1]
        scope = vars(module).copy()
        scope.update(args=SimpleNamespace(post_bridge=True, days=7, bridge_root='runtime'),
                     total_pr=0, prod=0, infra=0, grand_block=0, grand_adv=0,
                     interventions={'grok-scout-1': {'blocking': [], 'advisory': []}},
                     out_path='report', now=module.datetime.datetime(2026, 9, 24))
        monkeypatch.setattr(module.os.path, 'exists', lambda _: True)
        exec(compile(ast.Module(body=[branch], type_ignores=[]), str(SOURCE), 'exec'), scope)
    return calls


def test_report_has_neutral_identity(monkeypatch):
    module = load_metric()
    command, kwargs = invoke_post(module, monkeypatch)[0]
    assert command[command.index('-Agent')+1] == 'wd-agent-value'
    assert command[command.index('-Role')+1] == 'monitor'
    payload = module.json.loads(command[command.index('-PayloadJson')+1])
    assert payload['notification'] == 'informational'
    assert '-RequestId' not in command and '-ReplyToEventJson' not in command
    assert not any(k.startswith('AGENT_BRIDGE_') and k != 'AGENT_BRIDGE_RUNTIME_ROOT' for k in kwargs['env'])


def test_failed_write_is_not_success(monkeypatch):
    module = load_metric()
    with pytest.raises(module.subprocess.CalledProcessError):
        invoke_post(module, monkeypatch, fail=True)


def test_pinned_bundle_and_tampering(tmp_path):
    module = load_metric()
    relative = 'tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1'
    writer = tmp_path / relative
    writer.parent.mkdir(parents=True)
    writer.write_text('# test stub', encoding='utf-8')
    manifest = tmp_path / 'deployment-manifest.json'
    manifest.write_text(module.json.dumps({'files': {relative: module.hashlib.sha256(writer.read_bytes()).hexdigest()}}))
    pin = module.hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert module.verified_writer(tmp_path, pin) == writer
    with pytest.raises(ValueError, match='pin'):
        module.verified_writer(tmp_path, '0'*64)
    writer.write_text('# changed', encoding='utf-8')
    with pytest.raises(ValueError, match='file changed'):
        module.verified_writer(tmp_path, pin)


def test_reader_uses_pinned_no_ack_and_rejects_invalid_json(monkeypatch):
    module = load_metric()
    monkeypatch.setattr(module, 'verified_writer', lambda *args: Path('pinned/Read-AgentBridge.ps1'))
    def run(command, **kwargs):
        assert '-NoAckReceived' in command[-1]
        assert '-NoContinuity' in command[-1]
        assert kwargs['check'] is True
        return SimpleNamespace(stdout='not-json')
    monkeypatch.setattr(module.subprocess, 'run', run)
    with pytest.raises(ValueError):
        module.load_events('runtime', 'bundle', 'a'*64)


@pytest.mark.parametrize('output,count', [('', 0), ('[]', 0),
    ('{"agent":"wd-agent-value","type":"message","ts_utc":"2026-09-24T00:00:00Z"}', 1),
    ('[{"agent":"a","type":"message","ts_utc":"2026-09-24T00:00:00Z"},'
     '{"agent":"b","type":"message","ts_utc":"2026-09-24T00:00:01Z"}]', 2)])
def test_reader_accepts_powershell_pipeline_cardinality(monkeypatch, output, count):
    module = load_metric()
    monkeypatch.setattr(module, 'verified_writer', lambda *_: Path('pinned/Read-AgentBridge.ps1'))
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=output))
    assert len(module.load_events('runtime', 'bundle', 'a'*64)) == count


@pytest.mark.parametrize('output', ['null', '42', '{"error":"failed"}', '[42]',
    '[{}]', '[{"error":"failed"}]', '[{"agent":"a"}]',
    '[{"agent":"a","type":"message","ts_utc":"2026-09-24T00:00:00Z"},{}]'])
def test_reader_rejects_non_event_payload(monkeypatch, output):
    module = load_metric()
    monkeypatch.setattr(module, 'verified_writer', lambda *_: Path('pinned/Read-AgentBridge.ps1'))
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=output))
    with pytest.raises(ValueError):
        module.load_events('runtime', 'bundle', 'a'*64)
