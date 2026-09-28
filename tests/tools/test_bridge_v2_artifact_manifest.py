"""Content integrity is not an operator signature or deployment permission."""
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import tools.bridge_v2_artifact_manifest as subject

from tools.bridge_v2_artifact_manifest import (
    ManifestError, build_manifest, canonical_bytes, parse_manifest, verify_manifest,
)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "b.txt").write_bytes(b"second\r\n")
    (tmp_path / "a.txt").write_bytes(b"first\n")
    return tmp_path


def test_build_verify_and_order_independence(root):
    manifest = build_manifest(root, ["nested/b.txt", "a.txt"])
    assert canonical_bytes(manifest) == canonical_bytes(build_manifest(root, ["a.txt", "nested/b.txt"]))
    assert verify_manifest(root, manifest) == manifest
    assert parse_manifest(canonical_bytes(manifest)) == manifest
    assert [row["path"] for row in manifest["artifacts"]] == ["a.txt", "nested/b.txt"]


@pytest.mark.parametrize("path", [
    "", "../escape", "/absolute", "C:/absolute", "a\\b", "a//b", "./a.txt",
    "a/../a.txt", "a.txt:stream", "a.txt.", "a.txt ", "CON", "nul.txt",
    "nested/COM1.txt", ".git/config", ".codex-audit/secret", "a\x00b", "\ud800",
])
def test_reject_nonportable_or_private_paths_before_read(root, path):
    with pytest.raises(ManifestError):
        build_manifest(root, [path])


@pytest.mark.parametrize("paths", [[], ["a.txt", "a.txt"], ["a.txt", "A.txt"], "a.txt", [1]])
def test_reject_empty_duplicate_case_alias_and_wrong_types(root, paths):
    with pytest.raises(ManifestError):
        build_manifest(root, paths)


@pytest.mark.parametrize("operation", ["modify", "delete", "directory"])
def test_changed_file_fails(root, operation):
    manifest = build_manifest(root, ["a.txt"])
    path = root / "a.txt"
    if operation == "modify":
        path.write_bytes(b"First\n")
    else:
        path.unlink()
        if operation == "directory":
            path.mkdir()
    with pytest.raises(ManifestError):
        verify_manifest(root, manifest)


@pytest.mark.parametrize("field,value", [
    ("size", True), ("size", -1), ("size", 1.0), ("sha256", "0" * 64),
    ("sha256", "A" * 64), ("path", "../a.txt"),
])
def test_row_tampering_fails(root, field, value):
    manifest = build_manifest(root, ["a.txt"])
    manifest["artifacts"][0][field] = value
    with pytest.raises(ManifestError):
        verify_manifest(root, manifest)


@pytest.mark.parametrize("field", ["approved", "signature", "head", "capabilities", "unknown"])
def test_content_contract_never_accepts_authority_fields(root, field):
    manifest = build_manifest(root, ["a.txt"])
    manifest[field] = True
    with pytest.raises(ManifestError):
        verify_manifest(root, manifest)


def test_manifest_shape_and_order_are_strict(root):
    valid = build_manifest(root, ["a.txt", "nested/b.txt"])
    invalid = [None, [], {}, {"schema": "wrong", "artifacts": []}]
    reversed_rows = copy.deepcopy(valid)
    reversed_rows["artifacts"].reverse()
    invalid.append(reversed_rows)
    extra = copy.deepcopy(valid)
    extra["artifacts"][0]["optional"] = True
    invalid.append(extra)
    for value in invalid:
        with pytest.raises(ManifestError):
            verify_manifest(root, value)


@pytest.mark.parametrize("data", [b'{"schema":"a","schema":"b"}', b'{"size":NaN}', b'\xff', b'[]', b'{} trailing'])
def test_json_parser_rejects_ambiguous_input(data):
    with pytest.raises(ManifestError):
        parse_manifest(data)


def test_noncanonical_manifest_bytes_refused(root):
    manifest = build_manifest(root, ["a.txt"])
    for value in [canonical_bytes(manifest) + b"\n", json.dumps(manifest, indent=2).encode()]:
        with pytest.raises(ManifestError, match="noncanonical_manifest_bytes"):
            parse_manifest(value)


def test_parser_rejects_case_aliases_without_filesystem_fallback(root):
    manifest = build_manifest(root, ["a.txt"])
    upper = dict(manifest["artifacts"][0], path="A.txt")
    manifest["artifacts"].insert(0, upper)
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ManifestError, match="^duplicate_path$"):
        parse_manifest(raw)


def test_parser_rejects_wrong_byte_domain_without_content_comparison(root):
    manifest = build_manifest(root, ["a.txt"])
    manifest["byte_domain"] = "git-blobs"
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ManifestError, match="^invalid_manifest$"):
        parse_manifest(raw)


def test_surrogate_refused_by_path_contract_before_filesystem(root):
    with pytest.raises(ManifestError, match="^invalid_path$"):
        build_manifest(root, ["\ud800"])


def test_large_integer_has_stable_refusal():
    with pytest.raises(ManifestError):
        parse_manifest(b'{"size":' + b'9' * 5000 + b'}')


@pytest.mark.parametrize("private", [".git", ".codex-audit"])
def test_existing_private_file_refused_before_open(root, monkeypatch, private):
    (root / private).mkdir()
    (root / private / "secret").write_bytes(b"not a real secret")

    def forbidden_open(*args, **kwargs):
        raise AssertionError("private path reached open")

    monkeypatch.setattr(Path, "open", forbidden_open)
    with pytest.raises(ManifestError, match="^invalid_path$"):
        build_manifest(root, [f"{private}/secret"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction fixture")
@pytest.mark.parametrize("as_root", [False, True])
def test_windows_junctions_refused(root, as_root):
    import _winapi
    junction = root / "junction"
    _winapi.CreateJunction(str(root / "nested"), str(junction))
    if as_root:
        with pytest.raises(ManifestError, match="unsafe_root"):
            build_manifest(junction, ["b.txt"])
    else:
        with pytest.raises(ManifestError, match="linked_artifact"):
            build_manifest(root, ["junction/b.txt"])


def test_symlink_file_and_parent_refused(root, tmp_path):
    target = root / "alias"
    try:
        target.symlink_to(root / "nested", target_is_directory=True)
        (root / "link.txt").symlink_to(root / "a.txt")
    except OSError as exc:
        pytest.skip(f"OS does not permit symlink fixture: {exc}")
    for path in ["alias/b.txt", "link.txt"]:
        with pytest.raises(ManifestError):
            build_manifest(root, [path])


def test_unlisted_files_not_claimed_as_verified(root):
    manifest = build_manifest(root, ["a.txt"])
    (root / "unlisted.txt").write_bytes(b"not covered")
    assert verify_manifest(root, manifest) == manifest
    # This API covers the explicit inventory, NOT directory completeness.
    assert len(manifest["artifacts"]) == 1


@pytest.mark.parametrize("path", ["A.txt", "NESTED/b.txt", "nested/B.txt"])
def test_exact_disk_spelling_required_even_on_case_insensitive_fs(root, path):
    with pytest.raises(ManifestError):
        build_manifest(root, [path])


def test_hardlinked_artifact_refused(root):
    os.link(root / "a.txt", root / "hard.txt")
    with pytest.raises(ManifestError, match="hardlinked_artifact"):
        build_manifest(root, ["hard.txt"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows short names only")
def test_windows_83_alias_rejected(root):
    import ctypes
    path = root / "longfilename_abcdef.txt"
    path.write_bytes(b"alias test")
    buffer = ctypes.create_unicode_buffer(32768)
    get_short = ctypes.windll.kernel32.GetShortPathNameW
    get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    get_short.restype = ctypes.c_uint32
    assert 0 < get_short(str(path), buffer, len(buffer)) < len(buffer)
    short_name = Path(buffer.value).name
    if short_name == path.name:
        pytest.skip("8.3 name generation disabled on fixture volume")
    with pytest.raises(ManifestError, match="noncanonical_disk_path"):
        build_manifest(root, [short_name, path.name])


def test_materialized_byte_domain_never_normalizes_line_endings(root):
    (root / "a.txt").write_bytes(b"one\ntwo\n")
    manifest = build_manifest(root, ["a.txt"])
    assert manifest["byte_domain"] == "materialized-artifacts"
    (root / "a.txt").write_bytes(b"one\r\ntwo\r\n")
    with pytest.raises(ManifestError, match="content_mismatch"):
        verify_manifest(root, manifest)


@pytest.mark.parametrize("changed", [False, True])
def test_ctime_compared_within_api_not_across_windows_stat_apis(root, monkeypatch, changed):
    original = subject.os.fstat
    calls = []

    def fd_stat(fd):
        value = original(fd)
        calls.append(fd)
        result = {k: getattr(value, k) for k in
                  ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")}
        result["st_ctime_ns"] = 100 + (len(calls) if changed else 0)
        return SimpleNamespace(**result)

    monkeypatch.setattr(subject.os, "fstat", fd_stat)
    if changed:
        with pytest.raises(ManifestError, match="concurrent_change"):
            build_manifest(root, ["a.txt"])
    else:
        assert build_manifest(root, ["a.txt"])["artifacts"][0]["size"] == 6


def test_cli_real_files_and_nonzero_on_corruption(root):
    script = Path(__file__).resolve().parents[2] / "tools/bridge_v2_artifact_manifest.py"
    built = subprocess.run([sys.executable, str(script), "build", "--root", str(root),
                            "--path", "a.txt"], capture_output=True, check=True)
    manifest_path = root / "manifest.json"
    manifest_path.write_bytes(built.stdout)
    command = [sys.executable, str(script), "verify", "--root", str(root), "--manifest", str(manifest_path)]
    result = subprocess.run(command, capture_output=True, check=True)
    assert json.loads(result.stdout)["verified"] is True
    assert json.loads(result.stdout)["manifest_sha256"] == hashlib.sha256(built.stdout).hexdigest()
    assert built.stdout == canonical_bytes(parse_manifest(built.stdout))
    (root / "a.txt").write_bytes(b"corrupted")
    result = subprocess.run(command, capture_output=True)
    assert result.returncode == 2
    assert not result.stdout
    assert b"content_mismatch" in result.stderr
