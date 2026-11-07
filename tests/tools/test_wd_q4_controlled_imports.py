"""Isolated admission checks; never use an installed bundle or live data."""
import hashlib
import importlib._bootstrap_external
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _probe(tmp_path, scenario):
    site = tmp_path / 'site'
    site.mkdir()
    good = b'VALUE = 42\n'
    (site / 'wd_synthetic_good.py').write_bytes(good)
    if scenario == 'pyc':
        pyc = Path(importlib.util.cache_from_source(str(site / 'wd_synthetic_good.py')))
        pyc.parent.mkdir()
        forged = compile('VALUE = 666\n', str(site / 'wd_synthetic_good.py'), 'exec')
        pyc.write_bytes(importlib._bootstrap_external._code_to_hash_pyc(
            forged, importlib.util.source_hash(good), checked=False))
    (site / 'wd_synthetic_bad.py').write_text('raise AssertionError("must not execute")\n')
    files = [{'path': 'site/wd_synthetic_good.py', 'size': len(good),
              'sha256': hashlib.sha256(good).hexdigest()}]
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
    if scenario == 'metadata_tamper':
        (dist / 'METADATA').write_bytes(metadata.replace(b'1.2.3', b'9.9.9'))
    if scenario == 'metadata_extra':
        (dist / 'EXTRA').write_bytes(b'not in manifest')
    if scenario == 'tamper':
        (site / 'wd_synthetic_good.py').write_bytes(b'VALUE = 99\n')
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
pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
if scenario == 'badmanifest':
 try: ns['_install_controlled_imports'](root, manifest, '0'*64)
 except ns['_Refused']: print('PASS'); raise SystemExit(0)
 raise AssertionError('bad manifest pin accepted')
ns['_install_controlled_imports'](root, manifest, pin)
if scenario == 'preseed':
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


def test_controlled_imports_preserve_missing_semantics_and_reject_injections(tmp_path):
    cases = ('clean', 'pyc', 'preseed', 'unmanifested', 'cache', 'hook', 'meta', 'metadata',
             'metadata_tamper', 'metadata_extra', 'metadata_toctou',
             'badmanifest', 'tamper', 'pyc_only', 'outside', 'namespace')
    if os.name == 'nt':
        cases += ('native', 'native_tamper')
    for scenario in cases:
        case = tmp_path / scenario
        case.mkdir()
        result = _probe(case, scenario)
        assert result.returncode == 0, (scenario, result.stderr.decode('utf-8', 'replace'))
        assert result.stdout == b'PASS\r\n' or result.stdout == b'PASS\n'
