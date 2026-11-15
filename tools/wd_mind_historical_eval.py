"""Default-off local Mind historical-evaluation harness.

Use isolated Python (-I -S -B). This script reads a pinned local test set and
returns aggregate scoring only; it never writes query, result, or source text.
The scorer and source/runtime bundle are independently pinned. Evaluation is
not installation evidence, model authentication, or a guarantee of usefulness.
"""
from __future__ import annotations

import argparse
import builtins
import contextlib
import hashlib
import hmac
import io
import importlib.machinery
import json
import math
import os
from pathlib import Path
import stat
import sys
import threading
import time
import types

SCORER_PATH = Path(r'C:\Python\waggledance-agent-worktrees\fable-5-hex-capability-probe-v1-20260730\.codex-audit\mind-usefulness-v1-20261114\mind_usefulness_score_v1.py')
SCORER_SHA256 = 'f6a86222a39498b5b5c661cc7f2ef3580c133a323d48c0ebfb8f0e27ec97c1e3'


class EvaluationRefused(ValueError):
    """Fixed refusal, without evaluation content or paths."""


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_pinned_file(path, digest):
    if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('digest')
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('path')
    current = path
    while True:
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('reparse')
        if current == path and not stat.S_ISREG(info.st_mode):
            raise ValueError('file')
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise ValueError('parent')
        if current == current.parent:
            break
        current = current.parent
    with path.open('rb') as stream:
        before = os.fstat(stream.fileno())
        raw = stream.read()
        after = os.fstat(stream.fileno())
        if (before.st_size != len(raw) or after.st_size != before.st_size
                or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                or not hmac.compare_digest(_sha(raw), digest)):
            raise ValueError('changed')
    return raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate')
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('constant')))


def _verify_preimport_bundle(root, manifest_sha256, *, retain_code=False):
    """Verify declared code before importing any package module."""
    root = Path(root)
    manifest = _json(_read_pinned_file(root / 'deployment-manifest.json', manifest_sha256))
    if type(manifest) is not dict or manifest.get('schema_version') != 1 or type(manifest.get('files')) is not dict:
        raise ValueError('manifest')
    files = manifest['files']
    definition = _json(_read_pinned_file(root / 'bridge-code-files.json', files['bridge-code-files.json'].lower()))
    paths = definition.get('python_files')
    if type(paths) is not list or not paths or len(paths) != len(set(paths)):
        raise ValueError('files')
    retained = {}
    for relative in paths:
        if (type(relative) is not str or '\\' in relative or ':' in relative
                or any(part in ('', '.', '..') for part in relative.split('/'))):
            raise ValueError('relative')
        packaged = 'tools-bootstrap/' + relative
        data = _read_pinned_file(root.joinpath(*packaged.split('/')), files[packaged].lower())
        if retain_code and relative.endswith('.py'):
            retained[relative] = data
    return (manifest, retained) if retain_code else manifest


class _RetainedBundleLoader:
    """Import reserved bundle namespaces only from checked immutable bytes."""
    def __init__(self, bundle, retained):
        self._modules, self._roots, self._loaded = {}, set(), []
        root = Path(bundle) / 'tools-bootstrap'
        for relative, data in retained.items():
            if type(relative) is not str or type(data) is not bytes or not relative.endswith('.py'):
                raise ValueError('retained')
            parts = relative[:-3].split('/')
            is_package = parts[-1] == '__init__'
            if is_package:
                parts.pop()
            if not parts or not all(part.isidentifier() for part in parts):
                continue
            name = '.'.join(parts)
            if name in self._modules:
                raise ValueError('module_collision')
            self._modules[name] = (data, str(root.joinpath(*relative.split('/'))), is_package)
            self._roots.add(parts[0])
        for name in tuple(self._modules):
            parts = name.split('.')
            for index in range(1, len(parts)):
                parent = '.'.join(parts[:index])
                if parent not in self._modules:
                    self._modules[parent] = (b'', str(root.joinpath(*parts[:index]) / '__init__.py'), True)
                elif not self._modules[parent][2]:
                    raise ValueError('parent_collision')
        if any(name == root_name or name.startswith(root_name + '.')
               for root_name in self._roots for name in sys.modules):
            raise ValueError('already_imported')

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.', 1)[0] not in self._roots:
            return None
        if fullname not in self._modules:
            raise ModuleNotFoundError('unpinned_bundle_module')
        data, filename, package = self._modules[fullname]
        return importlib.machinery.ModuleSpec(fullname, self, origin=filename, is_package=package)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        data, filename, package = self._modules[module.__name__]
        module.__file__ = filename
        if package:
            module.__path__ = []
        self._loaded.append(module.__name__)
        exec(compile(data, filename, 'exec', dont_inherit=True), module.__dict__)


@contextlib.contextmanager
def _retained_imports(bundle, retained):
    loader = _RetainedBundleLoader(bundle, retained)
    sys.meta_path.insert(0, loader)
    try:
        yield loader
    finally:
        if loader in sys.meta_path:
            sys.meta_path.remove(loader)
        for name in reversed(loader._loaded):
            sys.modules.pop(name, None)


class _MemoryOutput(io.StringIO):
    def close(self):
        pass  # scorer's context manager closes a memory view, not a file.


def _score_aggregate(testset, rows, scorer_bytes, expected_scorer_sha256, declared_authors):
    if type(scorer_bytes) is not bytes or not hmac.compare_digest(_sha(scorer_bytes), expected_scorer_sha256):
        raise ValueError('scorer_pin')
    out = _MemoryOutput()
    dataset = json.dumps(testset, ensure_ascii=False, allow_nan=False)
    lines = ''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in rows)
    def memory_open(path, mode='r', *args, **kwargs):
        if path == 'mem:testset' and mode == 'r': return io.StringIO(dataset)
        if path == 'mem:results' and mode == 'r': return io.StringIO(lines)
        if path == 'mem:out' and mode == 'w': return out
        if path == 'mem:declared_authors' and mode == 'rb': return io.BytesIO(b'')
        raise ValueError('scorer_io')
    isolated_builtins = dict(vars(builtins))
    isolated_builtins['open'] = memory_open
    scope = {'__name__':'wd_mind_pinned_scorer', '__builtins__':isolated_builtins}
    exec(compile(scorer_bytes, '<pinned-local-scorer>', 'exec'), scope)
    scope['read_log'] = lambda path, max_lines: (
        (index, ref, {'agent':author}) for index, (ref, author) in enumerate(declared_authors.items(), 1))
    args = types.SimpleNamespace(testset='mem:testset', results='mem:results', out='mem:out',
        preflight=None, indexed_refs=None, log='mem:declared_authors', k=3, scope_enforced=False)
    with contextlib.redirect_stdout(io.StringIO()):
        scope['cmd_score'](args)
    card = _json(out.getvalue().encode('utf-8'))
    if type(card) is not dict or card.get('schema') != 'wd.mind-usefulness-scorecard.v1':
        raise ValueError('score')
    return {key:card[key] for key in ('overall','by_variant','by_need')}


def _validate_testset(value):
    if type(value) is not dict or type(value.get('questions')) is not list or not value['questions']:
        raise ValueError('testset')
    seen = set()
    for question in value['questions']:
        if (type(question) is not dict or type(question.get('id')) is not str or not question['id']
                or question['id'] in seen):
            raise ValueError('question')
        seen.add(question['id'])
        for key in ('q_en','q_fi'):
            if type(question.get(key)) is not str or not question[key].strip() or len(question[key]) > 4000:
                raise ValueError('question')
    return value


def _retain_hold_forever():
    # A live process owns unresolved worker handles: do not kill or abandon it.
    while True:
        try: threading.Event().wait(60)
        except BaseException: pass


def run_evaluation(*, enabled=False, bundle_root=None, manifest_sha256=None, testset_path=None,
                   testset_sha256=None, self_sha256=None, fast_build=False):
    """Run locally with a bound owner; output aggregate metrics only."""
    if enabled is not True or type(fast_build) is not bool:
        raise EvaluationRefused('mind_historical_eval_refused') from None
    handler = None
    imports = None
    try:
        if (not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode
                or 'tools' in sys.modules or type(bundle_root) is not str):
            raise ValueError('isolation')
        _read_pinned_file(Path(__file__).absolute(), self_sha256)
        _, retained = _verify_preimport_bundle(bundle_root, manifest_sha256, retain_code=True)
        testset = _validate_testset(_json(_read_pinned_file(testset_path, testset_sha256)))
        scorer = _read_pinned_file(SCORER_PATH, SCORER_SHA256)
        imports = _retained_imports(bundle_root, retained)
        imports.__enter__()
        from tools import wd_mind_full_runtime as runtime
        from tools import wd_mind_e5_rotating_client as rotating
        if not runtime._owner_preflight(bundle_root=bundle_root, manifest_sha256=manifest_sha256):
            raise ValueError('preflight')
        location = runtime.resolve_manifest_runtime_location(enabled=True, bundle_root=bundle_root,
                                                              expected_manifest_sha256=manifest_sha256)
        if (not os.path.samefile(sys.executable, location['interpreter'])
                or _sha(Path(sys.executable).read_bytes()) != location['interpreter_sha256']):
            raise ValueError('interpreter')
        binding = runtime.assemble_full_runtime(enabled=True, bundle_root=bundle_root,
                                               expected_manifest_sha256=manifest_sha256)
        factory = runtime.create_manifest_worker_factory(enabled=True, bundle_root=bundle_root,
            expected_manifest_sha256=manifest_sha256, worker_timeout_seconds=120, lock_timeout_seconds=5,
            bulk_contract_sha256=rotating.BULK_CONTRACT_SHA256)
        handler = runtime.create_flat_request_handler(enabled=True, binding=binding, lane='fable-5',
            worker_factory=factory, shutdown_timeout=5, bulk_contract_sha256=rotating.BULK_CONTRACT_SHA256)
        build_started = time.monotonic()
        if fast_build:
            runtime.build_fast_fenced_owner(enabled=True, handler=handler)
        else:
            handler.build()
        cold_build_seconds = time.monotonic() - build_started
        rows = []
        for question in testset['questions']:
            for variant in ('en','fi'):
                started = time.monotonic()
                result = runtime.query_historical_flat_memory(enabled=True, handler=handler, lane='fable-5',
                    own_lane_only=False, query=question['q_' + variant])
                hits = [{'rank':index + 1, 'source_event_sha256':hit['source_reference']['sha256'],
                         'text':hit['display_text']} for index, hit in enumerate(result['results'])]
                rows.append({'question_id':question['id'], 'variant':variant, 'system':'mind-historical-local',
                    'status':'ANSWER' if hits else 'ABSTAIN', 'elapsed_seconds':time.monotonic()-started, 'hits':hits})
        records = runtime._flat_check(handler._memory)
        authors = {}
        for record in records.values():
            ref, author = record['provenance']['source_event_ref'], record['meta']['agent']
            if ref in authors and authors[ref] != author:
                raise ValueError('author_conflict')
            authors[ref] = author
        if any(hit['source_event_sha256'] not in authors for row in rows for hit in row['hits']):
            raise ValueError('author_unresolved')
        card = _score_aggregate(testset, rows, scorer, SCORER_SHA256, authors)
        result = {'schema':'wd.mind-local-eval-aggregate.v1', 'authority':'none', 'status':'MEASURED',
            'snapshot_freshness':'UNKNOWN', 'source_authentication':'UNKNOWN', 'sender_authentication':'UNKNOWN',
            'testset_cutoff_match':'UNKNOWN', 'build_profile': 'fast_fenced_v1' if fast_build else 'legacy_v1',
            'cold_build_seconds':cold_build_seconds,
            'first_query_seconds':rows[0]['elapsed_seconds'], 'indexed_record_count':len(records),
            'source_prefix_bytes':handler._snapshot['messages']['end_cursor'].offset, 'metrics':card}
    except BaseException:
        result = None
    finally:
        if handler is not None:
            try: closed = handler.close()
            except BaseException: closed = None
            if type(closed) is not dict or closed.get('state') != 'EXITED' or closed.get('owned') is not False:
                print('{"schema":"wd.mind-local-eval-aggregate.v1","status":"HOLD"}', flush=True)
                _retain_hold_forever()
        if imports is not None:
            imports.__exit__(None, None, None)
    if result is None:
        raise EvaluationRefused('mind_historical_eval_refused') from None
    return result


def main(argv=None):
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('--run-local-eval', action='store_true')
        parser.add_argument('--fast-build', action='store_true')
        parser.add_argument('--bundle-root', required=True)
        parser.add_argument('--deployment-manifest-sha256', required=True)
        parser.add_argument('--testset', required=True)
        parser.add_argument('--testset-sha256', required=True)
        parser.add_argument('--self-sha256', required=True)
        args = parser.parse_args(argv)
        card = run_evaluation(enabled=args.run_local_eval, bundle_root=args.bundle_root,
            manifest_sha256=args.deployment_manifest_sha256, testset_path=args.testset,
            testset_sha256=args.testset_sha256, self_sha256=args.self_sha256, fast_build=args.fast_build)
        print(json.dumps(card, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
        return 0
    except BaseException:
        print('{"schema":"wd.mind-local-eval-aggregate.v1","status":"REFUSED"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
