"""Assess caller-supplied reboot backup bytes without performing any I/O.

The caller supplies the independently selected hashes, transaction file names,
and bytes. Matching them is evidence of consistency, not authentication or
authority to restore. The original restore tool's Apply prohibition is unchanged.
Same-commit reinstalls deliberately remain HOLD: this assessor is scoped to a
prior different commit, and does not accept all inventories the writer emits.
"""

import hashlib
import hmac
import json
import ntpath
import re

_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_STATE_NAME = "WD_REBOOT_STATE_CURRENT.json"
_LIMIT = 16 * 1024 * 1024


class _Invalid(ValueError):
    pass


def _require(condition):
    if not condition:
        raise _Invalid()


def _digest(value):
    _require(type(value) is bytes and len(value) <= _LIMIT)
    return hashlib.sha256(value).hexdigest().upper()


def _hash(value):
    _require(type(value) is str and _HASH.fullmatch(value) is not None)
    return value.upper()


def _same_hash(data, expected):
    return hmac.compare_digest(_digest(data), _hash(expected))


def _pairs(pairs):
    result = {}
    folded = set()
    for key, value in pairs:
        # PowerShell's object conversion cannot represent case-colliding keys.
        # Unicode casefold is intentionally conservative for all JSON objects.
        _require(key != '')  # Windows PowerShell 5.1 rejects empty member names.
        normalized = key.casefold()
        _require(normalized not in folded)
        folded.add(normalized)
        result[key] = value
    return result


def _json(data):
    _require(type(data) is bytes and len(data) <= _LIMIT)
    def invalid_constant(_value):
        raise _Invalid()
    value = json.loads(data.decode('utf-8').lstrip('\ufeff'), object_pairs_hook=_pairs, parse_constant=invalid_constant)
    _require(type(value) is dict)
    return value


def _commit(value):
    _require(type(value) is str and _COMMIT.fullmatch(value) is not None)
    return value


def _path(value):
    _require(type(value) is str and bool(re.match(r'^[cC]:[\\/]', value)))
    _require('\x00' not in value and ':' not in value[2:])
    components = re.split(r'[\\/]', value[2:])
    _require(not any(part in ('.', '..') for part in components))
    _require(ntpath.isabs(value) and ntpath.splitdrive(value)[0].lower() == 'c:')
    return ntpath.normcase(ntpath.normpath(value))


def _name(value):
    _require(type(value) is str and _NAME.fullmatch(value) is not None and not value.endswith('.'))
    return value


def assess_inventory(*, inventory_bytes, expected_inventory_sha256, expected_new_commit,
                     expected_new_manifest_sha256, expected_previous_commit,
                     expected_previous_manifest_sha256, expected_names, backup_bytes,
                     prior_manifest_bytes):
    """Return evidence status; never reads files, executes processes, or grants authority."""
    result = {
        'status': 'HOLD', 'authority_effect': 'none', 'restore_performed': False,
        'prior_bundle_files_verified': False, 'reparse_points_checked': False,
        'process_liveness_verified': False, 'anchors_authenticated': False,
        'backup_path_containment_verified': False, 'prior_wrapper_state_compatibility_verified': False,
        'transaction_file_set_verified': False, 'state_final_pair_consistent': False,
    }
    try:
        _require(_same_hash(inventory_bytes, expected_inventory_sha256))
        new, old = _commit(expected_new_commit), _commit(expected_previous_commit)
        _require(new != old)
        new_hash, old_hash = _hash(expected_new_manifest_sha256), _hash(expected_previous_manifest_sha256)
        doc = _json(inventory_bytes)
        _require(set(doc) == {'schema', 'new_commit', 'new_manifest_sha256', 'machine_root', 'prior_state', 'files'})
        _require(doc['schema'] == 'wd.reboot-backup-inventory.v1')
        _require(_commit(doc['new_commit']) == new and _hash(doc['new_manifest_sha256']) == new_hash)
        _path(doc['machine_root'])
        prior = doc['prior_state']
        _require(type(prior) is dict and set(prior) == {'state_sha256', 'source_commit', 'bundle_store', 'active_bundle', 'manifest_sha256'})
        _require(_commit(prior['source_commit']) == old and _hash(prior['manifest_sha256']) == old_hash)
        store, bundle = _path(prior['bundle_store']), _path(prior['active_bundle'])
        _require(bundle == _path(ntpath.join(store, old)))
        _require(_same_hash(prior_manifest_bytes, old_hash))
        manifest = _json(prior_manifest_bytes)
        _require(manifest.get('source_commit') == old and type(manifest.get('files')) is dict)
        _require(type(expected_names) in (tuple, list) and 0 < len(expected_names) <= 128)
        names = [_name(name) for name in expected_names]
        _require(len({name.casefold() for name in names}) == len(names) and _STATE_NAME in names)
        rows = doc['files']
        _require(type(rows) is list and len(rows) == len(names))
        _require(type(backup_bytes) is dict and len(backup_bytes) <= 128)
        seen, present = set(), set()
        for row in rows:
            _require(type(row) is dict and set(row) == {'name', 'present', 'sha256'})
            name = _name(row['name'])
            _require(name in names and name.casefold() not in seen and type(row['present']) is bool)
            seen.add(name.casefold())
            if row['present']:
                present.add(name)
                _require(name in backup_bytes and _same_hash(backup_bytes[name], row['sha256']))
            else:
                _require(row['sha256'] is None)
        _require(present == set(backup_bytes) and _STATE_NAME in present)
        state_bytes = backup_bytes[_STATE_NAME]
        _require(_same_hash(state_bytes, prior['state_sha256']))
        state = _json(state_bytes)
        # Generated wrappers compare each field with PowerShell's case-sensitive
        # -cne, including the uppercase manifest hash; normalized digest equality
        # alone would accept a state those wrappers refuse.
        _require(state.get('source_commit') == old and state.get('manifest_sha256') == old_hash)
        _require(type(state.get('final_commit')) is str and state['final_commit'] == old)
        _require(type(state.get('final_manifest_sha256')) is str and state['final_manifest_sha256'] == old_hash)
        _require(_path(state.get('active_bundle')) == bundle)
        result['state_final_pair_consistent'] = True
        result['status'] = 'BACKUP_CONSISTENT'
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        pass
    return result
