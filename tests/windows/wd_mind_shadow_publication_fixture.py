"""Opt-in A4 fixture: instrumented extracted publication primitive, NOT a producer run.

No production module is imported. The only extracted executable nodes are the
hash-verified atomic_write and _sharing_retry functions. atomic_write's B5
OutputBudget.charge resolves to a fixture-owned charge, never the real budget
class; the run requires the exact pointer size before the temporary exists.
A fixture-owned wrapper
around the real os.replace parks exactly before/after that call. Forced job
termination does not execute Python finally; before-stop temporary residue is
expected. This is file-level provenance, not OS isolation or runtime authority.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace


PINS = {
    'producer_sha256': ('sandbox/mind/mind_capture_producer.py', 'fe4bc86dc51860c02f1edf8e64ad18110ebbba6824bf2f8ecd6081e87791f8ff'),
    'core_sha256': ('tools/wd_bridge_mind.py', '83c1c43d4e6038711c7a7e90781693f27733d38ba0da51975a06af17599a8be1'),
}
# Only these two measured raw encodings of each immutable Git blob qualify.
# Unknown/mixed encodings are NOT normalized into acceptance. Actual raw hashes
# remain in the run binding; function segments use LF only after raw validation.
CANONICAL_PINS = {
    'producer_sha256': 'ff55b245f4aed82a44f2c19608d8e04894793139ac27a3d720773ecb9c359ce5',
    'core_sha256': '8555bd3fda512078ba61e3638af7f17ba3de55be11e126f6a7740ae140a67211',
}
OLD_POINTER = b'{"fixture":"A4","last_ok":"old.capture"}\n'
NEW_POINTER = b'{"fixture":"A4","last_ok":"new.capture"}\n'
CAPTURES = {'old.capture': b'synthetic old capture\n', 'new.capture': b'synthetic new capture\n'}
FUNCTION_PINS = {'atomic_write': 'bbb259db7fa10bf8391525a8443614989da32d646ed9cadc813af2a849a7c676',
                 '_sharing_retry': '91f92938912f6865c9f59f2900517caa8316d957f74bb09da201f4ce3e2159f1'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    if len(data) > 16384:
        raise ValueError('record_size')

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_key')
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite')))


def write_once(path, value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii') + b'\n'
    pending = path.with_name('.' + path.name + '.pending')
    with pending.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    # Atomic, no-overwrite publication; retain the sealed pending hard link.
    os.link(pending, path)


def check_binding(binding):
    if set(binding) != {'run_id', 'launch_id', 'phase', *PINS}:
        raise ValueError('binding_shape')
    for key in ('run_id', 'launch_id'):
        if type(binding[key]) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', binding[key]):
            raise ValueError('binding_identity')
    if binding['phase'] not in ('before_replace', 'after_replace'):
        raise ValueError('phase')
    for key, (_, expected) in PINS.items():
        if binding[key] not in (expected, CANONICAL_PINS[key]):
            raise ValueError('source_hash')


def unlimited_charge(size):
    # Fixture-owned stand-in for the producer's B5 OutputBudget.charge: its 256 MiB
    # default never refuses these synthetic sizes. A refusing charge is passed in.
    if type(size) is not int or size < 0:
        raise ValueError('charge_size')


def extracted_functions(code_root, binding, replace, sleep=time.sleep, charge=unlimited_charge):
    check_binding(binding)
    functions = {}
    for key, (relative, expected) in PINS.items():
        path = code_root / relative
        data = path.read_bytes()
        if path.is_symlink() or digest(data) != binding[key]:
            raise ValueError('source_hash')
        data = data.replace(b'\r\n', b'\n')
        if digest(data) != CANONICAL_PINS[key]:
            raise ValueError('canonical_source_hash')
        tree = ast.parse(data, filename=str(path))
        name = 'atomic_write' if key == 'producer_sha256' else '_sharing_retry'
        matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
        if len(matches) != 1 or matches[0].decorator_list:
            raise ValueError('extraction_shape')
        functions[name] = (matches[0], path, digest(ast.get_source_segment(data.decode('utf-8'), matches[0]).encode('utf-8')))
        if functions[name][2] != FUNCTION_PINS[name]:
            raise ValueError('function_hash')
    retry_node, retry_path, _ = functions['_sharing_retry']
    retry_globals = {'_SHARING_TRIES': 12, '_SHARING_FIRST_DELAY': 0.005,
                     '_SHARING_MAX_DELAY': 0.1, '_sleep': sleep}
    exec(compile(ast.Module(body=[retry_node], type_ignores=[]), str(retry_path), 'exec'), retry_globals)
    atomic_node, atomic_path, _ = functions['atomic_write']
    # A proxy changes only replace; no global os/module mutation or producer main.
    proxy = SimpleNamespace(fdopen=os.fdopen, fsync=os.fsync, unlink=os.unlink, replace=replace)
    namespace = {'os': proxy, 'tempfile': tempfile, 'OutputBudget': SimpleNamespace(charge=charge),
                 'core': SimpleNamespace(_sharing_retry=retry_globals['_sharing_retry'])}
    exec(compile(ast.Module(body=[atomic_node], type_ignores=[]), str(atomic_path), 'exec'), namespace)
    return namespace['atomic_write'], {name: value[2] for name, value in functions.items()}


def inspect_fixture(root, binding, expected_pid):
    check_binding(binding)
    ready = strict_json((root / 'ready.json').read_bytes())
    if set(ready) != {'record', 'binding', 'phase', 'pid', 'root', 'target', 'temporary', 'function_sha256'}:
        raise ValueError('ready_shape')
    if ready['record'] != 'at_publication_replace' or ready['binding'] != binding or ready['phase'] != binding['phase']:
        raise ValueError('foreign_ready')
    if type(ready['pid']) is not int or ready['pid'] != expected_pid or expected_pid <= 0:
        raise ValueError('foreign_pid')
    if ready['root'] != str(root.resolve()) or ready['target'] != str((root / 'latest.json').resolve()):
        raise ValueError('foreign_path')
    temporary = Path(ready['temporary'])
    if temporary.parent != root.resolve() or not re.fullmatch(r'\.latest\.json\.[A-Za-z0-9_-]+\.tmp', temporary.name):
        raise ValueError('foreign_temporary')
    hashes = ready['function_sha256']
    if type(hashes) is not dict or hashes != FUNCTION_PINS:
        raise ValueError('function_identity')
    expected = OLD_POINTER if binding['phase'] == 'before_replace' else NEW_POINTER
    if (root / 'latest.json').read_bytes() != expected:
        raise ValueError('pointer_incomplete')
    for name, data in CAPTURES.items():
        if (root / name).is_symlink() or (root / name).read_bytes() != data:
            raise ValueError('last_ok_changed')
    leftovers = []
    for path in root.iterdir():
        if path.name in {'latest.json', 'ready.json', '.ready.json.pending', *CAPTURES}:
            continue
        if path.is_symlink() or not path.is_file() or path != temporary or path.read_bytes() != NEW_POINTER:
            raise ValueError('unexpected_fixture_file')
        leftovers.append(path.name)
    if binding['phase'] == 'after_replace' and temporary.exists():
        raise ValueError('replace_not_observed')
    return {'pointer_complete': True, 'last_ok_intact': True, 'temporary_files': sorted(leftovers),
            'extraction': ['atomic_write', '_sharing_retry'], 'native_acceptance': 'NOT_CLAIMED'}


def run_fixture(root, code_root, binding, *, park=None, pid_provider=os.getpid):
    check_binding(binding)
    root = root.resolve(strict=True)
    if root.is_symlink() or list(root.iterdir()):
        raise ValueError('fixture_not_fresh')
    if park is None:
        def park():
            # Parent must stop the owned job; timeout is refusal, never success.
            time.sleep(120)
            raise TimeoutError('publication_barrier_parent_did_not_stop')
    functions = {}

    def intercepted(source, target):
        if Path(target) != root / 'latest.json':
            raise ValueError('unexpected_replace')
        if binding['phase'] == 'after_replace':
            os.replace(source, target)  # Actual successful replacement, not a declared phase.
        write_once(root / 'ready.json', {'record': 'at_publication_replace', 'binding': binding,
                   'phase': binding['phase'], 'pid': pid_provider(), 'root': str(root),
                   'target': str(target), 'temporary': str(source), 'function_sha256': functions})
        park()
        raise RuntimeError('publication_barrier_returned')

    def charged(size):
        # B5: atomic_write charges the exact new pointer bytes before its temporary exists.
        if size != len(NEW_POINTER) or any(path.name.endswith('.tmp') for path in root.iterdir()):
            raise ValueError('charge_order')

    atomic, functions = extracted_functions(code_root, binding, intercepted, charge=charged)
    for name, data in {**CAPTURES, 'latest.json': OLD_POINTER}.items():
        with (root / name).open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    atomic(root / 'latest.json', NEW_POINTER)
    raise RuntimeError('publication_barrier_missing')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--approved-native-fixture', action='store_true')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--code-root', type=Path, required=True)
    parser.add_argument('--binding-json', required=True)
    args = parser.parse_args()
    if not args.approved_native_fixture or '.codex-audit' not in args.root.resolve().parts:
        parser.error('explicit approved synthetic audit-only invocation required; opt-in grants no authority')
    run_fixture(args.root, args.code_root, strict_json(args.binding_json.encode('ascii')))


if __name__ == '__main__':
    main()
