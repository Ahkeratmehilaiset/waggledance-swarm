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


WINDOWS_POWERSHELL = 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'


@pytest.mark.skipif(not Path(WINDOWS_POWERSHELL).is_file(), reason='the reader runs under Windows PowerShell')
def test_reader_decodes_non_ascii_event_text_from_powershell(monkeypatch, tmp_path):
    # 2026-09-29: redirected PowerShell stdout used the OEM code page, and a Finnish 'ä'
    # in the window's events (byte 0x84) broke the UTF-8 decode before any report.
    module = load_metric()
    reader = tmp_path / 'Read-AgentBridge.ps1'
    reader.write_text("param([switch]$Raw,[switch]$NoAckReceived,[switch]$NoContinuity,[int]$Tail)\n"
                      "@(@{agent='operator';type='message';ts_utc='2026-09-29T18:00:00Z';"
                      "message=('k' + [char]0x00E4 + 'ytt' + [char]0x00F6)}) | ConvertTo-Json -Compress\n",
                      encoding='ascii')
    monkeypatch.setattr(module, 'verified_writer', lambda *_: reader)
    # Like the hidden scheduled task, the reader gets its own console with the default
    # code page. A shared test console keeps whatever code page an earlier child set.
    real_run = module.subprocess.run
    flags = getattr(module.subprocess, 'CREATE_NO_WINDOW', 0)
    monkeypatch.setattr(module.subprocess, 'run',
                        lambda *args, **kwargs: real_run(*args, creationflags=flags, **kwargs))
    rows = module.load_events(str(tmp_path), 'bundle', 'a' * 64)
    assert [row['message'] for row in rows] == ['käyttö']


def test_reader_command_forces_utf8_output_before_the_pinned_reader():
    module = load_metric()
    source = SOURCE.read_text(encoding='utf-8')
    assert "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); & '" in source


def recorded_commands(module, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='[]', stderr='')

    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setattr(module, 'verified_writer', lambda *_: Path('pinned/helper.ps1'))
    return calls


def test_reader_and_writer_run_windows_powershell_by_absolute_path(monkeypatch):
    # 2026-09-29 (Grok d534c2b3): a bare 'pwsh' resolves through PATH, so an earlier
    # PATH entry could stand in for the pinned reader or writer.
    module = load_metric()
    assert module.POWERSHELL == WINDOWS_POWERSHELL
    real_isfile = module.os.path.isfile
    monkeypatch.setattr(module.os.path, 'isfile', lambda path: path == WINDOWS_POWERSHELL or real_isfile(path))
    monkeypatch.setenv('PSModulePath', 'C:\\pwsh7\\Modules')
    calls = recorded_commands(module, monkeypatch)
    module.load_events('runtime', 'bundle', 'a' * 64)
    module.post_summary('summary', '20260929', 'runtime', 'bundle', 'a' * 64)
    assert [command[:6] for command, _ in calls] == [
        [WINDOWS_POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass']] * 2
    assert [command[6] for command, _ in calls] == ['-Command', '-File']
    for _, kwargs in calls:
        assert not any(key.upper() == 'PSMODULEPATH' for key in kwargs['env'])
    assert 'pwsh' not in SOURCE.read_text(encoding='utf-8').replace("bare 'pwsh'", '')


def test_a_missing_windows_powershell_fails_closed_before_running_anything(monkeypatch, tmp_path):
    module = load_metric()
    monkeypatch.setattr(module, 'POWERSHELL', str(tmp_path / 'missing' / 'powershell.exe'))
    calls = recorded_commands(module, monkeypatch)
    with pytest.raises(FileNotFoundError):
        module.load_events('runtime', 'bundle', 'a' * 64)
    with pytest.raises(FileNotFoundError):
        module.post_summary('summary', '20260929', 'runtime', 'bundle', 'a' * 64)
    assert calls == []
