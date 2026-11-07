"""Synthetic namespace packages only; no installed runtime or live data."""
import hashlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import types

import pytest

from sandbox.mind.step2 import q4_namespace_origin as verifier


@pytest.fixture
def sample(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    parent = root / 'site' / 'syntheticns'
    directory = parent / 'child'
    directory.mkdir(parents=True)
    data = b'synthetic = True\n'
    (directory / 'item.py').write_bytes(data)
    entries = {'site/syntheticns/child/item.py': (len(data), hashlib.sha256(data).hexdigest())}
    parent_spec = importlib.machinery.PathFinder.find_spec('syntheticns', [str(root / 'site')])
    parent_module = importlib.util.module_from_spec(parent_spec)
    monkeypatch.setitem(sys.modules, 'syntheticns', parent_module)
    spec = importlib.machinery.PathFinder.find_spec('syntheticns.child', parent_module.__path__)
    module = importlib.util.module_from_spec(spec)
    return root, directory, entries, module


def test_default_off_does_not_read_any_input():
    class Untouchable:
        def __getattribute__(self, name): raise AssertionError('OFF accessed input')
    assert verifier.verify_namespace(Untouchable(), Untouchable(), runtime_root=Untouchable(),
                                     entries=Untouchable()) is False


def test_real_pep420_namespace_under_pinned_runtime(sample):
    root, _, entries, module = sample
    assert verifier.verify_namespace('syntheticns.child', module, runtime_root=root, entries=entries,
                                     enabled=True) is True


@pytest.mark.parametrize('fault', ['wrong_name', 'module_subclass', 'different_loader', 'different_spec',
                                   'origin_present', 'module_file', 'different_path', 'additional_path',
                                   'missing_entry', 'outside_entry', 'bad_pin', 'regular_package'])
def test_invalid_namespace_is_not_accepted(sample, fault, tmp_path):
    root, directory, entries, module = sample
    name = 'syntheticns.child'
    if fault == 'wrong_name': name = 'syntheticns.other'
    if fault == 'module_subclass': module = type('FakeModule', (types.ModuleType,), {})(name)
    if fault == 'different_loader': module.__loader__ = object()
    if fault == 'different_spec': module.__spec__ = object()
    if fault == 'origin_present': module.__spec__.origin = str(directory / 'item.py')
    if fault == 'module_file': module.__file__ = str(directory / 'item.py')
    if fault == 'different_path': module.__path__ = [str(directory)]
    if fault == 'additional_path': module.__path__.append(str(tmp_path))
    if fault == 'missing_entry': entries = {}
    if fault == 'outside_entry': entries = {'site/syntheticns/childish/item.py': (1, '0' * 64)}
    if fault == 'bad_pin': entries = {'site/syntheticns/child/item.py': (1, 'not-a-sha')}
    if fault == 'regular_package': (directory / '__init__.py').write_text('', encoding='ascii')
    assert verifier.verify_namespace(name, module, runtime_root=root, entries=entries, enabled=True) is False


def test_assets_code_namespace_is_supported(sample, tmp_path, monkeypatch):
    root, _, _, _ = sample
    base = root / 'assets' / 'code'
    directory = base / 'anotherns' / 'leaf'
    directory.mkdir(parents=True)
    data = b'synthetic\n'
    (directory / 'unit.py').write_bytes(data)
    entries = {'assets/code/anotherns/leaf/unit.py': (len(data), hashlib.sha256(data).hexdigest())}
    parent_spec = importlib.machinery.PathFinder.find_spec('anotherns', [str(base)])
    parent = importlib.util.module_from_spec(parent_spec)
    monkeypatch.setitem(sys.modules, 'anotherns', parent)
    spec = importlib.machinery.PathFinder.find_spec('anotherns.leaf', parent.__path__)
    module = importlib.util.module_from_spec(spec)
    assert verifier.verify_namespace('anotherns.leaf', module, runtime_root=root, entries=entries, enabled=True) is True


def test_unsafe_path_chain_fails_closed(sample, monkeypatch):
    root, directory, entries, module = sample
    original = verifier.runtime_manifest._check_chain
    def refuse_directory(path):
        if Path(path) == directory:
            raise ValueError('synthetic reparse refusal')
        return original(path)
    monkeypatch.setattr(verifier.runtime_manifest, '_check_chain', refuse_directory)
    assert verifier.verify_namespace('syntheticns.child', module, runtime_root=root, entries=entries,
                                     enabled=True) is False
