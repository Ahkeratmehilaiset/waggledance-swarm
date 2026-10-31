"""Synthetic Q4 manifest builder tests; never load models, memory, or wheels."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from sandbox.mind.step2 import runtime_manifest

GENERATION = "a" * 40
REVISION = "b" * 40


def builder():
    from sandbox.mind.step2 import runtime_builder
    return runtime_builder


@pytest.fixture
def synthetic_tree(tmp_path):
    root = tmp_path / "runtime"
    paths = {
        "site/pkg/__init__.py": b"synthetic-code",
        "site/setuptools/script (dev).tmpl": b"synthetic-template",
        "model/config.json": b"synthetic-config",
        "assets/a.json": b"synthetic-asset",
    }
    for name, content in paths.items():
        target = root.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return root


def build(root, output, **kwargs):
    return builder().build_runtime_manifest(root, output, generation=kwargs.get("generation", GENERATION),
                                            revision=kwargs.get("revision", REVISION))


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_deterministic_manifest_is_exclusive_and_accepted_by_verifier(synthetic_tree, tmp_path):
    before = snapshot(synthetic_tree)
    first, second = tmp_path / "manifest-a.json", tmp_path / "manifest-b.json"
    result1, result2 = build(synthetic_tree, first), build(synthetic_tree, second)
    assert first.read_bytes() == second.read_bytes()
    assert result1["manifest_sha256"] == result2["manifest_sha256"] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert result1["file_count"] == 4
    report = runtime_manifest.verify_runtime(synthetic_tree, first, result1["manifest_sha256"],
                                             expected_generation=GENERATION, expected_revision=REVISION)
    assert report["file_count"] == 4 and snapshot(synthetic_tree) == before
    data = json.loads(first.read_text(encoding="utf-8"))
    assert [item["path"] for item in data["files"]] == sorted(before)


def test_existing_manifest_is_never_overwritten(synthetic_tree, tmp_path):
    output = tmp_path / "existing.json"
    output.write_bytes(b"preserve-synthetic")
    with pytest.raises(ValueError):
        build(synthetic_tree, output)
    assert output.read_bytes() == b"preserve-synthetic"


@pytest.mark.parametrize("target", ["inside", "missing_parent", "relative"])
def test_unsafe_output_location_is_refused_without_creating_it(synthetic_tree, tmp_path, target):
    output = {"inside": synthetic_tree / "manifest.json", "missing_parent": tmp_path / "missing" / "manifest.json",
              "relative": Path("relative-manifest.json")}[target]
    with pytest.raises(ValueError):
        build(synthetic_tree, output)
    assert not output.exists()


@pytest.mark.parametrize("key,bad", [("generation", "bad"), ("revision", "z" * 40)])
def test_invalid_generation_or_revision_cannot_publish(synthetic_tree, tmp_path, key, bad):
    output = tmp_path / "bad.json"
    with pytest.raises(ValueError):
        build(synthetic_tree, output, **{key: bad})
    assert not output.exists()


@pytest.mark.parametrize("kind", ["empty_directory", "unexpected_space", "root_link", "file_link", "hardlink"])
def test_invalid_tree_is_refused_before_manifest_creation(synthetic_tree, tmp_path, kind):
    output = tmp_path / "bad.json"
    root = synthetic_tree
    if kind == "empty_directory":
        (root / "assets" / "unused").mkdir()
    elif kind == "unexpected_space":
        (root / "site" / "unexpected name.py").write_bytes(b"synthetic")
    elif kind in ("root_link", "file_link"):
        link = tmp_path / "root-link" if kind == "root_link" else root / "assets" / "file-link"
        target = root if kind == "root_link" else root / "assets" / "a.json"
        try:
            os.symlink(target, link, target_is_directory=kind == "root_link")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlinks unavailable: {type(exc).__name__}")
        if kind == "root_link":
            root = link
    else:
        try:
            os.link(root / "assets" / "a.json", root / "assets" / "hardlink.json")
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"hardlinks unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError):
        build(root, output)
    assert not output.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows alternate data streams")
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_named_alternate_stream_refused_before_manifest_creation(synthetic_tree, tmp_path, kind):
    output = tmp_path / "bad.json"
    target = synthetic_tree / "model"
    if kind == "file":
        target = target / "config.json"
    try:
        with open(str(target) + ":q4_synthetic_probe", "xb") as stream:
            stream.write(b"synthetic")
    except OSError as exc:
        pytest.skip(f"alternate streams unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError):
        build(synthetic_tree, output)
    assert not output.exists()


def test_tree_change_during_hash_refuses_publication(synthetic_tree, tmp_path, monkeypatch):
    output = tmp_path / "bad.json"
    module = builder()
    original = module._hash_file
    first = True

    def mutate_after_hash(path):
        nonlocal first
        result = original(path)
        if first:
            first = False
            path.write_bytes(path.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(module, "_hash_file", mutate_after_hash)
    with pytest.raises(ValueError):
        build(synthetic_tree, output)
    assert not output.exists()


def test_case_collision_in_enumeration_refuses_before_read(synthetic_tree, tmp_path, monkeypatch):
    output = tmp_path / "bad.json"

    def conflicting_tree(root):
        return {"site/pkg/Foo.py", "site/pkg/foo.py"}, {"site", "site/pkg"}

    monkeypatch.setattr(runtime_manifest, "_tree", conflicting_tree)
    with pytest.raises(ValueError, match="case_collision"):
        build(synthetic_tree, output)
    assert not output.exists()


def test_write_failure_preserves_created_output_for_manual_inspection(synthetic_tree, tmp_path, monkeypatch):
    output = tmp_path / "write-failure.json"

    def fail_sync(fd):
        raise OSError("synthetic-write-error")

    monkeypatch.setattr(builder().os, "fsync", fail_sync)
    with pytest.raises(ValueError, match="output_unavailable") as error:
        build(synthetic_tree, output)
    assert "synthetic-write-error" not in str(error.value)
    assert output.exists()  # No automatic delete or overwrite, even on failure.


@pytest.mark.parametrize("mutation", ["same_length_content", "hardlink"])
def test_changed_output_before_return_is_refused_without_deletion(synthetic_tree, tmp_path, monkeypatch, mutation):
    output = tmp_path / "tampered-manifest.json"
    actual_fsync = os.fsync

    def tamper_during_sync(fd):
        actual_fsync(fd)
        if mutation == "same_length_content":
            with output.open("r+b") as stream:
                data = stream.read()
                stream.seek(0)
                stream.write(b"x" + data[1:])
                stream.flush()
        else:
            try:
                os.link(output, tmp_path / "synthetic-hardlink.json")
            except (OSError, NotImplementedError) as exc:
                pytest.skip(f"hardlinks unavailable: {type(exc).__name__}")

    monkeypatch.setattr(builder().os, "fsync", tamper_during_sync)
    with pytest.raises(ValueError):
        build(synthetic_tree, output)
    assert output.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows alternate data streams")
def test_output_named_stream_before_return_is_refused(synthetic_tree, tmp_path, monkeypatch):
    output = tmp_path / "stream-manifest.json"
    actual_fsync = os.fsync

    def add_stream_during_sync(fd):
        actual_fsync(fd)
        try:
            with open(str(output) + ":q4_synthetic_probe", "xb") as stream:
                stream.write(b"synthetic")
        except OSError as exc:
            pytest.skip(f"alternate streams unavailable: {type(exc).__name__}")

    monkeypatch.setattr(builder().os, "fsync", add_stream_during_sync)
    with pytest.raises(ValueError):
        build(synthetic_tree, output)
    assert output.exists()
