"""Default-off, audit-only synthetic card generation and validated C2 pointer.

Inputs are independently hash-pinned inside one request.  This fixture tool is
not a publisher, a scheduler, an E5 encoder, or a production activation path.
It accepts only synthetic route decisions and files below a caller-provided
existing .codex-audit directory.  Prefix validity is consistency evidence,
not source authentication or a claim of completeness/freshness.  A failure
after generation starts is UNKNOWN: inspect the isolated output before retry.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}\Z')
_INPUTS = frozenset({'decision', 'vocabulary', 'path_allowlist', 'policy', 'roster',
                     'card_policy', 'source', 'generation'})
_IDENTITY = {'model': 'synthetic-sha-bow', 'revision': '0' * 40, 'weights_sha256': '0' * 64}
_DIM = 64


class Refused(ValueError):
    """Refused before generation is attempted."""


class Unknown(RuntimeError):
    """Generation or pointer write may have occurred; inspect before retry."""


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _json(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out: raise ValueError()
            out[key] = value
        return out
    return json.loads(raw.decode('utf-8', 'strict'), object_pairs_hook=unique,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))


def _plain(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError()


def _inside(root, value, *, directory=False):
    if type(value) is not str or not value or '\\' in value:
        raise ValueError()
    parts = value.split('/')
    if any(not part or part in ('.', '..') or ':' in part for part in parts):
        raise ValueError()
    path = root.joinpath(*parts)
    _plain(path)
    if not path.is_relative_to(root) or (not path.is_dir() if directory else not path.is_file()):
        raise ValueError()
    return path


def _pinned(path, digest, limit):
    if type(digest) is not str or not _HEX.fullmatch(digest): raise ValueError()
    before = path.stat()
    if before.st_size > limit: raise ValueError()
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
        if not os.path.samestat(before, os.fstat(stream.fileno())): raise ValueError()
    if len(raw) != before.st_size or _sha(raw) != digest: raise ValueError()
    return raw


def _encode(texts):
    import numpy as np
    vectors = []
    for text in texts:
        row = [0.0] * _DIM
        for token in re.findall(r'[a-z0-9]{2,}', text.lower()):
            row[int(hashlib.sha256(token.encode()).hexdigest(), 16) % _DIM] += 1.0
        norm = sum(value * value for value in row) ** 0.5 or 1.0
        vectors.append([value / norm for value in row])
    return np.asarray(vectors, dtype=np.float32)


def build(*, enabled=False, root=None, request_path=None, request_sha256=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True: raise ValueError()
        root = Path(root).absolute()
        if ('.codex-audit' not in root.parts or '..' in root.parts or not root.is_dir()
                or root.resolve(strict=True) != root): raise ValueError()
        _plain(root)
        request_file = _inside(root, request_path)
        request_raw = _pinned(request_file, request_sha256, 65536)
        request = _json(request_raw)
        if (type(request) is not dict or set(request) != {'schema', 'inputs', 'cursor', 'now_utc', 'output_dir', 'generation_name'}
                or request['schema'] != 'wd.q4-synthetic-card-snapshot-request.v1'
                or type(request['inputs']) is not dict or set(request['inputs']) != _INPUTS
                or type(request['generation_name']) is not str or not _NAME.fullmatch(request['generation_name'])
                or request['generation_name'] in ('.', '..')): raise ValueError()
        files, content = {}, {}
        for name, spec in request['inputs'].items():
            if type(spec) is not dict or set(spec) != {'path', 'sha256'}: raise ValueError()
            files[name] = _inside(root, spec['path'])
            content[name] = _pinned(files[name], spec['sha256'], 16 * 1024 * 1024 if name == 'source' else 1024 * 1024)
        decision = _json(content['decision'])
        if type(decision) is not dict or decision.get('synthetic') is not True: raise ValueError()
        out = _inside(root, request['output_dir'], directory=True)
        if any(out.iterdir()): raise ValueError()
        cursor = request['cursor']
        if (type(cursor) is not dict or set(cursor) != {'offset', 'file_identity', 'generation'}
                or type(cursor['offset']) is not int or cursor['offset'] <= 0
                or type(cursor['file_identity']) is not str or type(cursor['generation']) is not str): raise ValueError()
        from waggledance.core.bridge_log_reader import BridgeCursor
        from tools import wd_q4_card_generation as producer
        from sandbox.mind.step2 import shared_card_checkpoint as checkpoint
        end_cursor = BridgeCursor(cursor['offset'], cursor['file_identity'], cursor['generation'])
        generation = out / request['generation_name']
        destination = out / 'card-snapshot.pointer.json'
        if generation.exists() or destination.exists(): raise ValueError()
    except Exception:
        raise Refused('synthetic_snapshot_refused') from None
    try:
        built = producer.prepare_card_generation(enabled=True, decision_bytes=content['decision'],
            decision_sha256=request['inputs']['decision']['sha256'], vocabulary_bytes=content['vocabulary'],
            vocabulary_sha256=request['inputs']['vocabulary']['sha256'], path_allowlist_bytes=content['path_allowlist'],
            path_allowlist_sha256=request['inputs']['path_allowlist']['sha256'], now_utc=request['now_utc'],
            policy_bytes=content['policy'], policy_sha256=request['inputs']['policy']['sha256'],
            roster_bytes=content['roster'], roster_sha256=request['inputs']['roster']['sha256'],
            card_policy_bytes=content['card_policy'], source_path=str(files['source']),
            generation_path=str(files['generation']), end_cursor=end_cursor, output_root=out,
            generation_name=request['generation_name'], encode=_encode, identity=_IDENTITY, dimension=_DIM)
        if built.get('status') != 'STAGED': raise ValueError()
        artifacts = {}
        for path in generation.rglob('*'):
            _plain(path)
            if path.is_file():
                if len(artifacts) >= 2048: raise ValueError()
                artifacts[path.relative_to(generation).as_posix()] = _sha(path.read_bytes())
        pointer = {'schema': 'wd.q4-card-snapshot.v1', 'generation': request['generation_name'],
                   'artifacts': artifacts, 'decision_sha256': request['inputs']['decision']['sha256'],
                   'policy_sha256': request['inputs']['policy']['sha256'],
                   'roster_sha256': request['inputs']['roster']['sha256'], 'source_prefix': decision['source_prefix']}
        raw = json.dumps(pointer, sort_keys=True, separators=(',', ':')).encode('ascii')
        pin = _sha(raw)
        check = dict(enabled=True, pointer_bytes=raw, expected_pointer_sha256=pin, generation_dir=generation,
            decision_bytes=content['decision'], decision_sha256=request['inputs']['decision']['sha256'],
            policy_bytes=content['policy'], policy_sha256=request['inputs']['policy']['sha256'],
            roster_bytes=content['roster'], roster_sha256=request['inputs']['roster']['sha256'],
            card_policy_bytes=content['card_policy'], source_path=str(files['source']),
            generation_path=str(files['generation']), end_cursor=end_cursor)
        if checkpoint.validate_card_snapshot(**check) != {'status':'VALIDATED_STAGED','completeness':'UNKNOWN'}:
            raise ValueError()
        for name, spec in request['inputs'].items():
            if _pinned(files[name], spec['sha256'], 16 * 1024 * 1024 if name == 'source' else 1024 * 1024) != content[name]:
                raise ValueError()
        if _pinned(request_file, request_sha256, 65536) != request_raw: raise ValueError()
        with destination.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if destination.read_bytes() != raw or checkpoint.validate_card_snapshot(**check) != {'status':'VALIDATED_STAGED','completeness':'UNKNOWN'}:
            raise ValueError()
        return {'status':'VALIDATED_STAGED','completeness':'UNKNOWN','pointer_sha256':pin,
                'memory_sha256':built['memory_sha256'],'descriptor_sha256':built['descriptor_sha256']}
    except BaseException:
        raise Unknown('synthetic_snapshot_unknown') from None


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print('{"status":"OFF"}'); return 0
    if len(args) != 7 or args[0] != '--enabled' or args[1] != '--root' or args[3] != '--request' or args[5] != '--request-sha256':
        print('{"status":"REFUSED"}', file=sys.stderr); return 2
    try:
        print(json.dumps(build(enabled=True, root=args[2], request_path=args[4], request_sha256=args[6]), sort_keys=True, separators=(',', ':')))
        return 0
    except Refused:
        print('{"status":"REFUSED"}', file=sys.stderr); return 2
    except Unknown:
        print('{"status":"UNKNOWN"}', file=sys.stderr); return 3


if __name__ == '__main__':
    sys.exit(main())
