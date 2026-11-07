"""Synthetic RED contract for staged, nested native observation; not production."""
import hashlib
from pathlib import Path
import subprocess
import sys
import types
import pytest
from sandbox.mind.step2 import q4_controlled_imports as controlled
from sandbox.mind.step2 import q4_native_additions as additions


@pytest.fixture
def observed():
    def check(value):
        if not value:
            raise ValueError('synthetic refusal')
    tracker = controlled._NativeRegistrations(check, lambda: additions.validate_additions)
    name = 'wd_synthetic_native'
    module = types.ModuleType(name)
    module.original = object()
    registry = {name: module}
    tracker.bind({}, registry, set(), single_thread=True, known_objects=())
    return tracker, name, module, registry


def begin(observed):
    tracker, name, module, registry = observed
    return tracker.begin_segmented(name, module, registry, window_verified=True, single_thread=True)


def test_nested_native_segments_commit_only_after_outer_success(observed):
    tracker, name, module, registry = observed
    transaction = begin(observed)
    module.parent_add = object()
    transaction.checkpoint(registry, segment_kind='native', verified=True, single_thread=True)
    with pytest.raises(ValueError):
        tracker.accept(name, module, registry)
    module.child_add = object()
    transaction.checkpoint(registry, segment_kind='native', verified=True, single_thread=True)
    with pytest.raises(ValueError):
        tracker.accept(name, module, registry)
    transaction.finish(registry, outer_success=True, verified=True, single_thread=True)
    tracker.accept(name, module, registry)


@pytest.mark.parametrize('case', ('abort_after_segment', 'source_addition', 'replacement', 'deletion',
                                  'registry_swap', 'unverified', 'concurrent', 'out_of_band'))
def test_ambiguous_segment_never_creates_completion(observed, case):
    tracker, name, module, registry = observed
    transaction = begin(observed)
    if case == 'abort_after_segment':
        module.parent_add = object()
        transaction.checkpoint(registry, segment_kind='native', verified=True, single_thread=True)
        transaction.abort()
    else:
        if case == 'replacement': module.original = object()
        elif case == 'deletion': del module.original
        else: module.added = object()
        if case == 'registry_swap': registry[name] = types.ModuleType(name)
        kind = 'source' if case == 'source_addition' else 'native'
        verified = case != 'unverified'
        single = case != 'concurrent'
        if case == 'out_of_band': kind = 'unobserved'
        with pytest.raises(ValueError):
            transaction.checkpoint(registry, segment_kind=kind, verified=verified, single_thread=single)
    with pytest.raises(ValueError):
        tracker.accept(name, module, registry)


def test_batch_failure_never_commits_an_earlier_module(observed):
    tracker, name, module, registry = observed
    other = types.ModuleType('wd_other_native')
    other.original = object()
    tracker.bind(dict(registry), {**registry, 'wd_other_native':other}, set(),
                 single_thread=True, known_objects=())
    registry['wd_other_native'] = other
    batch = controlled._NativeBatch(tracker, registry)
    module.new = object()
    other.original = object()
    with pytest.raises(ValueError):
        batch.checkpoint(registry, 'native')
    for key, value in registry.items():
        with pytest.raises(ValueError):
            tracker.accept(key, value, registry)


def test_batch_interrupt_during_commit_invalidates_even_already_written_record(observed):
    tracker, name, module, registry = observed
    other = types.ModuleType('wd_other_native')
    other.original = object()
    tracker.bind(dict(registry), {**registry, 'wd_other_native':other}, set(),
                 single_thread=True, known_objects=())
    registry['wd_other_native'] = other
    class InterruptedRecords(dict):
        def __setitem__(self, key, value):
            if key == 'wd_other_native':
                raise KeyboardInterrupt()
            super().__setitem__(key, value)
    tracker._records = InterruptedRecords(tracker._records)
    batch = controlled._NativeBatch(tracker, registry)
    module.new = object()
    batch.checkpoint(registry, 'native')
    with pytest.raises(KeyboardInterrupt):
        batch.finish(registry)
    for key, value in registry.items():
        with pytest.raises(ValueError):
            tracker.accept(key, value, registry)


@pytest.mark.parametrize('scenario', ('success', 'outer_failure', 'inner_failure_caught',
                                      'source_addition', 'tampered_validator'))
def test_nested_native_loader_keeps_observations_staged_until_outer_return(tmp_path, scenario):
    root = Path(__file__).resolve().parents[2]
    probe = r'''
import hashlib,importlib.machinery as mach,importlib.util,json,os,pathlib,sys,types
repo, root, scenario = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
spec=importlib.util.spec_from_file_location('candidate',repo/'sandbox/mind/step2/q4_controlled_imports.py')
candidate=importlib.util.module_from_spec(spec); spec.loader.exec_module(candidate)
def check(value):
 if not value: raise ValueError('refused')
def sha(value): return hashlib.sha256(value).hexdigest()
def under(path, parent):
 try: return os.path.commonpath((path,parent)) == parent
 except ValueError: return False
site=root/'site'; site.mkdir(); helper=root/'assets/code/sandbox/mind/step2/q4_native_additions.py'
helper.parent.mkdir(parents=True); helper.write_bytes((repo/'sandbox/mind/step2/q4_native_additions.py').read_bytes())
files=[]
for path in (site/'wd_outer.pyd',site/'wd_inner.pyd'):
 path.write_bytes(b'synthetic, never loaded')
source=site/'wd_source.py'
source.write_bytes(b'import sys\nsys.modules["wd_synthetic_cython"].source_add = object()\n')
for path in (helper,site/'wd_outer.pyd',site/'wd_inner.pyd',source):
 raw=path.read_bytes(); files.append({'path':path.relative_to(root).as_posix(),'size':len(raw),'sha256':sha(raw)})
manifest=root/'manifest.json'; raw=json.dumps({'files':files}).encode(); manifest.write_bytes(raw)
phase=[0]; name='wd_synthetic_cython'
def fake_exec(loader, unused):
 if loader.name=='wd_outer' and phase[0]==0:
  module=types.ModuleType(name); module.original=object(); sys.modules[name]=module
 elif loader.name=='wd_outer':
  module=sys.modules[name]
  try: guard.accept_created(name,module)
  except ValueError: pass
  else: raise AssertionError('outer interval exposed early completion')
  module.parent_add=object()
  if scenario=='source_addition':
   source_spec=importlib.util.spec_from_file_location('wd_source',str(source))
   source_module=importlib.util.module_from_spec(source_spec); source_spec.loader.exec_module(source_module)
  else:
   try: mach.ExtensionFileLoader('wd_inner',str(site/'wd_inner.pyd')).exec_module(types.ModuleType('wd_inner'))
   except RuntimeError:
    if scenario!='inner_failure_caught': raise
  if scenario=='outer_failure': raise RuntimeError('synthetic outer failure')
 else:
  sys.modules[name].child_add=object()
  if scenario=='inner_failure_caught': raise RuntimeError('synthetic inner failure')
mach.ExtensionFileLoader.exec_module=fake_exec
guard=candidate.install(root,manifest,sha(raw),_check=check,_sha=sha,_unique_pairs=dict,
  _interpreter_roots=lambda:(os.path.normcase(os.path.realpath(sys.base_prefix)),),_under=under)
outer=mach.ExtensionFileLoader('wd_outer',str(site/'wd_outer.pyd'))
outer.exec_module(types.ModuleType('wd_outer')); guard.accept_created(name,sys.modules[name])
phase[0]=1
if scenario=='tampered_validator': helper.write_bytes(b'raise AssertionError("must not execute")\n')
try:
 outer.exec_module(types.ModuleType('wd_outer'))
except (ValueError,RuntimeError):
 if scenario=='success': raise
 try: guard.accept_created(name,sys.modules[name])
 except ValueError: pass
 else: raise AssertionError('failed native batch produced completion')
else:
 if scenario!='success': raise AssertionError('ambiguous native batch succeeded')
 guard.accept_created(name,sys.modules[name])
print('PASS')
'''
    completed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', probe, str(root), str(tmp_path), scenario],
                               capture_output=True, timeout=60)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', 'replace')
    assert completed.stdout in (b'PASS\n', b'PASS\r\n')
