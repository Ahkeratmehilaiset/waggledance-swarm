"""Read-only preflight for an offline Q4 wheel set; not an installer.

No pip, subprocess, network access, site creation or activation occurs here.
Caller-supplied pins and free-space estimates are not trust anchors or a real
peak-space measurement. Wheel tags are only a conservative filename check;
package safety, wheel provenance, dependency closure, pip and interpreter
stdlib/DLL provenance and same-user races are not established by this module.
"""

import hashlib
import os
from pathlib import Path
import re
import stat

from sandbox.mind.step2 import runtime_builder, runtime_manifest


_SHA = re.compile(r'[0-9a-f]{64}\Z')
_LOCK = re.compile(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+]+)\s+--hash=sha256:([0-9a-f]{64})\Z')
_WHEEL_FIELD = re.compile(r'[A-Za-z0-9_.+]+\Z')
_WHEEL_DISTRIBUTION = re.compile(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*\Z')
# Conservative normalized version subset used by the pinned wheel set; this
# is deliberately not a complete PEP 440 implementation.
_WHEEL_VERSION = re.compile(r'[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?'
                            r'(?:\.post[0-9]+)?(?:\.dev[0-9]+)?'
                            r'(?:\+[A-Za-z0-9]+(?:[._][A-Za-z0-9]+)*)?\Z')
_WHEEL_BUILD = re.compile(r'[0-9][A-Za-z0-9_]*\Z')
_FLOOR = 3 * 1024**3


def _name(value):
    return re.sub(r'[-_.]+', '-', value).lower()


def _related(left, right):
    try:
        common = os.path.normcase(os.path.commonpath((str(left), str(right))))
    except ValueError:
        return False
    return common in (os.path.normcase(str(left)), os.path.normcase(str(right)))


def _directory(value):
    path = runtime_manifest._check_chain(value)
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        raise ValueError('directory_invalid')
    return path


def _wheel_name(path, target_tag):
    if target_tag != 'cp313-cp313-win_amd64' or path.suffix != '.whl':
        raise ValueError('wheel_target_invalid')
    parts = path.name[:-4].split('-')
    if len(parts) not in (5, 6) or any(not _WHEEL_FIELD.fullmatch(part) for part in parts):
        raise ValueError('wheel_name_invalid')
    distribution, version = parts[:2]
    if (not _WHEEL_DISTRIBUTION.fullmatch(distribution) or not _WHEEL_VERSION.fullmatch(version)
            or (len(parts) == 6 and not _WHEEL_BUILD.fullmatch(parts[2]))):
        raise ValueError('wheel_name_invalid')
    python_tag, abi_tag, platform_tag = parts[-3:]
    pythons, abis, platforms = python_tag.split('.'), abi_tag.split('.'), platform_tag.split('.')
    portable = 'py3' in pythons and 'none' in abis and 'any' in platforms
    direct = 'cp313' in pythons and 'cp313' in abis and 'win_amd64' in platforms
    stable = ('abi3' in abis and 'win_amd64' in platforms
              and any(re.fullmatch(r'cp3(?:[2-9]|1[0-3])', tag) for tag in pythons))
    if not (portable or direct or stable):
        raise ValueError('wheel_target_invalid')
    return _name(distribution), version


def _lock_records(raw, expected):
    if (type(raw) is not bytes or not raw or len(raw) > 1024 * 1024
            or type(expected) is not str or not _SHA.fullmatch(expected)
            or hashlib.sha256(raw).hexdigest() != expected):
        raise ValueError('lock_invalid')
    try:
        lines = raw.decode('ascii').splitlines()
    except UnicodeError:
        raise ValueError('lock_invalid') from None
    result = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = _LOCK.fullmatch(line)
        if match is None:
            raise ValueError('lock_invalid')
        name, version, digest = match.groups()
        name = _name(name)
        if name in result:
            raise ValueError('duplicate_distribution')
        result[name] = (version, digest)
    if not result:
        raise ValueError('lock_invalid')
    return result


def validate_plan(*, wheel_roots, wheels, target, python_executable, python_sha256,
                  lock_bytes, lock_sha256, target_tag, max_bytes, free_bytes):
    """Validate explicit candidate inputs without creating or executing anything.

    free_bytes is a caller-provided estimate, not a disk measurement or an
    authorization to materialize. max_bytes bounds the wheel inputs only, not
    extraction peak. Paths and bytes must be revalidated immediately before any
    separately implemented materialization; this is not an immutable lease.
    """
    if (type(wheel_roots) not in (list, tuple) or not 2 <= len(wheel_roots) <= 8
            or type(wheels) not in (list, tuple) or not wheels
            or type(max_bytes) is not int or max_bytes <= 0
            or type(free_bytes) is not int or free_bytes < _FLOOR + max_bytes):
        raise ValueError('plan_invalid')
    roots = [_directory(root) for root in wheel_roots]
    if any(_related(a, b) for i, a in enumerate(roots) for b in roots[i + 1:]):
        raise ValueError('root_overlap')
    supplied = Path(target)
    if not supplied.is_absolute() or '..' in supplied.parts or not supplied.name:
        raise ValueError('target_invalid')
    runtime_manifest._path_parts('assets/' + supplied.name)
    target_path = _directory(supplied.parent) / supplied.name
    if os.path.lexists(target_path) or any(_related(root, target_path) for root in roots):
        raise ValueError('target_invalid')
    executable = runtime_manifest._check_chain(python_executable)
    if _related(executable, target_path) or type(python_sha256) is not str or not _SHA.fullmatch(python_sha256):
        raise ValueError('interpreter_invalid')
    _, interpreter_digest, _ = runtime_builder._hash_file(executable)
    if interpreter_digest != python_sha256:
        raise ValueError('interpreter_invalid')
    expected = _lock_records(lock_bytes, lock_sha256)
    seen, paths, total = {}, set(), 0
    for wheel in wheels:
        if type(wheel) is not dict or set(wheel) != {'path', 'size', 'sha256'}:
            raise ValueError('wheel_invalid')
        path = runtime_manifest._check_chain(wheel['path'])
        if sum(_related(root, path) and os.path.normcase(os.path.commonpath((str(root), str(path))))
               == os.path.normcase(str(root)) for root in roots) != 1:
            raise ValueError('wheel_root_invalid')
        name, version = _wheel_name(path, target_tag)
        path_key = os.path.normcase(str(path))
        if name in seen or path_key in paths:
            raise ValueError('duplicate_wheel')
        if (type(wheel['size']) is not int or wheel['size'] < 0 or type(wheel['sha256']) is not str
                or not _SHA.fullmatch(wheel['sha256'])):
            raise ValueError('wheel_invalid')
        size, digest, _ = runtime_builder._hash_file(path)
        if (size, digest) != (wheel['size'], wheel['sha256']):
            raise ValueError('wheel_pin_mismatch')
        seen[name], total = (version, digest), total + size
        paths.add(path_key)
    if seen != expected or total > max_bytes:
        raise ValueError('wheel_lock_mismatch')
    return {'wheel_count': len(seen), 'wheel_bytes': total, 'lock_sha256': lock_sha256,
            'target_tag': target_tag, 'disk_measurement': 'NOT_MEASURED',
            'materialized': False, 'dependency_closure': 'NOT_VERIFIED',
            'interpreter_abi': 'NOT_VERIFIED', 'wheel_archive_metadata': 'NOT_VERIFIED'}


def build_isolated_env(*, system_root, temp_dir):
    """Construct a prospective child environment without inheriting variables.

    This is a static dictionary only; it does not launch a process or prove OS
    network isolation. System root and temp paths are explicit existing dirs.
    """
    system, temp = _directory(system_root), _directory(temp_dir)
    return {'SYSTEMROOT': str(system), 'WINDIR': str(system), 'TEMP': str(temp), 'TMP': str(temp),
            'PIP_NO_INDEX': '1', 'PIP_CONFIG_FILE': os.devnull, 'PIP_NO_INPUT': '1',
            'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PYTHONNOUSERSITE': '1',
            'PYTHONDONTWRITEBYTECODE': '1', 'SOURCE_DATE_EPOCH': '315532800'}
