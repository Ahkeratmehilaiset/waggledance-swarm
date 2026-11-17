"""Synthetic canonical-loader checks; never load a live bundle."""

import hashlib
import json
from pathlib import Path
import runpy
import sys

import pytest

from tools import wd_mind_runtime_child as child


def _fixture(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    package = bundle / 'tools-bootstrap' / 'tools'
    package.mkdir(parents=True)
    init = b'# synthetic package\n'
    source = b'def _fixed_child_main(argv):\n    return 37\n'
    (package / '__init__.py').write_bytes(init)
    script = package / 'wd_mind_runtime_child.py'
    script.write_bytes(source)
    files = {'tools-bootstrap/tools/__init__.py': hashlib.sha256(init).hexdigest(),
             'tools-bootstrap/tools/wd_mind_runtime_child.py': hashlib.sha256(source).hexdigest()}
    raw = json.dumps({'schema_version': 1, 'files': files}, sort_keys=True).encode()
    (bundle / 'deployment-manifest.json').write_bytes(raw)
    monkeypatch.setattr(child, '__file__', str(script))
    monkeypatch.delitem(sys.modules, 'tools.wd_mind_runtime_child', raising=False)
    monkeypatch.delitem(sys.modules, 'tools', raising=False)
    return bundle, hashlib.sha256(raw).hexdigest(), script


def test_canonical_loader_executes_manifest_pinned_bytes(tmp_path, monkeypatch):
    bundle, digest, _ = _fixture(tmp_path, monkeypatch)
    try:
        module = child._load_pinned_canonical_child(bundle, digest)
        assert module is sys.modules['tools.wd_mind_runtime_child']
        assert module._fixed_child_main([]) == 37
        assert sys.modules['tools'].wd_mind_runtime_child is module
    finally:
        sys.modules.pop('tools.wd_mind_runtime_child', None)
        sys.modules.pop('tools', None)


def test_actual_script_dispatches_through_pinned_canonical_module(tmp_path, monkeypatch):
    bundle, _, script = _fixture(tmp_path, monkeypatch)
    # The fixture replaces __file__; use the checked-out source, not live state.
    source = Path(__file__).resolve().parents[2] / 'tools/wd_mind_runtime_child.py'
    script.write_bytes(source.read_bytes())
    package = script.parent / '__init__.py'
    files = {'tools-bootstrap/tools/__init__.py': hashlib.sha256(package.read_bytes()).hexdigest(),
             'tools-bootstrap/tools/wd_mind_runtime_child.py': hashlib.sha256(script.read_bytes()).hexdigest()}
    raw = json.dumps({'schema_version': 1, 'files': files}, sort_keys=True).encode()
    (bundle / 'deployment-manifest.json').write_bytes(raw)
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(sys, 'argv', [str(script), '--parent-control'])
    try:
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(script), run_name='__main__')
        assert result.value.code == 2  # Existing parent gate refuses before side effects.
        assert sys.modules['tools'].wd_mind_runtime_child is sys.modules['tools.wd_mind_runtime_child']
        assert sys.modules['tools.wd_mind_runtime_child'].__file__ == str(script)
    finally:
        sys.modules.pop('tools.wd_mind_runtime_child', None)
        sys.modules.pop('tools', None)


@pytest.mark.parametrize('fault', ['manifest', 'source', 'package', 'module_collision'])
def test_canonical_loader_refuses_changed_inputs_before_execution(tmp_path, monkeypatch, fault):
    bundle, digest, script = _fixture(tmp_path, monkeypatch)
    if fault == 'manifest':
        digest = '0' * 64
    elif fault == 'source':
        script.write_bytes(b'raise AssertionError("must not execute")\n')
    elif fault == 'package':
        (script.parent / '__init__.py').write_bytes(b'raise AssertionError("must not execute")\n')
    else:
        monkeypatch.setitem(sys.modules, 'tools.wd_mind_runtime_child', object())
    with pytest.raises(child.LaunchRefused):
        child._load_pinned_canonical_child(bundle, digest)
