"""Synthetic interpreter-tree identities; never execute files or inspect the host runtime."""
import hashlib
import json
import os

import pytest

from sandbox.mind.step2 import interpreter_identity as subject


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def synthetic(tmp_path):
    root = tmp_path / "synthetic-runtime"
    root.mkdir()
    contents = {"bin/interpreter.exe": ("interpreter", b"not-an-executable"),
                "lib/stdlib.zip": ("stdlib", b"not-a-zip"),
                "dlls/example.dll": ("dll", b"not-a-dll")}
    records = []
    for name, (role, data) in contents.items():
        target = root.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append({"role": role, "path": name, "size": len(data), "sha256": digest(data)})
    return root, {"schema": subject.SCHEMA, "files": records}


def verify(root, document, *, pin=None):
    raw = encoded(document)
    return subject.verify_interpreter_identity(enabled=True, root=root, descriptor_bytes=raw,
                                                expected_sha256=digest(raw) if pin is None else pin)


def test_default_off_does_not_touch_inputs():
    class Poison:
        def __getattribute__(self, name):
            pytest.fail("OFF touched input")
    assert subject.verify_interpreter_identity(root=Poison(), descriptor_bytes=Poison(),
                                               expected_sha256=Poison()) == {"status": "OFF"}


def test_matching_synthetic_tree_returns_metadata_only(synthetic):
    root, document = synthetic
    result = verify(root, document)
    assert result == {"status": "VERIFIED", "descriptor_sha256": digest(encoded(document)),
                      "file_count": 3, "total_bytes": sum(record["size"] for record in document["files"])}
    assert str(root) not in repr(result)


def test_wrong_pin_refuses_before_json_or_filesystem(synthetic, monkeypatch):
    root, document = synthetic
    monkeypatch.setattr(subject.json, "loads", lambda *_a, **_k: pytest.fail("parsed before pin"))
    monkeypatch.setattr(subject.runtime, "_check_chain", lambda *_a: pytest.fail("opened before pin"))
    with pytest.raises(subject.IdentityRefused, match="^interpreter_identity_refused$"):
        verify(root, document, pin="0" * 64)


@pytest.mark.parametrize("enabled", [None, 0, 1, "true"])
def test_enabled_requires_exact_true(synthetic, enabled):
    root, document = synthetic
    raw = encoded(document)
    with pytest.raises(subject.IdentityRefused, match="^interpreter_identity_refused$"):
        subject.verify_interpreter_identity(enabled=enabled, root=root, descriptor_bytes=raw,
                                            expected_sha256=digest(raw))


def test_duplicate_json_and_oversized_descriptor_refuse_without_chained_data(synthetic):
    root, _ = synthetic
    for raw in (b'{"schema":"x","schema":"y","files":[]}', b" " * (subject.MAX_DESCRIPTOR_BYTES + 1)):
        with pytest.raises(subject.IdentityRefused) as caught:
            subject.verify_interpreter_identity(enabled=True, root=root, descriptor_bytes=raw,
                                                expected_sha256=digest(raw))
        assert caught.value.args == ("interpreter_identity_refused",)
        assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("mutation", ["duplicate", "case", "escape", "absolute", "missing_role",
                                      "extra_role", "wrong_size", "wrong_hash", "unknown_key"])
def test_bad_descriptor_refuses(synthetic, mutation):
    root, document = synthetic
    records = document["files"]
    if mutation == "duplicate": records.append(dict(records[0]))
    elif mutation == "case": records.append(dict(records[0], path="BIN/interpreter.exe"))
    elif mutation == "escape": records[0]["path"] = "bin/../escape"
    elif mutation == "absolute": records[0]["path"] = "/outside"
    elif mutation == "missing_role": records[0]["role"] = "dll"
    elif mutation == "extra_role": records[0]["role"] = "unknown"
    elif mutation == "wrong_size": records[0]["size"] += 1
    elif mutation == "wrong_hash": records[0]["sha256"] = "0" * 64
    else: document["extra"] = "not-allowed"
    with pytest.raises(subject.IdentityRefused, match="^interpreter_identity_refused$"):
        verify(root, document)


@pytest.mark.parametrize("mutation", ["missing", "extra", "extra_directory", "changed"])
def test_changed_tree_refuses(synthetic, mutation):
    root, document = synthetic
    path = root / "bin" / "interpreter.exe"
    if mutation == "missing": path.unlink()
    elif mutation == "extra": (root / "unexpected.txt").write_bytes(b"x")
    elif mutation == "extra_directory": (root / "unexpected").mkdir()
    else: path.write_bytes(b"x" * path.stat().st_size)
    with pytest.raises(subject.IdentityRefused, match="^interpreter_identity_refused$"):
        verify(root, document)


def test_unknown_directory_is_not_walked(synthetic, monkeypatch):
    root, document = synthetic
    unknown = root / "unknown-subtree"
    unknown.mkdir()
    (unknown / "extra").write_bytes(b"synthetic")
    original_scandir = os.scandir
    scanned = []

    def tracked_scandir(path):
        scanned.append(os.fspath(path))
        return original_scandir(path)

    monkeypatch.setattr(subject.runtime.os, "scandir", tracked_scandir)
    with pytest.raises(subject.IdentityRefused, match="^interpreter_identity_refused$"):
        verify(root, document)
    assert os.fspath(unknown) not in scanned


@pytest.mark.parametrize("mutation", ["directory_to_file", "file_to_directory"])
def test_known_path_with_wrong_type_refuses(synthetic, mutation):
    root, document = synthetic
    target = root / "bin" / "interpreter.exe"
    target.unlink()
    if mutation == "directory_to_file":
        target.parent.rmdir()
        target.parent.write_bytes(b"synthetic")
    else:
        target.mkdir()
    with pytest.raises(subject.IdentityRefused, match="^interpreter_identity_refused$"):
        verify(root, document)


def test_extra_entry_appearing_after_hash_is_refused(synthetic, monkeypatch):
    root, document = synthetic
    original = subject.runtime._same_file
    calls = []

    def add_extra_after_hash(*args):
        valid = original(*args)
        calls.append(1)
        if len(calls) == len(document["files"]):
            (root / "late-extra").write_bytes(b"synthetic")
        return valid

    monkeypatch.setattr(subject.runtime, "_same_file", add_extra_after_hash)
    with pytest.raises(subject.IdentityRefused, match="^interpreter_identity_refused$"):
        verify(root, document)
    assert len(calls) == len(document["files"])


def test_scandir_failure_is_fixed_refusal_without_context(synthetic, monkeypatch):
    root, document = synthetic

    def refuse(_path):
        raise PermissionError("synthetic private detail")

    monkeypatch.setattr(subject.os, "scandir", refuse)
    with pytest.raises(subject.IdentityRefused) as caught:
        verify(root, document)
    assert caught.value.args == ("interpreter_identity_refused",)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_observed_opened_identity_change_refuses(synthetic, monkeypatch):
    root, document = synthetic
    monkeypatch.setattr(subject.runtime, "_same_file", lambda *_: False)
    with pytest.raises(subject.IdentityRefused, match="^interpreter_identity_refused$"):
        verify(root, document)


def test_named_stream_refusal_is_not_suppressed(synthetic, monkeypatch):
    root, document = synthetic
    def refuse(_path):
        raise ValueError("private synthetic detail")
    monkeypatch.setattr(subject.runtime, "_check_no_named_streams", refuse)
    with pytest.raises(subject.IdentityRefused) as caught:
        verify(root, document)
    assert caught.value.args == ("interpreter_identity_refused",)
    assert caught.value.__context__ is None


def test_symlink_or_reparse_is_refused(synthetic, tmp_path):
    root, document = synthetic
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    try:
        os.symlink(outside, root / "unexpected-link")
    except (OSError, NotImplementedError):
        pytest.skip("synthetic symlink creation unavailable")
    with pytest.raises(subject.IdentityRefused, match="^interpreter_identity_refused$"):
        verify(root, document)
