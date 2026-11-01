"""Synthetic-only tests for assembling, not installing, a Q4 runtime candidate.

The pin set is an explicit file inventory, not proof of wheel provenance or a
complete or safe Python dependency closure. No model, package installation,
pip execution or network access is used in these tests.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from sandbox.mind.step2 import runtime_manifest


GENERATION = "a" * 40
REVISION = "b" * 40


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "source"
    files = {
        "site/example/__init__.py": b"# synthetic package\n",
        "model/config.json": b'{"synthetic":true}\n',
        "assets/policy.json": b'{"synthetic":"policy"}\n',
    }
    for name, data in files.items():
        path = source.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    pins = {name: {"size": len(data), "sha256": digest(data)} for name, data in files.items()}
    return source, tmp_path / "candidate", tmp_path / "candidate-manifest.json", files, pins


def assemble(inputs, **kwargs):
    from tools.build_q4_runtime_candidate import build_candidate

    source, candidate, manifest, _, pins = inputs
    args = {"pins": pins, "generation": GENERATION, "revision": REVISION, "max_bytes": 4096}
    args.update(kwargs)
    return build_candidate(source, candidate, manifest, **args)


def tree_snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def assert_no_output(inputs):
    assert not inputs[1].exists()
    assert not inputs[2].exists()


def test_tiny_candidate_is_separate_exact_and_manifest_verifies(inputs):
    source, candidate, manifest, files, _ = inputs
    before = tree_snapshot(source)
    result = assemble(inputs)
    assert tree_snapshot(source) == before == files
    assert tree_snapshot(candidate) == files
    manifest_bytes = manifest.read_bytes()
    assert result["manifest_sha256"] == digest(manifest_bytes)
    assert result["file_count"] == len(files)
    assert result["total_bytes"] == sum(map(len, files.values()))
    document = json.loads(manifest_bytes)
    assert {record["path"] for record in document["files"]} == set(files)
    verified = runtime_manifest.verify_runtime(
        candidate, manifest, result["manifest_sha256"],
        expected_generation=GENERATION, expected_revision=REVISION,
    )
    assert verified["file_count"] == len(files)


@pytest.mark.parametrize("mutation", ["missing", "wrong_digest", "wrong_size", "bad_digest", "empty_pins"])
def test_bad_input_pins_refuse_before_any_output_write(inputs, mutation):
    source, _, _, _, pins = inputs
    altered = {name: dict(pin) for name, pin in pins.items()}
    name = "model/config.json"
    if mutation == "missing":
        altered["model/missing.json"] = {"size": 1, "sha256": digest(b"x")}
    elif mutation == "wrong_digest":
        altered[name]["sha256"] = "0" * 64
    elif mutation == "wrong_size":
        altered[name]["size"] += 1
    elif mutation == "bad_digest":
        altered[name]["sha256"] = "not-a-sha256"
    else:
        altered = {}
    before = tree_snapshot(source)
    with pytest.raises(ValueError):
        assemble(inputs, pins=altered)
    assert_no_output(inputs)
    assert tree_snapshot(source) == before


@pytest.mark.parametrize("budget", [0, -1, 1])
def test_insufficient_budget_refuses_before_any_output_write(inputs, budget):
    before = tree_snapshot(inputs[0])
    with pytest.raises(ValueError):
        assemble(inputs, max_bytes=budget)
    assert_no_output(inputs)
    assert tree_snapshot(inputs[0]) == before


@pytest.mark.parametrize("unsafe", ["source_child", "source_ancestor", "manifest_in_source", "manifest_in_candidate"])
def test_overlapping_output_refuses_without_changes(inputs, unsafe):
    source, candidate, manifest, _, pins = inputs
    if unsafe == "source_child":
        candidate = source / "candidate"
    elif unsafe == "source_ancestor":
        candidate = source.parent
    elif unsafe == "manifest_in_source":
        manifest = source / "manifest.json"
    else:
        manifest = candidate / "manifest.json"
    from tools.build_q4_runtime_candidate import build_candidate

    before = tree_snapshot(source)
    with pytest.raises(ValueError):
        build_candidate(source, candidate, manifest, pins=pins, generation=GENERATION,
                        revision=REVISION, max_bytes=4096)
    assert tree_snapshot(source) == before
    assert not (source / "candidate").exists()
    assert not (source / "manifest.json").exists()
    assert not inputs[1].exists()
    assert not inputs[2].exists()


@pytest.mark.parametrize("relative", ["../outside", "site/../escape", "site/a:stream", "site/x\\y", "other/file"])
def test_unsafe_pinned_names_refused_without_output(inputs, relative):
    pins = dict(inputs[4])
    pins[relative] = {"size": 1, "sha256": digest(b"x")}
    with pytest.raises(ValueError):
        assemble(inputs, pins=pins)
    assert_no_output(inputs)


@pytest.mark.parametrize("target", ["candidate", "manifest"])
def test_existing_output_is_never_overwritten(inputs, target):
    _, candidate, manifest, _, _ = inputs
    if target == "candidate":
        candidate.mkdir()
        (candidate / "do-not-touch").write_bytes(b"synthetic-existing")
    else:
        manifest.write_bytes(b"synthetic-existing")
    with pytest.raises(ValueError):
        assemble(inputs)
    if target == "candidate":
        assert (candidate / "do-not-touch").read_bytes() == b"synthetic-existing"
        assert not manifest.exists()
    else:
        assert manifest.read_bytes() == b"synthetic-existing"
        assert not candidate.exists()


@pytest.mark.parametrize("kind", ["source_file", "source_directory"])
def test_source_symlinks_are_refused_before_any_output_write(inputs, tmp_path, kind):
    source, _, _, _, pins = inputs
    outside = tmp_path / "external"
    outside.mkdir()
    if kind == "source_file":
        target = outside / "source.json"
        target.write_bytes(b"synthetic-link-data")
        link = source / "model" / "linked.json"
        pins["model/linked.json"] = {"size": target.stat().st_size, "sha256": digest(target.read_bytes())}
    else:
        target = outside
        (target / "linked.json").write_bytes(b"synthetic-link-data")
        link = source / "assets" / "linked-dir"
        data = (target / "linked.json").read_bytes()
        pins["assets/linked-dir/linked.json"] = {"size": len(data), "sha256": digest(data)}
    try:
        os.symlink(target, link, target_is_directory=kind == "source_directory")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError):
        assemble(inputs, pins=pins)
    assert_no_output(inputs)


def test_source_change_during_manifest_creation_refuses_publication(inputs, monkeypatch):
    """A source change observed late must not yield a successful candidate."""
    from sandbox.mind.step2 import runtime_builder

    original = runtime_builder.build_runtime_manifest

    def change_source_before_manifest(*args, **kwargs):
        (inputs[0] / "model" / "config.json").write_bytes(b"changed-synthetic-input")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_builder, "build_runtime_manifest", change_source_before_manifest)
    with pytest.raises(ValueError):
        assemble(inputs)


def test_candidate_change_before_manifest_is_not_accepted(inputs, monkeypatch):
    """The produced manifest cannot legitimize a tampered candidate."""
    from sandbox.mind.step2 import runtime_builder

    source_before = tree_snapshot(inputs[0])
    original = runtime_builder.build_runtime_manifest

    def change_candidate_before_manifest(*args, **kwargs):
        (inputs[1] / "model" / "config.json").write_bytes(b"changed-synthetic-candidate")
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_builder, "build_runtime_manifest", change_candidate_before_manifest)
    with pytest.raises(ValueError):
        assemble(inputs)
    assert tree_snapshot(inputs[0]) == source_before
