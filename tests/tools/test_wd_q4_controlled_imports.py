"""Isolated admission checks; never use an installed bundle or live data."""
import hashlib
import importlib._bootstrap_external
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path
import pytest


ROOT = Path(__file__).resolve().parents[2]


def _probe(tmp_path, scenario):
    site = tmp_path / 'site'
    site.mkdir()
    good = b'VALUE = 42\n'
    (site / 'wd_synthetic_good.py').write_bytes(good)
    if scenario in ('pyc', 'direct_pyc', 'loader_pyc'):
        # The isolated child ignores PYTHONPYCACHEPREFIX. Construct the forged
        # cache at the same test-owned location it would look at, regardless
        # of the parent test runner's cache configuration.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(sys, 'pycache_prefix', None)
            pyc = Path(importlib.util.cache_from_source(str(site / 'wd_synthetic_good.py')))
        pyc.parent.mkdir()
        forged = compile('VALUE = 666\n', str(site / 'wd_synthetic_good.py'), 'exec')
        pyc.write_bytes(importlib._bootstrap_external._code_to_hash_pyc(
            forged, importlib.util.source_hash(good), checked=False))
    (site / 'wd_synthetic_bad.py').write_text('raise AssertionError("must not execute")\n')
    files = [{'path': 'site/wd_synthetic_good.py', 'size': len(good),
              'sha256': hashlib.sha256(good).hexdigest()}]
    if scenario == 'mp_wrong_source':
        source = b'import sys\nsys.modules["__mp_main__"] = sys.modules["__main__"]\n'
        (site / 'multiprocessing.py').write_bytes(source)
        files.append({'path': 'site/multiprocessing.py', 'size': len(source),
                      'sha256': hashlib.sha256(source).hexdigest()})
    if scenario.startswith('subclass_'):
        producer = (b'import sys, types\n'
                    b'class VFModule(types.ModuleType):\n'
                    b' def __init__(self, name):\n'
                    b'  super().__init__(name)\n'
                    b'  self.vf = object()\n'
                    b' def __getattr__(self, name):\n'
                    b'  return getattr(self.vf, name)\n'
                    b'sys.modules[__name__] = VFModule(__name__)\n')
        (site / 'wd_synthetic_vf.py').write_bytes(producer)
        files.append({'path': 'site/wd_synthetic_vf.py', 'size': len(producer),
                      'sha256': hashlib.sha256(producer).hexdigest()})
    if scenario.startswith('inherited_'):
        sources = {
            'wd_synthetic_base.py': (b'import types\n'
                b'class PropModule(types.ModuleType):\n'
                b' def __init__(self, module, name):\n'
                b'  super().__init__(name)\n'
                b'  self.m = module\n'
                b' def __getattr__(self, name): return getattr(self.m, name)\n'),
            'wd_synthetic_wrapped.py': (b'import sys\nfrom contextlib import contextmanager\nfrom functools import lru_cache\n'
                b'from wd_synthetic_base import PropModule\n'
                b'class ContextProp:\n'
                b' def __init__(self, getter): self.getter = getter\n'
                b' def __get__(self, obj, objtype): return self.getter()\n'
                b'def get_value(): return 1\n'
                b'class Wrapped(PropModule):\n'
                b' def __init__(self, module, name): super().__init__(module, name)\n'
                b' @staticmethod\n'
                b' def work(): return 1\n'
                b' @property\n'
                b' def property_value(self): return 1\n'
                b' @contextmanager\n'
                b' def operation(self): yield 1\n'
                b' @lru_cache(maxsize=None)\n'
                b' def cached(self): return 1\n'
                b' option = ContextProp(get_value)\n'
                b'sys.modules[__name__] = Wrapped(sys.modules[__name__], __name__)\n')}
        if scenario == 'inherited_parent_failure':
            sources['wd_synthetic_base.py'] += b'import wd_synthetic_wrapped\nraise RuntimeError("synthetic failure")\n'
        if scenario == 'inherited_direct':
            sources['wd_synthetic_wrapped.py'] = sources['wd_synthetic_wrapped.py'].replace(
                b'from wd_synthetic_base import PropModule', b'import types\nPropModule = types.ModuleType').replace(
                b'super().__init__(module, name)', b'super().__init__(name); self.m = module')
        for filename, data in sources.items():
            (site / filename).write_bytes(data)
            files.append({'path':'site/' + filename, 'size':len(data), 'sha256':hashlib.sha256(data).hexdigest()})
    if scenario.startswith('sourcealias_'):
        factory = (b'import types\n'
                   b'class Namespace(types.ModuleType):\n'
                   b' def __init__(self):\n'
                   b'  super().__init__("wd_synthetic_root.ops")\n'
                   b'  self.marker = object()\n'
                   b' def __getattr__(self, name): raise AttributeError(name)\n')
        if scenario == 'sourcealias_nested':
            factory += b'import wd_synthetic_nested\nops = wd_synthetic_nested.ops\n'
            nested = b'from wd_synthetic_factory import Namespace\nops = Namespace()\n'
            (site / 'wd_synthetic_nested.py').write_bytes(nested)
            files.append({'path':'site/wd_synthetic_nested.py','size':len(nested),'sha256':hashlib.sha256(nested).hexdigest()})
        else:
            factory += b'ops = Namespace()\n'
        parent = (b'import sys\nfrom wd_synthetic_factory import ops\n'
                  b'sys.modules.setdefault(__name__ + ".ops", ops)\n')
        if scenario == 'sourcealias_source_addition':
            parent += b'ops.synthetic_namespace = object()\n'
        if scenario == 'sourcealias_source_replacement':
            parent += b'ops.marker = object()\n'
        for filename,data in (('wd_synthetic_factory.py',factory),('wd_synthetic_root.py',parent)):
            (site / filename).write_bytes(data)
            files.append({'path':'site/' + filename,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    if scenario.startswith('namespace_'):
        namespace = site / 'wd_synthetic_namespace'
        namespace.mkdir()
        child = b'VALUE = 1\n'
        (namespace / 'child.py').write_bytes(child)
        files.append({'path':'site/wd_synthetic_namespace/child.py','size':len(child),'sha256':hashlib.sha256(child).hexdigest()})
        for filename in ('q4_namespace_origin.py', 'runtime_manifest.py'):
            relative = 'assets/code/sandbox/mind/step2/' + filename
            data = (ROOT / 'sandbox/mind/step2' / filename).read_bytes()
            destination = tmp_path.joinpath(*relative.split('/'))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            if not ((scenario == 'namespace_missing_pin' and filename == 'q4_namespace_origin.py')
                    or (scenario == 'namespace_missing_verifier_pin' and filename == 'runtime_manifest.py')):
                files.append({'path':relative,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    if scenario.startswith('witness_'):
        producer = (b'import sys, types\nVALUE = "safe"\n'
                    b'def make_alias(target):\n'
                    b'    alias = types.ModuleType("wd_synthetic_alias")\n'
                    b'    alias.__file__ = None\n'
                    b'    def getter(name): return target + VALUE\n'
                    b'    alias.__getattr__ = getter\n'
                    b'    return alias\n'
                    b'sys.modules["wd_synthetic_alias"] = make_alias("fixture")\n')
        (site / 'wd_synthetic_producer.py').write_bytes(producer)
        files.append({'path': 'site/wd_synthetic_producer.py', 'size': len(producer),
                      'sha256': hashlib.sha256(producer).hexdigest()})
    helper_relative = 'assets/code/sandbox/mind/step2/q4_controlled_imports.py'
    helper = (ROOT / 'sandbox/mind/step2/q4_controlled_imports.py').read_bytes()
    helper_path = tmp_path.joinpath(*helper_relative.split('/'))
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    helper_path.write_bytes(helper)
    files.append({'path': helper_relative, 'size': len(helper), 'sha256': hashlib.sha256(helper).hexdigest()})
    if scenario in ('metadata', 'metadata_tamper', 'metadata_extra', 'metadata_toctou'):
        dist = site / 'wd_synthetic_demo-1.2.3.dist-info'
        dist.mkdir()
        metadata = b'Metadata-Version: 2.1\nName: wd-synthetic-demo\nVersion: 1.2.3\n\n'
        (dist / 'METADATA').write_bytes(metadata)
        files.append({'path': 'site/wd_synthetic_demo-1.2.3.dist-info/METADATA',
                      'size': len(metadata), 'sha256': hashlib.sha256(metadata).hexdigest()})
    if scenario in ('native', 'native_tamper'):
        native = (Path(sys.base_prefix) / 'DLLs' / 'winsound.pyd').read_bytes()
        (site / 'winsound.pyd').write_bytes(native if scenario == 'native' else native + b'X')
        files.append({'path': 'site/winsound.pyd', 'size': len(native),
                      'sha256': hashlib.sha256(native).hexdigest()})
    if scenario == 'pyc_only':
        pyc = importlib._bootstrap_external._code_to_hash_pyc(
            compile('VALUE = 42\n', '<synthetic>', 'exec'), importlib.util.source_hash(good), checked=False)
        (site / 'wd_synthetic_only.pyc').write_bytes(pyc)
        files.append({'path': 'site/wd_synthetic_only.pyc', 'size': len(pyc),
                      'sha256': hashlib.sha256(pyc).hexdigest()})
    manifest = json.dumps({'files': files}).encode()
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_bytes(manifest)
    if scenario == 'helper_tamper':
        helper_path.write_bytes(helper + b'\n')
    if scenario == 'metadata_tamper':
        (dist / 'METADATA').write_bytes(metadata.replace(b'1.2.3', b'9.9.9'))
    if scenario == 'metadata_extra':
        (dist / 'EXTRA').write_bytes(b'not in manifest')
    if scenario == 'tamper':
        (site / 'wd_synthetic_good.py').write_bytes(b'VALUE = 99\n')
    if scenario == 'namespace_tampered_helper':
        (tmp_path / 'assets/code/sandbox/mind/step2/q4_namespace_origin.py').write_bytes(b'raise AssertionError("must not execute")\n')
    if scenario == 'namespace_tampered_verifier':
        (tmp_path / 'assets/code/sandbox/mind/step2/runtime_manifest.py').write_bytes(b'raise AssertionError("must not execute")\n')
    outside = tmp_path.parent / '_outside_shared'
    outside.mkdir(exist_ok=True)
    (outside / 'wd_synthetic_external.py').write_text('raise AssertionError("must not execute")\n')
    (outside / 'wd_synthetic_ns').mkdir(exist_ok=True)
    probe = r'''
import hashlib, importlib, importlib.machinery, importlib.metadata, importlib.util, sys
from pathlib import Path
source, root, scenario = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
ns = {'__name__':'_wd_secure_child', '__file__':str(source)}
exec(compile(source.read_bytes(), '<secure>', 'exec'), ns)
manifest = root/'manifest.json'
sys.path.insert(0,str(root/'site'))
effects = []
class Evil:
 def find_spec(self, name, target=None): effects.append('executed'); return None
def evil_hook(path): effects.append('hook'); return Evil()
if scenario == 'preseed':
 sys.path_hooks.insert(0,evil_hook)
 sys.path_importer_cache[str(root/'site')] = Evil()
if scenario == 'mp_foreign_preseed':
 import types
 sys.modules['__mp_main__'] = types.ModuleType('foreign')
if scenario == 'mp_initial_same':
 sys.modules['__mp_main__'] = sys.modules['__main__']
pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
if scenario in ('badmanifest', 'helper_tamper', 'mp_foreign_preseed'):
 try: ns['_install_controlled_imports'](root, manifest, '0'*64 if scenario == 'badmanifest' else pin)
 except ns['_Refused']: print('PASS'); raise SystemExit(0)
 raise AssertionError('bad manifest pin accepted')
guard = ns['_install_controlled_imports'](root, manifest, pin)
if scenario.startswith('mp_'):
 import types
 if scenario == 'mp_initial_same':
  baseline = set(sys.modules)
  importlib.import_module('multiprocessing')
  assert sys.modules['__mp_main__'] is sys.modules['__main__']
  assert '__mp_main__' in baseline
  print('PASS'); raise SystemExit(0)
 assert '__mp_main__' not in sys.modules
 main = sys.modules['__main__']
 if scenario == 'mp_prior_source':
  sys.modules['__mp_main__'] = main
  try:
   importlib.import_module('multiprocessing')
   guard.accept_created('__mp_main__', main)
  except ns['_Refused']: pass
  else: raise AssertionError('alias registered before source was credited to source')
  print('PASS'); raise SystemExit(0)
 if scenario == 'mp_unwitnessed': sys.modules['__mp_main__'] = main
 else: importlib.import_module('multiprocessing')
 if scenario == 'mp_alias_swap': sys.modules['__mp_main__'] = types.ModuleType('foreign')
 if scenario == 'mp_main_swap': sys.modules['__main__'] = types.ModuleType('foreign')
 if scenario == 'mp_file_mutation': main.__file__ = 'synthetic-invalid'
 if scenario == 'mp_spec_mutation': main.__spec__ = object()
 if scenario in ('mp_clean', 'mp_origin'):
  guard.accept_created('__mp_main__', sys.modules['__mp_main__'])
  if scenario == 'mp_origin':
   roots = [ns['os'].path.normcase(ns['os'].path.realpath(root/'site'))] + ns['_interpreter_roots']()
   ns['_module_origins'](set(sys.modules)-{'__mp_main__'}, roots, str(root/'assets'/'code'), guard)
 else:
  try: guard.accept_created('__mp_main__', sys.modules['__mp_main__'])
  except ns['_Refused']: pass
  else: raise AssertionError('unbound main alias accepted')
elif scenario == 'preseed':
 assert importlib.import_module('wd_synthetic_good').VALUE == 42 and not effects
elif scenario in ('clean', 'pyc'):
 assert importlib.import_module('wd_synthetic_good').VALUE == 42
 assert importlib.util.find_spec('wd_synthetic_absent') is None
 try: importlib.import_module('wd_synthetic_absent')
 except ModuleNotFoundError: pass
 else: raise AssertionError('missing import accepted')
elif scenario == 'unmanifested':
 try: importlib.import_module('wd_synthetic_bad')
 except (ImportError, ns['_Refused']): pass
 else: raise AssertionError('unmanifested file accepted')
elif scenario in ('tamper', 'native_tamper', 'pyc_only'):
 target = {'tamper':'wd_synthetic_good','native_tamper':'winsound','pyc_only':'wd_synthetic_only'}[scenario]
 try: importlib.import_module(target)
 except (ImportError, ns['_Refused']): pass
 else: raise AssertionError('tampered or bytecode-only import accepted')
elif scenario == 'native':
 module = importlib.import_module('winsound')
 assert Path(module.__file__).parent == root/'site' and callable(module.Beep)
elif scenario == 'metadata':
 assert importlib.metadata.version('wd-synthetic-demo') == '1.2.3'
elif scenario == 'metadata_toctou':
 dist = importlib.metadata.distribution('wd-synthetic-demo')
 (root/'site'/'wd_synthetic_demo-1.2.3.dist-info'/'METADATA').write_text('Metadata-Version: 2.1\nName: wd-synthetic-demo\nVersion: 9.9.9\n\n')
 assert dist.version == '1.2.3'
elif scenario in ('metadata_tamper', 'metadata_extra'):
 try: importlib.metadata.version('wd-synthetic-demo')
 except (ImportError, ns['_Refused']): pass
 else: raise AssertionError('unverified distribution metadata accepted')
elif scenario in ('outside', 'namespace'):
 sys.path.insert(0,str(root.parent/'_outside_shared'))
 target = 'wd_synthetic_external' if scenario == 'outside' else 'wd_synthetic_ns'
 try: importlib.import_module(target)
 except (ImportError, ns['_Refused']): pass
 else: raise AssertionError('outside origin or namespace accepted')
elif scenario in ('direct_pyc', 'direct_unmanifested', 'loader_pyc', 'loader_unmanifested'):
 bad = scenario.endswith('unmanifested')
 path = root/'site'/('wd_synthetic_bad.py' if bad else 'wd_synthetic_good.py')
 name = 'wd_synthetic_direct'
 try:
  if scenario.startswith('direct_'):
   spec = importlib.util.spec_from_file_location(name, path)
  else:
   loader = importlib.machinery.SourceFileLoader(name, str(path))
   spec = importlib.util.spec_from_loader(name, loader)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  if bad: raise AssertionError('unmanifested direct loader accepted')
  assert module.VALUE == 42
 except (ImportError, ns['_Refused']):
  if not bad: raise
elif scenario in ('direct_custom_loader', 'direct_custom_from_loader'):
 class CustomLoader:
  def create_module(self, spec): return None
  def exec_module(self, module): effects.append('executed')
 try:
  if scenario == 'direct_custom_loader':
   spec = importlib.util.spec_from_file_location('wd_synthetic_direct', root/'site'/'wd_synthetic_good.py', loader=CustomLoader())
  else:
   spec = importlib.util.spec_from_loader('wd_synthetic_direct', CustomLoader())
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
 except (ImportError, ns['_Refused']): pass
 else: raise AssertionError('custom direct loader accepted')
 assert not effects
elif scenario == 'origin_no_side_effect':
 import types
 fake = types.ModuleType('wd_synthetic_untrusted')
 def side_effect(name): effects.append('executed'); return None
 fake.__getattr__ = side_effect
 sys.modules['wd_synthetic_untrusted'] = fake
 roots = [ns['os'].path.normcase(ns['os'].path.realpath(root/'site'))] + ns['_interpreter_roots']()
 try: ns['_module_origins'](set(sys.modules)-{'wd_synthetic_untrusted'}, roots, str(root/'assets'/'code'), guard)
 except ns['_Refused']: pass
 else: raise AssertionError('unwitnessed module accepted')
 assert not effects
elif scenario in ('torch_preseed', 'cython_preseed'):
 import types
 name = 'torch.synthetic' if scenario == 'torch_preseed' else '_cython_3_1_4'
 sys.modules[name] = types.ModuleType(name)
 try:
  try: ns['_reject_preloaded_runtime']()
  except ns['_Refused']: pass
  else: raise AssertionError('runtime preseed accepted')
 finally: del sys.modules[name]
elif scenario.startswith('namespace_'):
 sys.path.insert(0,str(root/'assets'/'code'))
 if scenario == 'namespace_preloaded_verifier':
  import types
  sys.modules['sandbox.mind.step2.runtime_manifest'] = types.ModuleType('sandbox.mind.step2.runtime_manifest')
 name = 'wd_synthetic_namespace'
 module = importlib.import_module(name)
 roots = [ns['os'].path.normcase(ns['os'].path.realpath(root/'site')),
          ns['os'].path.normcase(ns['os'].path.realpath(root/'assets'/'code'))] + ns['_interpreter_roots']()
 try: ns['_module_origins'](set(sys.modules)-{name}, roots, str(root/'assets'/'code'), guard)
 except (ns['_Refused'], ImportError):
  if scenario == 'namespace_clean': raise
 else:
  if scenario != 'namespace_clean': raise AssertionError('unverified namespace helper accepted')
elif scenario.startswith('sourcealias_'):
 import types
 name = 'wd_synthetic_root.ops'
 if scenario == 'sourcealias_preseed': sys.modules[name] = types.ModuleType(name)
 try: importlib.import_module('wd_synthetic_root')
 except ns['_Refused']:
  if scenario != 'sourcealias_source_replacement': raise
  print('PASS'); raise SystemExit(0)
 module = sys.modules[name]
 if scenario in ('sourcealias_preseed', 'sourcealias_nested', 'sourcealias_source_replacement'):
  try: guard.accept_created(name, module)
  except ns['_Refused']: pass
  else: raise AssertionError('unproven source alias accepted')
 else:
  guard.accept_created(name, module)
  if scenario == 'sourcealias_swap': sys.modules[name] = types.ModuleType(name)
  elif scenario == 'sourcealias_instance_mutation': module.marker = object()
  elif scenario == 'sourcealias_class_mutation': type(module).__getattr__ = lambda self, name: None
  if scenario not in ('sourcealias_clean', 'sourcealias_source_addition'):
   try: guard.accept_created(name, sys.modules[name])
   except ns['_Refused']: pass
   else: raise AssertionError('changed source alias accepted')
elif scenario.startswith('inherited_'):
 import types
 name = 'wd_synthetic_wrapped'
 if scenario == 'inherited_parent_failure':
  try: importlib.import_module('wd_synthetic_base')
  except RuntimeError: pass
  else: raise AssertionError('synthetic parent unexpectedly succeeded')
  try: guard.accept_created(name, sys.modules[name])
  except ns['_Refused']: pass
  else: raise AssertionError('witness from failed parent source accepted')
  print('PASS'); raise SystemExit(0)
 module = importlib.import_module(name)
 guard.accept_created(name, module)
 cls = type(module)
 if scenario == 'inherited_swap': sys.modules[name] = types.ModuleType(name)
 elif scenario == 'inherited_base_mutation': cls.__bases__[0].__getattr__ = lambda self, name: None
 elif scenario == 'inherited_base_global_swap': sys.modules['wd_synthetic_base'].PropModule = type('PropModule', (types.ModuleType,), {})
 elif scenario == 'inherited_original_swap': module.m = types.ModuleType(name)
 elif scenario == 'inherited_descriptor_mutation': vars(cls)['option'].getter = lambda: 2
 elif scenario == 'inherited_cached_mutation': vars(cls)['cached'].__wrapped__ = lambda: 2
 elif scenario == 'inherited_static_mutation': cls.work = staticmethod(lambda: 2)
 if scenario not in ('inherited_clean', 'inherited_direct'):
  try: guard.accept_created(name, sys.modules[name])
  except ns['_Refused']: pass
  else: raise AssertionError('mutated inherited source witness accepted')
elif scenario.startswith('subclass_'):
 import types
 name = 'wd_synthetic_vf'
 if scenario == 'subclass_preseed': sys.modules[name] = types.ModuleType(name)
 module = importlib.import_module(name)
 if scenario == 'subclass_preseed':
  try: guard.accept_created(name, module)
  except ns['_Refused']: pass
  else: raise AssertionError('preseeded module accepted')
 else:
  guard.accept_created(name, module)
  if scenario == 'subclass_swap': sys.modules[name] = types.ModuleType(name)
  elif scenario == 'subclass_instance_mutation': module.vf = object()
  elif scenario == 'subclass_class_mutation': type(module).__getattr__ = lambda self, name: None
  elif scenario == 'subclass_code_mutation': type(module).__getattr__.__code__ = type(module).__getattr__.__code__.replace()
  if scenario != 'subclass_clean':
   try: guard.accept_created(name, sys.modules[name])
   except ns['_Refused']: pass
   else: raise AssertionError('modified source subclass witness accepted')
elif scenario.startswith('witness_'):
 import types
 before = set(sys.modules)
 if scenario == 'witness_preseed':
  sys.modules['wd_synthetic_alias'] = types.ModuleType('wd_synthetic_alias')
 try:
  producer = importlib.import_module('wd_synthetic_producer')
 except ns['_Refused']:
  if scenario != 'witness_preseed': raise
 else:
  if scenario == 'witness_preseed': raise AssertionError('preseed accepted')
  alias = sys.modules['wd_synthetic_alias']
  guard.accept_created('wd_synthetic_alias', alias)
  if scenario == 'witness_swap':
   fake = types.ModuleType('wd_synthetic_alias')
   fake.__getattr__ = alias.__getattr__
   sys.modules['wd_synthetic_alias'] = fake
  elif scenario == 'witness_code':
   alias.__getattr__.__code__ = alias.__getattr__.__code__.replace()
  elif scenario == 'witness_globals':
   producer.VALUE = 'changed'
  elif scenario == 'witness_closure':
   alias.__getattr__.__closure__[0].cell_contents = 'changed'
  elif scenario == 'witness_direct_reload':
   spec = importlib.util.spec_from_file_location('wd_synthetic_producer', root/'site'/'wd_synthetic_producer.py')
   module = importlib.util.module_from_spec(spec)
   spec.loader.exec_module(module)
   alias = sys.modules['wd_synthetic_alias']
  if scenario in ('witness_swap','witness_code','witness_globals','witness_closure'):
   try: guard.accept_created('wd_synthetic_alias', sys.modules['wd_synthetic_alias'])
   except ns['_Refused']: pass
   else: raise AssertionError('changed creator evidence accepted')
  else:
   guard.accept_created('wd_synthetic_alias', alias)
   if scenario == 'witness_origin':
    roots = [ns['os'].path.normcase(ns['os'].path.realpath(root/'site'))] + ns['_interpreter_roots']()
    ns['_module_origins'](set(sys.modules)-{'wd_synthetic_alias'}, roots, str(root/'assets'/'code'), guard)
elif scenario == 'cache':
 sys.path_importer_cache[str(root/'site')] = Evil()
 try: importlib.import_module('wd_synthetic_good')
 except (ImportError, ns['_Refused']): pass
 else: raise AssertionError('cache injection accepted')
 assert not effects
elif scenario == 'hook':
 sys.path_hooks.insert(0, evil_hook)
 try: importlib.import_module('wd_synthetic_good')
 except (ImportError, ns['_Refused']): pass
 else: raise AssertionError('hook injection accepted')
 assert not effects
elif scenario == 'meta':
 try: sys.meta_path.append(Evil())
 except (AttributeError, TypeError): pass
 else: raise AssertionError('mutable meta chain')
 assert importlib.import_module('wd_synthetic_good').VALUE == 42
 assert not effects
print('PASS')
'''
    return subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe,
                           str(ROOT / 'tools' / 'wd_q4_secure_transport.py'), str(tmp_path), scenario],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)


def test_pyc_fixture_ignores_parent_cache_prefix(tmp_path, monkeypatch):
    unrelated_cache = tmp_path / 'unrelated-cache'
    monkeypatch.setattr(sys, 'pycache_prefix', str(unrelated_cache))
    result = _probe(tmp_path, 'direct_pyc')
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert not unrelated_cache.exists()
    assert any((tmp_path / 'site' / '__pycache__').glob('*.pyc'))
    assert sys.pycache_prefix == str(unrelated_cache)


def test_controlled_imports_preserve_missing_semantics_and_reject_injections(tmp_path):
    cases = ('clean', 'pyc', 'preseed', 'unmanifested', 'cache', 'hook', 'meta', 'metadata',
             'metadata_tamper', 'metadata_extra', 'metadata_toctou',
             'badmanifest', 'helper_tamper', 'tamper', 'pyc_only', 'outside', 'namespace', 'origin_no_side_effect',
             'torch_preseed', 'cython_preseed')
    if os.name == 'nt':
        cases += ('native', 'native_tamper')
    for scenario in cases:
        case = tmp_path / scenario
        case.mkdir()
        result = _probe(case, scenario)
        assert result.returncode == 0, (scenario, result.stderr.decode('utf-8', 'replace'))
        assert result.stdout == b'PASS\r\n' or result.stdout == b'PASS\n'


@pytest.mark.parametrize('scenario', ('direct_pyc', 'direct_unmanifested', 'loader_pyc', 'loader_unmanifested',
                                      'direct_custom_loader', 'direct_custom_from_loader'))
def test_direct_standard_loader_is_also_bounded(tmp_path, scenario):
    result = _probe(tmp_path, scenario)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert result.stdout in (b'PASS\r\n', b'PASS\n')


@pytest.mark.parametrize('scenario', ('witness_clean', 'witness_preseed', 'witness_swap', 'witness_code',
                                      'witness_globals', 'witness_closure', 'witness_direct_reload', 'witness_origin'))
def test_verified_source_creation_witness(tmp_path, scenario):
    result = _probe(tmp_path, scenario)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert result.stdout in (b'PASS\r\n', b'PASS\n')


@pytest.mark.parametrize('scenario', ('mp_clean', 'mp_origin', 'mp_unwitnessed', 'mp_prior_source', 'mp_initial_same', 'mp_wrong_source',
                                      'mp_foreign_preseed', 'mp_alias_swap', 'mp_main_swap',
                                      'mp_file_mutation', 'mp_spec_mutation'))
def test_main_alias_is_bound_to_bootstrap_and_verified_stdlib_source(tmp_path, scenario):
    result = _probe(tmp_path, scenario)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert result.stdout in (b'PASS\r\n', b'PASS\n')


@pytest.mark.parametrize('scenario', ('subclass_clean', 'subclass_preseed', 'subclass_swap',
                                      'subclass_instance_mutation', 'subclass_class_mutation', 'subclass_code_mutation'))
def test_verified_source_created_module_subclass_is_bound(tmp_path, scenario):
    result = _probe(tmp_path, scenario)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert result.stdout in (b'PASS\r\n', b'PASS\n')


@pytest.mark.parametrize('scenario', ('inherited_clean', 'inherited_direct', 'inherited_swap', 'inherited_base_mutation',
                                      'inherited_base_global_swap', 'inherited_parent_failure',
                                      'inherited_original_swap', 'inherited_descriptor_mutation', 'inherited_cached_mutation',
                                      'inherited_static_mutation'))
def test_verified_inherited_wrapper_and_descriptor_are_bound(tmp_path, scenario):
    result = _probe(tmp_path, scenario)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert result.stdout in (b'PASS\r\n', b'PASS\n')


@pytest.mark.parametrize('scenario', ('sourcealias_clean', 'sourcealias_source_addition', 'sourcealias_source_replacement',
                                      'sourcealias_preseed', 'sourcealias_nested',
                                      'sourcealias_swap', 'sourcealias_instance_mutation', 'sourcealias_class_mutation'))
def test_source_created_alias_is_bound_across_verified_source_calls(tmp_path, scenario):
    result = _probe(tmp_path, scenario)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert result.stdout in (b'PASS\r\n', b'PASS\n')


@pytest.mark.parametrize('scenario', ('namespace_clean', 'namespace_missing_pin', 'namespace_tampered_helper',
                                      'namespace_missing_verifier_pin', 'namespace_tampered_verifier',
                                      'namespace_preloaded_verifier'))
def test_namespace_consumer_loads_only_manifest_pinned_helper(tmp_path, scenario):
    result = _probe(tmp_path, scenario)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    assert result.stdout in (b'PASS\r\n', b'PASS\n')


@pytest.mark.parametrize('scenario', ('clean', 'preseed', 'borrowed', 'hidden_borrow', 'nested_source', 'nested_native',
                                      'concurrent', 'dict_mutation'))
def test_native_registration_is_scoped_and_does_not_claim_creation(scenario):
    from sandbox.mind.step2 import q4_controlled_imports as controlled

    def check(condition):
        if not condition:
            raise ValueError('synthetic refusal')

    tracker = controlled._NativeRegistrations(check)
    alias = types.ModuleType('wd_synthetic_native')
    alias.__file__ = None
    before, after, tainted = {}, {'wd_synthetic_native': alias}, set()
    known_objects = ()
    if scenario == 'preseed':
        before['wd_synthetic_native'] = types.ModuleType('wd_synthetic_native')
    elif scenario == 'borrowed':
        before['wd_existing'] = alias
        after['wd_existing'] = alias
    elif scenario == 'hidden_borrow':
        known_objects = (alias,)
    elif scenario in ('nested_source', 'nested_native'):
        tainted.add('wd_synthetic_native')
    if scenario == 'concurrent':
        with pytest.raises(ValueError):
            tracker.bind(before, after, tainted, single_thread=False, known_objects=known_objects)
        return
    tracker.bind(before, after, tainted, single_thread=True, known_objects=known_objects)
    if scenario == 'dict_mutation':
        alias.synthetic_change = 1
    if scenario in ('preseed', 'borrowed', 'hidden_borrow', 'nested_source', 'nested_native', 'dict_mutation'):
        with pytest.raises(ValueError):
            tracker.accept('wd_synthetic_native', alias, after)
    else:
        tracker.accept('wd_synthetic_native', alias, after)


@pytest.mark.parametrize('scenario', ('clean', 'preexisting', 'nested_source', 'nested_native',
                                      'registered_early', 'source_nested', 'source_preseed',
                                      'source_other_key', 'wrong_owner', 'substitution', 'native_mutation', 'source_mutation', 'concurrent'))
def test_native_object_later_registered_by_verified_source(scenario):
    from sandbox.mind.step2 import q4_controlled_imports as controlled

    def check(condition):
        if not condition:
            raise ValueError('synthetic refusal')

    tracker = controlled._NativeRegistrations(check)
    name = 'wd_synthetic_native.deferred'
    alias = types.ModuleType(name)
    known = (alias,) if scenario == 'preexisting' else ()
    tainted_objects = {id(alias)} if scenario in ('nested_source', 'nested_native') else set()
    if scenario == 'concurrent':
        with pytest.raises(ValueError):
            tracker.bind_objects(known, (alias,), tainted_objects, owner='wd_synthetic_native', single_thread=False)
        return
    owner = 'wd_unrelated_native' if scenario == 'wrong_owner' else 'wd_synthetic_native'
    tracker.bind_objects(known, (alias,), tainted_objects, owner=owner, single_thread=True)
    if scenario == 'native_mutation':
        alias.changed = 1
    source_before = {name: alias} if scenario in ('registered_early', 'source_preseed') else {}
    if scenario == 'source_other_key':
        source_before['wd_synthetic_other'] = alias
    candidate = types.ModuleType(name) if scenario == 'substitution' else alias
    source_after = {name: candidate}
    if scenario == 'source_other_key':
        source_after.update(source_before)
    source_tainted = {name} if scenario == 'source_nested' else set()
    tracker.bind_source(source_before, source_after, source_tainted, single_thread=True)
    if scenario == 'source_mutation':
        alias.changed = 1
    if scenario == 'clean':
        tracker.accept(name, alias, source_after)
    else:
        with pytest.raises(ValueError):
            tracker.accept(name, candidate, source_after)


def test_nested_interpreter_native_object_is_not_credited_to_manifest_native(tmp_path):
    # Only synthetic callbacks execute: both .pyd paths are verified but never loaded.
    probe = r'''
import hashlib, importlib.machinery as mach, importlib.util as util, json, os, pathlib, sys, types
workspace, root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = util.spec_from_file_location('q4_audit_helper', workspace/'sandbox/mind/step2/q4_controlled_imports.py')
helper = util.module_from_spec(spec); spec.loader.exec_module(helper)
def check(value):
 if not value: raise ValueError('refused')
def sha(value): return hashlib.sha256(value).hexdigest()
def under(path, base):
 try: return os.path.commonpath((path, base)) == base
 except ValueError: return False
site = root/'site'; site.mkdir(); (root/'assets/code').mkdir(parents=True)
source = b'import sys\nsys.modules["wd_outer.nested_alias"] = sys.modules["__main__"].candidate\n'
native = b'synthetic native file; never loaded'
(site/'wd_producer.py').write_bytes(source); (site/'wd_outer.pyd').write_bytes(native)
files = [{'path':p, 'size':len(b), 'sha256':sha(b)} for p,b in [('site/wd_producer.py',source),('site/wd_outer.pyd',native)]]
manifest=root/'manifest.json'; raw=json.dumps({'files':files}).encode(); manifest.write_bytes(raw)
def fake_exec(loader, module):
 if loader.name == 'wd_outer':
  nested=mach.ExtensionFileLoader('trusted_nested', str(pathlib.Path(sys.base_prefix)/'DLLs'/'_socket.pyd'))
  nested.exec_module(types.ModuleType('trusted_nested'))
 else:
  sys.modules['__main__'].candidate=types.ModuleType('wd_outer.nested_alias')
mach.ExtensionFileLoader.exec_module=fake_exec
sys.path.insert(0,str(site))
guard=helper.install(root,manifest,sha(raw),_check=check,_sha=sha,_unique_pairs=dict,
                    _interpreter_roots=lambda:(os.path.normcase(os.path.realpath(sys.base_prefix)),),_under=under)
mach.ExtensionFileLoader('wd_outer',str(site/'wd_outer.pyd')).exec_module(types.ModuleType('wd_outer'))
import wd_producer
try: guard.accept_created('wd_outer.nested_alias',candidate)
except ValueError: print('PASS')
else: raise AssertionError('nested interpreter object wrongly attributed to outer manifest native')
'''
    completed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe, str(ROOT), str(tmp_path)],
                               capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', 'replace')
    assert completed.stdout in (b'PASS\n', b'PASS\r\n')
