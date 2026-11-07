"""Origin claims must agree with the trusted interpreter's import registry."""
import sys
import types
from importlib.machinery import BuiltinImporter, FrozenImporter, ModuleSpec

import pytest

from tools import wd_q4_secure_transport as transport


@pytest.mark.parametrize(('name', 'origin', 'loader'), (
    ('wd_synthetic_spoof', 'built-in', None),
    ('wd_synthetic_spoof', 'built-in', BuiltinImporter),
    ('wd_synthetic_spoof', 'frozen', None),
    ('wd_synthetic_spoof', 'frozen', FrozenImporter),
    ('sys', 'built-in', None),
    ('_frozen_importlib', 'frozen', None),
))
def test_originless_spoofed_spec_is_refused(monkeypatch, name, origin, loader):
    module = types.ModuleType(name)
    module.__loader__ = loader
    module.__spec__ = ModuleSpec(name, loader=loader, origin=origin)
    before = set(sys.modules) - {name}
    monkeypatch.setitem(sys.modules, name, module)
    with pytest.raises(transport._Refused):
        transport._module_origins(before, [])


@pytest.mark.parametrize('name', ('sys', '_frozen_importlib'))
def test_real_interpreter_module_still_passes(monkeypatch, name):
    assert name in sys.modules
    monkeypatch.delattr(sys.modules[name], '__file__', raising=False)
    transport._module_origins(set(sys.modules) - {name}, [])
