"""Compare caller-supplied bundle bytes with a pinned manifest, without I/O.

This is a bounded, deliberately conservative byte comparison, not a directory
walk, source authentication, operator approval, or authorization to restore.
The caller supplies every byte and the independent expected manifest digest.
Paths are restricted to safe ASCII Windows-relative names; a valid bundle with
other names or above these resource limits remains HOLD, not a failed restore.
The separate deployment-manifest.json bytes are not part of supplied_files.
"""
import hashlib
import hmac
import re

from tools.wd_reboot_restore_inventory_assess import _Invalid, _commit, _hash, _json, _require, _same_hash

MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_FILES = 4096
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_RELATIVE_PATH = 2048
_DEVICE = re.compile(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$', re.IGNORECASE)


def _relative_name(value):
    _require(type(value) is str and 0 < len(value) <= MAX_RELATIVE_PATH)
    _require(all(32 <= ord(char) < 127 and char not in '<>:"|?*' for char in value))
    parts = value.replace('\\', '/').split('/')
    _require(all(part and part not in ('.', '..') and len(part) <= 255
                 and not part.endswith(('.', ' ')) and not _DEVICE.fullmatch(part) for part in parts))
    normalized = '/'.join(parts).lower()
    _require(normalized != 'deployment-manifest.json')
    return normalized


def assess_bundle_snapshot(*, manifest_bytes, expected_manifest_sha256, expected_commit, supplied_files):
    """Return only facts about supplied bytes; never read paths or run a process."""
    result = {
        'status': 'HOLD', 'supplied_bundle_bytes_match_manifest': False,
        'supplied_file_count': 0, 'authority_effect': 'none', 'rollback_performed': False,
        'filesystem_enumeration_verified': False, 'prior_bundle_files_verified': False,
        'reparse_points_checked': False, 'anchors_authenticated': False,
        'process_liveness_verified': False,
    }
    try:
        _require(type(manifest_bytes) is bytes and len(manifest_bytes) <= MAX_MANIFEST_BYTES)
        _require(_same_hash(manifest_bytes, expected_manifest_sha256))
        commit = _commit(expected_commit)
        manifest = _json(manifest_bytes)
        _require(type(manifest.get('schema_version')) is int and manifest['schema_version'] == 1)
        _require(manifest.get('source_commit') == commit)
        entries = manifest.get('files')
        _require(type(entries) is dict and 0 < len(entries) <= MAX_FILES)
        _require(type(supplied_files) is dict and len(supplied_files) == len(entries))
        expected = {}
        for name, expected_hash in entries.items():
            key = _relative_name(name)
            _require(key not in expected)
            expected[key] = _hash(expected_hash)
        seen = set()
        total = 0
        for name, data in supplied_files.items():
            key = _relative_name(name)
            _require(key in expected and key not in seen and type(data) is bytes and len(data) <= MAX_FILE_BYTES)
            seen.add(key)
            total += len(data)
            _require(total <= MAX_TOTAL_BYTES)
            _require(hmac.compare_digest(hashlib.sha256(data).hexdigest().upper(), expected[key]))
        _require(len(seen) == len(expected))
        result['supplied_file_count'] = len(seen)
        result['supplied_bundle_bytes_match_manifest'] = True
        result['status'] = 'SUPPLIED_BYTES_MATCH'
    except (_Invalid, ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        pass
    return result
