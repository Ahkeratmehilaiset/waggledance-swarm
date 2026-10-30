"""Exact artifact bytes, not a second serializer or presentation comparator."""
from __future__ import annotations

import hashlib
import json

import pytest


def api():
    from tools import exact_written_bytes
    return exact_written_bytes


def digest(data):
    return hashlib.sha256(data).hexdigest()


def test_exclusive_write_and_read_back(tmp_path):
    data = b'\x00canonical\r\nbytes\xff'
    path = tmp_path / 'artifact'
    assert api().write_bytes_exclusive(path, data) == {
        'sha256': digest(data), 'length': len(data),
    }
    assert path.read_bytes() == data
    with pytest.raises(FileExistsError):
        api().write_bytes_exclusive(path, b'replacement')
    assert path.read_bytes() == data


@pytest.mark.parametrize('replacement', [b'changed', b'shor', b'longer canonical'])
def test_mutation_and_truncation_refuse(tmp_path, replacement):
    original = b'canonical'
    path = tmp_path / 'artifact'
    path.write_bytes(original)
    path.write_bytes(replacement)
    with pytest.raises(api().ArtifactMismatch):
        api().verify_written_bytes(path, digest(original), len(original))


def test_hex_index_width_is_not_signature_normalization(tmp_path):
    canonical = b'index 12345678..abcdef01 100644\n'
    api_presentation = b'index 123456789..abcdef012 100644\n'
    path = tmp_path / 'designated-canonical.diff'
    path.write_bytes(canonical)
    assert api().verify_written_bytes(path, digest(canonical), len(canonical))
    assert digest(canonical) != digest(api_presentation)
    with pytest.raises(api().ArtifactMismatch):
        api().verify_written_bytes(path, digest(api_presentation), len(canonical))
    # A changed canonical diff itself also invalidates the old byte signature.
    path.write_bytes(api_presentation)
    with pytest.raises(api().ArtifactMismatch):
        api().verify_written_bytes(path, digest(canonical), len(canonical))


def test_api_hunk_rendering_cannot_replace_git_artifact(tmp_path):
    git_bytes = b'@@ -1 +1 @@ def producer():\n-old\n+new\n'
    api_bytes = b'@@ -1 +1 @@\n-old\n+new\n'
    path = tmp_path / 'canonical.diff'
    path.write_bytes(git_bytes)
    assert api().verify_written_bytes(path, digest(git_bytes), len(git_bytes))
    with pytest.raises(api().ArtifactMismatch):
        api().verify_written_bytes(path, digest(api_bytes), len(git_bytes))


def test_ps_host_json_renderings_are_distinct_artifacts_not_dispatch_gates(tmp_path):
    literal = b'{"message":"a<b"}'
    escaped = b'{"message":"a\\u003cb"}'
    assert json.loads(literal) == json.loads(escaped)
    path = tmp_path / 'actual-content.utf8'
    path.write_bytes(literal)
    assert api().verify_written_bytes(path, digest(literal), len(literal))
    with pytest.raises(api().ArtifactMismatch):
        api().verify_written_bytes(path, digest(escaped), len(literal))
    # Neither another host nor its JSON round-trip is called by the verifier.


@pytest.mark.parametrize('sha,length', [('f' * 63, 1), ('F' * 64, 1), ('0' * 64, True), ('0' * 64, -1)])
def test_invalid_expected_binding_refuses(tmp_path, sha, length):
    with pytest.raises(ValueError):
        api().verify_written_bytes(tmp_path / 'not-read', sha, length)


def test_missing_artifact_is_not_success(tmp_path):
    with pytest.raises(FileNotFoundError):
        api().verify_written_bytes(tmp_path / 'missing', '0' * 64, 0)


def test_cli_only_verifies_written_bytes(tmp_path, capsys):
    path = tmp_path / 'artifact'
    path.write_bytes(b'actual')
    args = [str(path), '--expected-sha256', digest(b'actual'), '--expected-length', '6']
    assert api().main(args) == 0
    assert json.loads(capsys.readouterr().out) == {'sha256': digest(b'actual'), 'length': 6}
    path.write_bytes(b'mutation')
    assert api().main(args) == 2
    assert json.loads(capsys.readouterr().out) == {'error': 'ArtifactMismatch'}
