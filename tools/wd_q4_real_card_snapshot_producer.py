"""Opt-in real-format card generation and exclusive, verified C2 pointer.

The caller must independently pin and authorize all policy, decision, source,
Git and encoder inputs.  No input proves publisher or source authentication.
This function stages an immutable generation and a new pointer file; it does
not install, select, schedule, activate, or refresh anything.  Runtime code,
interpreter and native DLL origins must be established by the invoking host.
Same-user interference and filesystem check/use races remain outside this
cooperative binding.  On uncertainty after a write, never retry automatically.
"""
import hashlib
import json
import os
from pathlib import Path
import re


_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
_KEYS = frozenset({'decision_bytes', 'decision_sha256', 'vocabulary_bytes', 'vocabulary_sha256',
    'path_allowlist_bytes', 'path_allowlist_sha256', 'now_utc', 'policy_bytes', 'policy_sha256',
    'roster_bytes', 'roster_sha256', 'card_policy_bytes', 'source_path', 'generation_path', 'end_cursor',
    'output_root', 'generation_name', 'real_git_executable', 'real_git_repository', 'real_approved_anchors'})


class Refused(ValueError):
    """No generation or pointer write was attempted."""


class Unknown(RuntimeError):
    """An attempted write or interrupted operation requires reconciliation."""


def _sha(value): return hashlib.sha256(value).hexdigest()


def _load_pinned_e5(*, encoder_config, trusted_model_root, output_root, source_root):
    """Reuse the existing fixed-local E5 model asset and identity checks."""
    from sandbox.mind.step2 import wd_q4_prefix_build_child as child
    if (type(encoder_config) is not dict or set(encoder_config) != child.ENCODER_KEYS
            or encoder_config['model'] != child.ENCODER_MODEL or encoder_config['dim'] != child.ENCODER_DIM
            or type(encoder_config['dim']) is not int or type(encoder_config['revision']) is not str
            or not _HEX40.fullmatch(encoder_config['revision'])
            or type(encoder_config['weights_sha256']) is not str
            or not _HEX64.fullmatch(encoder_config['weights_sha256'])
            or not child._assets_shape(encoder_config['assets'])):
        return None
    files = child._model_root_bound(encoder_config['model_dir'], trusted_model_root, output_root, source_root)
    if files is None or not child._assets_bound(encoder_config['model_dir'], encoder_config['assets'], files):
        return None
    identity = child._identity(encoder_config)
    if identity is None: return None
    encode = child._encoder(encoder_config, identity)
    if encode is None: return None
    def recheck():
        current = child._model_root_bound(encoder_config['model_dir'], trusted_model_root, output_root, source_root)
        return (current is not None and set(current) == set(files)
                and child._assets_bound(encoder_config['model_dir'], encoder_config['assets'], current)
                and child._identity(encoder_config) == identity)
    return encode, identity, child.ENCODER_DIM, recheck


def produce(*, enabled=False, generation=None, encoder_config=None, trusted_model_root=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        from tools import wd_q4_public_card_prefix_verifier as verifier
        scope = verifier._snapshot_scan_scope()
    except Exception:
        raise Refused('real_card_snapshot_refused') from None
    entered = False
    try:
        with scope:
            entered = True
            return _produce(enabled=enabled, generation=generation, encoder_config=encoder_config,
                            trusted_model_root=trusted_model_root)
    except (Refused, Unknown):
        raise
    except Exception:
        if entered:
            raise Unknown('real_card_snapshot_unknown') from None
        raise Refused('real_card_snapshot_refused') from None
    except BaseException:
        if entered:
            raise Unknown('real_card_snapshot_unknown') from None
        raise


def _produce(*, enabled, generation, encoder_config, trusted_model_root):
    try:
        if enabled is not True or type(generation) is not dict or set(generation) != _KEYS: raise ValueError()
        args = dict(generation)
        decision_bytes = args['decision_bytes']
        if (type(decision_bytes) is not bytes or type(args['decision_sha256']) is not str
                or not _HEX64.fullmatch(args['decision_sha256'])
                or _sha(decision_bytes) != args['decision_sha256']): raise ValueError()
        decision = json.loads(decision_bytes.decode('utf-8', 'strict'))
        if (type(decision) is not dict or decision.get('synthetic') is not False
                or decision.get('decision') != 'enable_real_card_route'): raise ValueError()
        name = args['generation_name']
        if type(name) is not str or not _NAME.fullmatch(name) or name in ('.', '..'): raise ValueError()
        output = Path(args['output_root'])
        source = Path(args['source_path'])
        if not output.is_absolute() or not output.is_dir() or not source.is_absolute() or not source.is_file():
            raise ValueError()
        from sandbox.mind.step2 import shared_card_checkpoint as checkpoint
        from tools import wd_q4_card_generation as producer
        checkpoint._plain(output)
        generation_dir = output / name
        pointer_path = output / ('card-snapshot-' + name + '.json')
        if generation_dir.exists() or pointer_path.exists(): raise ValueError()
        model = _load_pinned_e5(encoder_config=encoder_config, trusted_model_root=trusted_model_root,
                                output_root=output, source_root=source.parent)
        if model is None: raise ValueError()
        encode, identity, dim, recheck = model
    except Exception:
        raise Refused('real_card_snapshot_refused') from None
    try:
        built = producer.prepare_card_generation(enabled=True, encode=encode, identity=identity, dimension=dim, **args)
    except producer.CardGenerationRefused:
        raise Refused('real_card_snapshot_refused') from None
    except BaseException:
        raise Unknown('real_card_snapshot_unknown') from None
    try:
        if (built.get('status') != 'STAGED' or type(built.get('rejected_invalid_expiry')) is not int
                or not 0 <= built['rejected_invalid_expiry'] <= 4096 or not recheck()): raise ValueError()
        artifacts = {}
        for path in generation_dir.rglob('*'):
            checkpoint._plain(path)
            if path.is_file():
                if len(artifacts) >= 2048: raise ValueError()
                artifacts[path.relative_to(generation_dir).as_posix()] = _sha(path.read_bytes())
        pointer = {'schema': 'wd.q4-card-snapshot.v1', 'generation': name, 'artifacts': artifacts,
                   'decision_sha256': args['decision_sha256'], 'policy_sha256': args['policy_sha256'],
                   'roster_sha256': args['roster_sha256'], 'source_prefix': decision['source_prefix']}
        raw = json.dumps(pointer, sort_keys=True, separators=(',', ':')).encode('ascii')
        pin = _sha(raw)
        checks = dict(enabled=True, pointer_bytes=raw, expected_pointer_sha256=pin, generation_dir=generation_dir,
            decision_bytes=decision_bytes, decision_sha256=args['decision_sha256'],
            policy_bytes=args['policy_bytes'], policy_sha256=args['policy_sha256'],
            roster_bytes=args['roster_bytes'], roster_sha256=args['roster_sha256'],
            card_policy_bytes=args['card_policy_bytes'], source_path=args['source_path'],
            generation_path=args['generation_path'], end_cursor=args['end_cursor'])
        expected = {'status':'VALIDATED_STAGED','completeness':'UNKNOWN'}
        if checkpoint.validate_card_snapshot(**checks) != expected: raise ValueError()
        with pointer_path.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if pointer_path.read_bytes() != raw or checkpoint.validate_card_snapshot(**checks) != expected:
            raise ValueError()
        return {'status':'VALIDATED_STAGED', 'pointer_sha256':pin, 'memory_sha256':built['memory_sha256'],
                'descriptor_sha256':built['descriptor_sha256'], 'completeness':'UNKNOWN',
                'source_authentication':'UNKNOWN', 'publisher_authentication':'UNKNOWN', 'freshness':'UNKNOWN',
                'rejected_invalid_expiry':built['rejected_invalid_expiry']}
    except BaseException:
        raise Unknown('real_card_snapshot_unknown') from None
