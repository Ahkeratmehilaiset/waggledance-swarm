"""Default-off external generation adapter for one already pinned log prefix.

The generation is an audit-owned label, NOT a bridge-supplied generation or
proof of provenance.  The caller must independently pin the original identity,
LF boundary and prefix SHA, keep the private binding receipt, and carry all
four values into subsequent reads.  Appends may continue.  Identity reuse,
same-user races, origin authenticity, rotation history and later freshness are
not proven. Never modify the live source or place these files in its directory.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat


_HEX=re.compile(r'[0-9a-f]{64}\Z')


class Refused(ValueError):
    """No audit anchor write was attempted."""


class Unknown(RuntimeError):
    """Audit output may be partial; inspect pending before any retry."""


class Interrupted(BaseException):
    """Interrupted audit output remains unknown and must not be reused."""


def _plain(path):
    for part in (path,*path.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400: raise ValueError()


def _verify_source(bridge, path, cursor, pin):
    _plain(path)
    with bridge._open_log(path) as stream:
        if bridge._file_identity(stream)!=cursor.file_identity or os.fstat(stream.fileno()).st_size<cursor.offset:
            raise ValueError()
        if bridge._hash_prefix(stream,cursor.offset).hexdigest()!=pin: raise ValueError()
        stream.seek(cursor.offset-1)
        if stream.read(1)!=b'\n': raise ValueError()
        if bridge._file_identity(stream)!=cursor.file_identity or os.fstat(stream.fileno()).st_size<cursor.offset:
            raise ValueError()
        if bridge._hash_prefix(stream,cursor.offset).hexdigest()!=pin: raise ValueError()
    _plain(path)
    with bridge._open_log(path) as current:
        if (bridge._file_identity(current)!=cursor.file_identity or os.fstat(current.fileno()).st_size<cursor.offset
                or bridge._hash_prefix(current,cursor.offset).hexdigest()!=pin): raise ValueError()


def _write_new(path, raw):
    with path.open('xb') as stream:
        if stream.write(raw)!=len(raw): raise OSError()
        stream.flush();os.fsync(stream.fileno())


def verify(*, enabled=False, source_path=None, original_cursor=None, expected_prefix_sha256=None,
           output_root=None, expected_generation_sha256=None, expected_binding_sha256=None):
    """Revalidate independently pinned external evidence without writing."""
    if enabled is False: return {'status':'OFF'}
    try:
        if (enabled is not True or type(source_path) is not str or type(output_root) is not str
                or any(type(pin) is not str or not _HEX.fullmatch(pin) for pin in
                       (expected_prefix_sha256,expected_generation_sha256,expected_binding_sha256))): raise ValueError()
        source=Path(source_path);root=Path(output_root)
        if (not source.is_absolute() or not root.is_absolute() or '..' in source.parts or '..' in root.parts
                or '.codex-audit' not in root.parts or not root.is_dir() or not source.is_file()): raise ValueError()
        _plain(root);_plain(source)
        if (root.resolve(strict=True)!=root or source.resolve(strict=True)!=source
                or root.is_relative_to(source.parent) or source.parent.is_relative_to(root)
                or (root/'pending.json').exists()): raise ValueError()
        from waggledance.core import bridge_log_reader as bridge
        from tools import wd_q4_public_card_prefix_verifier as prefix
        if (type(original_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(original_cursor)
                or original_cursor.generation is not None or type(original_cursor.offset) is not int
                or not 0<original_cursor.offset<=prefix._MAX_SOURCE): raise ValueError()
        sidecar_path=root/'generation.json';binding_path=root/'binding.json'
        _plain(sidecar_path);_plain(binding_path)
        with sidecar_path.open('rb') as stream: sidecar=stream.read(513)
        with binding_path.open('rb') as stream: binding=stream.read(4097)
        if (len(sidecar)>512 or len(binding)>4096 or hashlib.sha256(sidecar).hexdigest()!=expected_generation_sha256
                or hashlib.sha256(binding).hexdigest()!=expected_binding_sha256): raise ValueError()
        document=json.loads(sidecar.decode('ascii'))
        generation=document.get('generation') if type(document) is dict else None
        if (set(document)!={'generation'} or type(generation) is not str
                or not re.fullmatch(r'external-audit-[0-9a-f]{32}',generation)
                or json.dumps(document,sort_keys=True,separators=(',',':')).encode('ascii')!=sidecar): raise ValueError()
        expected={'schema':'wd.q4-external-generation-binding.v1','generation':generation,
                  'file_identity':original_cursor.file_identity,'offset':original_cursor.offset,
                  'prefix_sha256':expected_prefix_sha256,'source_authentication':'UNKNOWN','freshness':'UNKNOWN'}
        if json.dumps(expected,sort_keys=True,separators=(',',':')).encode('ascii')!=binding: raise ValueError()
        _verify_source(bridge,source,original_cursor,expected_prefix_sha256)
        adapted=bridge.BridgeCursor(original_cursor.offset,original_cursor.file_identity,generation)
        prefix._read(str(source),str(sidecar_path),adapted,expected_prefix_sha256)
        _verify_source(bridge,source,original_cursor,expected_prefix_sha256)
        with sidecar_path.open('rb') as stream: sidecar_after=stream.read(513)
        with binding_path.open('rb') as stream: binding_after=stream.read(4097)
        if sidecar_after!=sidecar or binding_after!=binding or (root/'pending.json').exists(): raise ValueError()
        return {'status':'VERIFIED_WITHIN_PREFIX','generation':generation,'source_authentication':'UNKNOWN',
                'freshness':'UNKNOWN','generation_authority':'EXTERNAL_AUDIT_ONLY'}
    except Exception:
        raise Refused('external_generation_refused') from None


def prepare(*, enabled=False, source_path=None, original_cursor=None, expected_prefix_sha256=None, output_root=None):
    if enabled is False: return {'status':'OFF'}
    try:
        if enabled is not True or type(source_path) is not str or type(output_root) is not str: raise ValueError()
        source=Path(source_path);root=Path(output_root)
        if (not source.is_absolute() or not root.is_absolute() or '..' in source.parts or '..' in root.parts
                or '.codex-audit' not in root.parts or not root.is_dir() or not source.is_file()): raise ValueError()
        _plain(root);_plain(source)
        if root.resolve(strict=True)!=root or source.resolve(strict=True)!=source: raise ValueError()
        source_parent=source.parent
        if root.is_relative_to(source_parent) or source_parent.is_relative_to(root) or any(root.iterdir()):
            raise ValueError()
        from waggledance.core import bridge_log_reader as bridge
        from tools import wd_q4_public_card_prefix_verifier as prefix
        if (type(original_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(original_cursor)
                or original_cursor.generation is not None or type(original_cursor.offset) is not int
                or not 0<original_cursor.offset<=prefix._MAX_SOURCE or type(expected_prefix_sha256) is not str
                or not _HEX.fullmatch(expected_prefix_sha256)): raise ValueError()
        _verify_source(bridge,source,original_cursor,expected_prefix_sha256)
    except Exception:
        raise Refused('external_generation_refused') from None
    try:
        generation='external-audit-'+secrets.token_hex(16)
        sidecar=json.dumps({'generation':generation},sort_keys=True,separators=(',',':')).encode('ascii')
        binding=json.dumps({'schema':'wd.q4-external-generation-binding.v1','generation':generation,
            'file_identity':original_cursor.file_identity,'offset':original_cursor.offset,
            'prefix_sha256':expected_prefix_sha256,'source_authentication':'UNKNOWN',
            'freshness':'UNKNOWN'},sort_keys=True,separators=(',',':')).encode('ascii')
        pending=root/'pending.json';sidecar_path=root/'generation.json';binding_path=root/'binding.json'
        _write_new(pending,b'{"status":"UNKNOWN"}')
        _write_new(sidecar_path,sidecar)
        adapted=bridge.BridgeCursor(original_cursor.offset,original_cursor.file_identity,generation)
        prefix._read(str(source),str(sidecar_path),adapted,expected_prefix_sha256)
        _verify_source(bridge,source,original_cursor,expected_prefix_sha256)
        _write_new(binding_path,binding)
        if sidecar_path.read_bytes()!=sidecar or binding_path.read_bytes()!=binding: raise ValueError()
        prefix._read(str(source),str(sidecar_path),adapted,expected_prefix_sha256)
        _verify_source(bridge,source,original_cursor,expected_prefix_sha256)
        pending.unlink()
        return {'status':'PREPARED','generation':generation,'generation_sha256':hashlib.sha256(sidecar).hexdigest(),
                'binding_sha256':hashlib.sha256(binding).hexdigest(),'source_authentication':'UNKNOWN',
                'freshness':'UNKNOWN','generation_authority':'EXTERNAL_AUDIT_ONLY'}
    except (KeyboardInterrupt,SystemExit,GeneratorExit):
        raise Interrupted('external_generation_unknown') from None
    except Exception:
        raise Unknown('external_generation_unknown') from None
