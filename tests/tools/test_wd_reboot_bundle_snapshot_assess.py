"""Only caller-supplied synthetic bytes; this does not inspect any bundle on disk."""
import copy
import hashlib
import json

import pytest


COMMIT = 'a' * 40


def digest(data):
    return hashlib.sha256(data).hexdigest().upper()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sample():
    supplied = {'start-wd-all.ps1': b'synthetic launcher',
                'tools-bootstrap/.agent-bridge/bin/reader.py': b'synthetic reader'}
    manifest = encoded({'schema_version': 1, 'source_commit': COMMIT, 'source_branch': 'synthetic',
                        'files': {name: digest(data) for name, data in supplied.items()}})
    return {'manifest_bytes': manifest, 'expected_manifest_sha256': digest(manifest),
            'expected_commit': COMMIT, 'supplied_files': supplied}


def assess(inputs):
    from tools.wd_reboot_bundle_snapshot_assess import assess_bundle_snapshot
    return assess_bundle_snapshot(**inputs)


def rewrite_manifest(inputs, modify):
    manifest = json.loads(inputs['manifest_bytes'])
    modify(manifest)
    inputs['manifest_bytes'] = encoded(manifest)
    inputs['expected_manifest_sha256'] = digest(inputs['manifest_bytes'])


def test_exact_supplied_bytes_match_but_do_not_authorize_or_verify_filesystem():
    inputs = sample()
    original = copy.deepcopy(inputs)
    result = assess(inputs)
    assert result['status'] == 'SUPPLIED_BYTES_MATCH'
    assert result['supplied_bundle_bytes_match_manifest'] is True
    assert result['authority_effect'] == 'none'
    for field in ('filesystem_enumeration_verified', 'prior_bundle_files_verified', 'reparse_points_checked',
                  'anchors_authenticated', 'process_liveness_verified', 'rollback_performed'):
        assert result[field] is False
    assert inputs == original


@pytest.mark.parametrize('damage', ['missing', 'extra', 'changed', 'manifest_pin', 'commit', 'schema',
                                    'empty_files', 'invalid_hash', 'nonstring_hash', 'wrong_files_type',
                                    'collision', 'traversal', 'absolute', 'drive', 'unc', 'ads',
                                    'empty_component', 'dot_component', 'trailing_dot', 'device_name',
                                    'nul', 'duplicate_key', 'empty_key', 'manifest_self', 'unicode_case_collision',
                                    'nonbytes_payload'])
def test_bad_or_unbounded_inputs_hold(damage):
    inputs = sample()
    files = inputs['supplied_files']
    first = next(iter(files))
    if damage == 'missing': del files[first]
    elif damage == 'extra': files['unlisted.txt'] = b'synthetic'
    elif damage == 'changed': files[first] += b'changed'
    elif damage == 'manifest_pin': inputs['expected_manifest_sha256'] = '0' * 64
    elif damage == 'commit': inputs['expected_commit'] = 'b' * 40
    elif damage == 'schema': rewrite_manifest(inputs, lambda doc: doc.update(schema_version=2))
    elif damage == 'empty_files': rewrite_manifest(inputs, lambda doc: doc.update(files={}))
    elif damage == 'invalid_hash': rewrite_manifest(inputs, lambda doc: doc['files'].update({first: 'no'}))
    elif damage == 'nonstring_hash': rewrite_manifest(inputs, lambda doc: doc['files'].update({first: 1}))
    elif damage == 'wrong_files_type': rewrite_manifest(inputs, lambda doc: doc.update(files=[]))
    elif damage == 'collision': files[first.upper()] = files[first]
    elif damage == 'nonbytes_payload': files[first] = bytearray(files[first])
    elif damage == 'duplicate_key':
        raw = inputs['manifest_bytes']
        inputs['manifest_bytes'] = raw[:-1] + b',"source_commit":"' + COMMIT.encode() + b'"}'
        inputs['expected_manifest_sha256'] = digest(inputs['manifest_bytes'])
    else:
        name = {'traversal':'../escape', 'absolute':'/escape', 'drive':'C:/escape',
                'unc':'//server/share/file', 'ads':'file.txt:stream', 'empty_component':'a//b',
                'dot_component':'a/./b', 'trailing_dot':'unsafe.', 'device_name':'CON.txt',
                'nul':'bad\x00name', 'empty_key':'', 'manifest_self':'deployment-manifest.json',
                'unicode_case_collision':'straße.txt'}[damage]
        if damage == 'unicode_case_collision':
            files[name] = b'one'; files['STRASSE.txt'] = b'two'
            rewrite_manifest(inputs, lambda doc: doc['files'].update({name:digest(b'one'), 'STRASSE.txt':digest(b'two')}))
        else:
            files[name] = b'synthetic'
            rewrite_manifest(inputs, lambda doc: doc['files'].update({name:digest(b'synthetic')}))
    result = assess(inputs)
    assert result['status'] == 'HOLD', (damage, result)
    assert result['supplied_bundle_bytes_match_manifest'] is False
    assert result['authority_effect'] == 'none' and result['rollback_performed'] is False


def test_case_and_separator_variation_matches_windows_file_identity():
    inputs = sample()
    inputs['supplied_files'] = {name.upper().replace('/', '\\'): data
                                for name, data in inputs['supplied_files'].items()}
    assert assess(inputs)['status'] == 'SUPPLIED_BYTES_MATCH'


def test_hundreds_of_synthetic_files_fit_the_bounded_contract():
    files = {f'tools-bootstrap/pkg/item_{number}.py': str(number).encode() for number in range(600)}
    manifest = encoded({'schema_version': 1, 'source_commit': COMMIT,
                        'files': {name:digest(data) for name,data in files.items()}})
    assert assess({'manifest_bytes':manifest, 'expected_manifest_sha256':digest(manifest),
                   'expected_commit':COMMIT, 'supplied_files':files})['status'] == 'SUPPLIED_BYTES_MATCH'


def test_byte_and_file_count_limits_are_fail_closed(monkeypatch):
    import tools.wd_reboot_bundle_snapshot_assess as subject
    inputs = sample()
    monkeypatch.setattr(subject, 'MAX_FILES', 1)
    assert assess(inputs)['status'] == 'HOLD'
    monkeypatch.setattr(subject, 'MAX_FILES', 4)
    monkeypatch.setattr(subject, 'MAX_FILE_BYTES', 1)
    assert assess(inputs)['status'] == 'HOLD'
    monkeypatch.setattr(subject, 'MAX_FILE_BYTES', 100)
    monkeypatch.setattr(subject, 'MAX_TOTAL_BYTES', 1)
    assert assess(inputs)['status'] == 'HOLD'
    monkeypatch.setattr(subject, 'MAX_MANIFEST_BYTES', 1)
    assert assess(inputs)['status'] == 'HOLD'


def test_assessor_does_not_perform_file_or_process_io(monkeypatch):
    import builtins
    import subprocess
    from tools.wd_reboot_bundle_snapshot_assess import assess_bundle_snapshot
    def forbidden(*_args, **_kwargs): raise AssertionError('I/O forbidden')
    inputs = sample()
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert assess_bundle_snapshot(**inputs)['status'] == 'SUPPLIED_BYTES_MATCH'
