"""Isolated admission checks; never use an installed bundle or live data."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _probe(tmp_path, scenario):
    site = tmp_path / 'site'
    site.mkdir()
    good = b'VALUE = 42\n'
    (site / 'wd_synthetic_good.py').write_bytes(good)
    (site / 'wd_synthetic_bad.py').write_text('raise AssertionError("must not execute")\n')
    manifest = json.dumps({'files': [{'path': 'site/wd_synthetic_good.py', 'size': len(good),
                                     'sha256': hashlib.sha256(good).hexdigest()}]}).encode()
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_bytes(manifest)
    probe = r'''
import hashlib, importlib, importlib.machinery, importlib.util, sys
from pathlib import Path
source, root, scenario = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
ns = {'__name__':'_wd_secure_child', '__file__':str(source)}
exec(compile(source.read_bytes(), '<secure>', 'exec'), ns)
manifest = root/'manifest.json'
sys.path.append(str(root/'site'))
effects = []
class Evil:
 def find_spec(self, name, target=None): effects.append('executed'); return None
def evil_hook(path): effects.append('hook'); return Evil()
if scenario == 'preseed':
 sys.path_hooks.insert(0,evil_hook)
 sys.path_importer_cache[str(root/'site')] = Evil()
ns['_install_controlled_imports'](root, manifest, hashlib.sha256(manifest.read_bytes()).hexdigest())
if scenario == 'preseed':
 assert importlib.import_module('wd_synthetic_good').VALUE == 42 and not effects
elif scenario == 'clean':
 assert importlib.import_module('wd_synthetic_good').VALUE == 42
 assert importlib.util.find_spec('wd_synthetic_absent') is None
 try: importlib.import_module('wd_synthetic_absent')
 except ModuleNotFoundError: pass
 else: raise AssertionError('missing import accepted')
elif scenario == 'unmanifested':
 try: importlib.import_module('wd_synthetic_bad')
 except (ImportError, ns['_Refused']): pass
 else: raise AssertionError('unmanifested file accepted')
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
    for scenario in ('clean', 'preseed', 'unmanifested', 'cache', 'hook', 'meta'):
        case = tmp_path / scenario
        case.mkdir()
        result = _probe(case, scenario)
        assert result.returncode == 0, (scenario, result.stderr.decode('utf-8', 'replace'))
        assert result.stdout == b'PASS\r\n' or result.stdout == b'PASS\n'
