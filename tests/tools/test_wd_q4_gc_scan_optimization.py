"""Module snapshot equivalence on synthetic objects; no runtime or bridge data."""
import ast
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _snapshot_function(objects):
    source = (ROOT / 'sandbox/mind/step2/q4_controlled_imports.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    matches = [node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name == 'source_modules_in_gc']
    assert len(matches) == 1
    module = ast.Module(body=matches, type_ignores=[])
    ast.fix_missing_locations(module)
    calls = []
    def get_objects():
        calls.append(True)
        return objects
    namespace = {'gc': types.SimpleNamespace(get_objects=get_objects), 'types': types}
    exec(compile(module, '<synthetic-snapshot>', 'exec'), namespace)
    return namespace['source_modules_in_gc'], calls


def test_exact_snapshot_identity_order_and_no_dynamic_hooks():
    class HostileMeta(type):
        def __getattribute__(self, name):
            if name in ('__mro__', '__class__'):
                raise AssertionError('metaclass hook must not run')
            return super().__getattribute__(name)

    class HostileModule(types.ModuleType, metaclass=HostileMeta):
        def __getattribute__(self, name):
            if name == '__class__':
                raise AssertionError('instance hook must not run')
            return super().__getattribute__(name)

    class HostileObject(metaclass=HostileMeta):
        def __getattribute__(self, name):
            if name == '__class__':
                raise AssertionError('instance hook must not run')
            return super().__getattribute__(name)

    first, subclass = types.ModuleType('synthetic_a'), HostileModule('synthetic_b')
    values = [HostileObject(), first, object(), subclass, first, None]
    snapshot, calls = _snapshot_function(values)
    result = snapshot()
    assert len(result) == 3
    assert all(actual is expected for actual, expected in zip(result, (first, subclass, first)))
    assert calls == [True]


def test_each_call_takes_a_fresh_gc_snapshot():
    values = [types.ModuleType('synthetic_first')]
    snapshot, calls = _snapshot_function(values)
    first = snapshot()
    extra = types.ModuleType('synthetic_second')
    values.append(extra)
    second = snapshot()
    assert len(first) == 1 and len(second) == 2 and second[1] is extra
    assert calls == [True, True]
