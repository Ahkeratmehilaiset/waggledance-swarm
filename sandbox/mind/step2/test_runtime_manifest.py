"""Synthetic-only Q4 runtime manifest validation; no wheel/model data or deployment."""

import hashlib
import json
import os
from pathlib import Path

import pytest

GENERATION = "a" * 40
REVISION = "b" * 40
PATHS = (
    "site/scipy/io/tests/data/Transparent Busy.ani",
    "site/setuptools/launcher manifest.xml",
    "site/setuptools/script (dev).tmpl",
    "site/setuptools/_vendor/jaraco/text/Lorem ipsum.txt",
    "site/example/__init__.py",
    "model/config.json",
    "assets/runtime.json",
)


def verifier():
    from sandbox.mind.step2 import runtime_manifest
    return runtime_manifest


def write_manifest(path, data):
    raw = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    files = []
    for index, relative in enumerate(PATHS):
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"synthetic-file-{index}".encode()
        path.write_bytes(content)
        files.append({"path": relative, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    manifest = {"schema": "wd.q4-runtime-manifest.v1", "generation": GENERATION,
                "revision": REVISION, "files": files}
    path = tmp_path / "runtime-manifest.json"
    return root, path, manifest, write_manifest(path, manifest)


def run(bundle, *, pin=None, generation=GENERATION, revision=REVISION):
    root, path, _, default_pin = bundle
    return verifier().verify_runtime(root, path, default_pin if pin is None else pin,
                                     expected_generation=generation, expected_revision=revision)


def changed(bundle, files):
    root, path, manifest, _ = bundle
    updated = dict(manifest, files=files)
    return root, path, updated, write_manifest(path, updated)


def test_valid_runtime_allows_only_four_explicit_wheel_names(bundle):
    result = run(bundle)
    assert result["generation"] == GENERATION
    assert result["revision"] == REVISION
    assert result["file_count"] == len(PATHS)
    assert result["manifest_sha256"] == bundle[3]


@pytest.mark.parametrize("pin", ["0" * 64, "bad", "", None])
def test_wrong_or_absent_external_pin_refuses(bundle, pin):
    root, path, _, _ = bundle
    with pytest.raises(ValueError):
        verifier().verify_runtime(root, path, pin, expected_generation=GENERATION, expected_revision=REVISION)


def test_generation_and_revision_are_externally_bound(bundle):
    with pytest.raises(ValueError):
        run(bundle, generation="c" * 40)
    with pytest.raises(ValueError):
        run(bundle, revision="d" * 40)


@pytest.mark.parametrize("path", [
    "site/../escape.py", "site/./x.py", "../escape.py", "/site/abs.py", "C:/site/a.py",
    "//server/share/x.py", "site/a\\b.py", "site/a.py:stream", "site/a//b.py", "site/a/",
    "site/CON", "site/con.txt", "site/AUX/x.py", "site/COM1.py", "site/LPT9.log", "site/PRN.dat",
    "site/trailing.", "site/trailing ", "site/ arbitrary.py", "site/general file.py", "site/general(dev).py",
    "model/setuptools/script (dev).tmpl", "assets/launcher manifest.xml", "other/okay.py",
])
def test_bad_paths_refused_before_filesystem_access(bundle, path):
    entries = list(bundle[2]["files"]) + [{"path": path, "size": 0, "sha256": "0" * 64}]
    with pytest.raises(ValueError):
        run(changed(bundle, entries))


def test_duplicate_file_and_case_alias_refused(bundle):
    entries = list(bundle[2]["files"])
    for extra in (entries[0], dict(entries[0], path=entries[0]["path"].upper())):
        with pytest.raises(ValueError):
            run(changed(bundle, entries + [extra]))


def test_directory_case_alias_refused(bundle):
    entries = list(bundle[2]["files"]) + [{"path": "site/EXAMPLE/other.py", "size": 0, "sha256": "0" * 64}]
    with pytest.raises(ValueError):
        run(changed(bundle, entries))


def test_duplicate_json_keys_refused_even_with_correct_hash(bundle):
    root, path, _, _ = bundle
    raw = ('{"schema":"wd.q4-runtime-manifest.v1","schema":"wd.q4-runtime-manifest.v1",'
           f'"generation":"{GENERATION}","revision":"{REVISION}","files":[]}}').encode()
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        verifier().verify_runtime(root, path, hashlib.sha256(raw).hexdigest(),
                                  expected_generation=GENERATION, expected_revision=REVISION)


@pytest.mark.parametrize("mutation", ["modified_same_length", "missing", "extra", "extra_empty_dir"])
def test_any_missing_extra_or_changed_file_fails_closed(bundle, mutation):
    root = bundle[0]
    path = root / "model" / "config.json"
    if mutation == "modified_same_length":
        path.write_bytes(b"x" * path.stat().st_size)
    elif mutation == "missing":
        path.unlink()
    elif mutation == "extra":
        (root / "site" / "extra.py").write_bytes(b"extra")
    else:
        (root / "site" / "unexpected-empty").mkdir()
    with pytest.raises(ValueError):
        run(bundle)


def test_runtime_tree_reparse_or_symlink_is_rejected(bundle, tmp_path):
    root = bundle[0]
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, root / "site" / "unexpected-link", target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError):
        run(bundle)


def test_oversized_manifest_fails_closed_before_parsing(bundle):
    root, path, _, _ = bundle
    raw = b" " * (8 * 1024 * 1024 + 1)
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        verifier().verify_runtime(root, path, hashlib.sha256(raw).hexdigest(),
                                  expected_generation=GENERATION, expected_revision=REVISION)


def test_hardlinked_runtime_files_are_rejected(bundle):
    root = bundle[0]
    try:
        os.link(root / "model" / "config.json", root / "model" / "hardlink.json")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hardlink creation unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError):
        run(bundle)


def test_relative_roots_and_manifest_inside_the_tree_are_refused(bundle):
    root, path, _, pin = bundle
    with pytest.raises(ValueError):
        verifier().verify_runtime(Path("relative"), path, pin,
                                  expected_generation=GENERATION, expected_revision=REVISION)
    with pytest.raises(ValueError):
        verifier().verify_runtime(root, root / "model" / "config.json", pin,
                                  expected_generation=GENERATION, expected_revision=REVISION)


@pytest.mark.parametrize("target", ["root", "manifest"])
def test_linked_root_and_linked_manifest_are_refused(bundle, tmp_path, target):
    root, path, _, pin = bundle
    link = tmp_path / f"synthetic-{target}-link"
    try:
        os.symlink(root if target == "root" else path, link, target_is_directory=target == "root")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError):
        verifier().verify_runtime(link if target == "root" else root, link if target == "manifest" else path, pin,
                                  expected_generation=GENERATION, expected_revision=REVISION)


def test_declared_total_file_size_is_bounded_before_reads(bundle):
    entries = list(bundle[2]["files"])
    entries[0] = dict(entries[0], size=8 * 1024 * 1024 * 1024)
    entries[1] = dict(entries[1], size=8 * 1024 * 1024 * 1024)
    with pytest.raises(ValueError, match="runtime_over_budget"):
        run(changed(bundle, entries))


@pytest.mark.parametrize("changes", [
    {"generation": "c" * 40}, {"revision": "d" * 40}, {"extra": "disallowed"},
    {"files": []}, {"schema": "wrong"},
])
def test_wrong_identity_or_manifest_shape_is_refused_with_correct_pin(bundle, changes):
    root, path, manifest, _ = bundle
    altered = dict(manifest, **changes)
    pin = write_manifest(path, altered)
    with pytest.raises(ValueError):
        verifier().verify_runtime(root, path, pin, expected_generation=GENERATION, expected_revision=REVISION)


def test_verifier_leaves_synthetic_tree_byte_identical(bundle):
    root = bundle[0]
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    run(bundle)
    after = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert after == before


@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate data streams require Windows")
@pytest.mark.parametrize("target", ["file", "directory"])
def test_named_alternate_data_stream_is_refused(bundle, target):
    path = bundle[0] / "model"
    if target == "file":
        path = path / "config.json"
    try:
        with open(str(path) + ":q4_synthetic_probe", "xb") as stream:
            stream.write(b"synthetic")
    except OSError as exc:
        pytest.skip(f"alternate streams unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError):
        run(bundle)


@pytest.mark.parametrize("target", ["manifest", "file"])
def test_read_errors_are_refused_without_leaking_os_error(bundle, monkeypatch, target):
    module = verifier()
    original = module._open_regular

    class BrokenRead:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, *args):
            raise OSError("synthetic-sensitive-path")

        def fileno(self):
            return self.stream.fileno()

    def broken_open(path, max_size):
        stream, info = original(path, max_size)
        if (target == "manifest" and path == bundle[1]) or (target == "file" and path.name == "config.json"):
            return BrokenRead(stream), info
        return stream, info

    monkeypatch.setattr(module, "_open_regular", broken_open)
    with pytest.raises(ValueError) as error:
        run(bundle)
    assert "synthetic-sensitive-path" not in str(error.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows stream enumeration")
def test_stream_enumeration_error_fails_closed(bundle, monkeypatch):
    import ctypes

    module = verifier()
    module._windows_stream_api.cache_clear()

    def unavailable(*args, **kwargs):
        raise OSError("synthetic-sensitive-path")

    monkeypatch.setattr(ctypes, "WinDLL", unavailable)
    try:
        with pytest.raises(ValueError, match="stream_unavailable") as error:
            run(bundle)
        assert "synthetic-sensitive-path" not in str(error.value)
    finally:
        module._windows_stream_api.cache_clear()
