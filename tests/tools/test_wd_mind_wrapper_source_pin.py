"""Exercise the wrapper's fixed source-byte bootstrap without subprocesses."""

import builtins
import hashlib
import io
import json
from pathlib import Path
import re
import runpy
import sys

import pytest


CONTEXT = Path(__file__).resolve().parents[2] / 'ops/windows/reboot/BridgeCodeContext.ps1'


def _bootstrap():
    source = CONTEXT.read_text(encoding='utf-8')
    match = re.search(r"\$script:WdMindPinnedSourceTrampoline\s*=\s*@'\r?\n(.*?)\r?\n'@", source, re.S)
    assert match, 'Mind fixed source bootstrap is absent'
    return match.group(1)


def _run_bootstrap(monkeypatch, path, pin, *arguments, manifest_raw=None, manifest_pin=None):
    if manifest_raw is None:
        manifest_raw = json.dumps({'schema_version':1, 'files':{
            'tools-bootstrap/tools/wd_mind_runtime_child.py':pin}}).encode()
    manifest_path = path.parent / 'synthetic-manifest.json'
    manifest_path.write_bytes(manifest_raw)
    anchor = manifest_pin if manifest_pin is not None else hashlib.sha256(manifest_raw).hexdigest()
    monkeypatch.setattr(sys, 'argv', ['-c', str(path), str(manifest_path), anchor, *arguments])
    original_flags = sys.flags

    class SafeFlags:
        safe_path = True
        def __getattr__(self, name):
            return getattr(original_flags, name)

    monkeypatch.setattr(sys, 'flags', SafeFlags())
    exec(compile(_bootstrap(), '<synthetic-wrapper-bootstrap>', 'exec'), {'__builtins__':builtins.__dict__})


def test_bootstrap_executes_only_manifest_pinned_bytes_and_preserves_argv(tmp_path, monkeypatch):
    target = tmp_path / 'synthetic.py'
    raw = (b'import sys\nsys._mind_wrapper_test_result = (list(sys.argv), __file__, __name__, '
           b'__package__, __spec__, __cached__, type(__loader__).__name__, list(sys.path))\n')
    target.write_bytes(raw)
    monkeypatch.setattr(sys, '_mind_wrapper_test_result', None, raising=False)
    expected_path = list(sys.path)
    _run_bootstrap(monkeypatch, target, hashlib.sha256(raw).hexdigest(), '--synthetic')
    assert sys._mind_wrapper_test_result == ([str(target), '--synthetic'], str(target), '__main__', None,
                                            None, None, 'SourceFileLoader', expected_path)


def test_bootstrap_rejects_changed_bytes_before_execution(tmp_path, monkeypatch):
    target = tmp_path / 'synthetic.py'
    target.write_bytes(b'import sys\nsys._mind_wrapper_test_result = "executed"\n')
    monkeypatch.setattr(sys, '_mind_wrapper_test_result', None, raising=False)
    with pytest.raises(SystemExit) as result:
        _run_bootstrap(monkeypatch, target, '0' * 64)
    assert result.value.code == 2 and sys._mind_wrapper_test_result is None


def test_bootstrap_executes_verified_snapshot_if_path_changes_after_read(tmp_path, monkeypatch):
    target = tmp_path / 'synthetic.py'
    original = b'import sys\nsys._mind_wrapper_test_result = "verified"\n'
    target.write_bytes(original)
    monkeypatch.setattr(sys, '_mind_wrapper_test_result', None, raising=False)
    real_open = builtins.open

    class ChangedAfterRead(io.BytesIO):
        def read(self, *args):
            result = super().read(*args)
            target.write_bytes(b'import sys\nsys._mind_wrapper_test_result = "changed"\n')
            return result

    def synthetic_open(path, *args, **kwargs):
        if str(path) == str(target):
            return ChangedAfterRead(original)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', synthetic_open)
    _run_bootstrap(monkeypatch, target, hashlib.sha256(original).hexdigest())
    assert sys._mind_wrapper_test_result == 'verified'


def test_wrapper_binds_mind_child_to_external_manifest_and_bootstrap():
    source = CONTEXT.read_text(encoding='utf-8')
    body = source.split('function Invoke-WdBridgePythonTool', 1)[1]
    assert "tools/wd_mind_runtime_child.py" in body
    assert '$script:WdMindPinnedSourceTrampoline' in body
    assert "'Mind child requires an external manifest anchor'" in body


def test_bootstrap_propagates_script_exit_status(tmp_path, monkeypatch):
    target = tmp_path / 'synthetic.py'
    raw = b'raise SystemExit(7)\n'
    target.write_bytes(raw)
    with pytest.raises(SystemExit) as result:
        _run_bootstrap(monkeypatch, target, hashlib.sha256(raw).hexdigest())
    assert result.value.code == 7


def test_bootstrap_and_runpy_preserve_script_argv_and_exit(tmp_path, monkeypatch):
    target = tmp_path / 'synthetic.py'
    raw = (b'import sys\nsys._mind_wrapper_test_result = (__name__, __file__, __package__, list(sys.argv))\n'
           b'raise SystemExit(9)\n')
    target.write_bytes(raw)
    monkeypatch.setattr(sys, '_mind_wrapper_test_result', None, raising=False)
    monkeypatch.setattr(sys, 'argv', [str(target), '--synthetic'])
    with pytest.raises(SystemExit) as baseline:
        runpy.run_path(str(target), run_name='__main__')
    runpy_result = sys._mind_wrapper_test_result
    with pytest.raises(SystemExit) as actual:
        _run_bootstrap(monkeypatch, target, hashlib.sha256(raw).hexdigest(), '--synthetic')
    current = sys._mind_wrapper_test_result
    assert baseline.value.code == actual.value.code == 9
    assert runpy_result[:2] == current[:2] and runpy_result[3] == current[3]
    # Direct Python script execution sets None; runpy.run_path sets ''.
    assert runpy_result[2] == '' and current[2] is None


def test_bootstrap_refuses_when_python_safe_path_is_disabled(tmp_path, monkeypatch):
    target = tmp_path / 'synthetic.py'
    raw = b'raise AssertionError("must not execute")\n'
    target.write_bytes(raw)
    monkeypatch.setattr(sys, 'argv', ['-c', str(target), str(tmp_path / 'unused'), 'a' * 64])
    original_flags = sys.flags
    class UnsafeFlags:
        safe_path = False
        def __getattr__(self, name):
            return getattr(original_flags, name)
    monkeypatch.setattr(sys, 'flags', UnsafeFlags())
    with pytest.raises(SystemExit) as result:
        exec(compile(_bootstrap(), '<synthetic-wrapper-bootstrap>', 'exec'), {'__builtins__':builtins.__dict__})
    assert result.value.code == 2


@pytest.mark.parametrize('raw', [
    b'{"schema_version":1,"files":{},"files":{}}',
    b'{"schema_version":true,"files":{}}',
    b'{"schema_version":1,"files":[]}',
    b'{"schema_version":1,"files":{"tools-bootstrap/tools/wd_mind_runtime_child.py":7}}',
])
def test_bootstrap_refuses_ambiguous_manifest_before_execution(tmp_path, monkeypatch, raw):
    target = tmp_path / 'synthetic.py'
    target.write_bytes(b'raise AssertionError("must not execute")\n')
    with pytest.raises(SystemExit) as result:
        _run_bootstrap(monkeypatch, target, 'a' * 64, manifest_raw=raw)
    assert result.value.code == 2


def test_bootstrap_refuses_incorrect_external_manifest_pin(tmp_path, monkeypatch):
    target = tmp_path / 'synthetic.py'
    target.write_bytes(b'raise AssertionError("must not execute")\n')
    with pytest.raises(SystemExit) as result:
        _run_bootstrap(monkeypatch, target, 'a' * 64, manifest_pin='0' * 64)
    assert result.value.code == 2
