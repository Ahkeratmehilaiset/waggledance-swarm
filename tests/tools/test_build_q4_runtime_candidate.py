"""Synthetic-only tests for assembling, not installing, a Q4 runtime candidate.

The pin set is an explicit file inventory, not proof of wheel provenance or a
complete or safe Python dependency closure. No model, package installation,
pip execution or network access is used in these tests.
"""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

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


@pytest.mark.parametrize("relative,data", [
    ("site/example/unlisted.py", b"synthetic extra code"),
    ("model/extra.json", b""),
    ("assets/code/extra.py", b"synthetic nested code"),
])
def test_unpinned_candidate_file_before_manifest_is_not_accepted(inputs, monkeypatch, relative, data):
    """A manifest of a changed tree is not the caller's pinned file set."""
    from sandbox.mind.step2 import runtime_builder

    original = runtime_builder.build_runtime_manifest

    def add_unpinned_file_before_manifest(*args, **kwargs):
        target = inputs[1].joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_builder, "build_runtime_manifest", add_unpinned_file_before_manifest)
    with pytest.raises(ValueError):
        assemble(inputs)


def test_manifest_and_candidate_must_remain_bound_to_caller_pins(inputs, monkeypatch):
    """Matching manifest and candidate are insufficient if both differ from pins."""
    from sandbox.mind.step2 import runtime_builder
    from tools import build_q4_runtime_candidate as module

    source, candidate, _, files, _ = inputs
    target = candidate / "model" / "config.json"
    pinned = files["model/config.json"]
    alternate = b"x" * len(pinned)
    assert alternate != pinned
    original_build = runtime_builder.build_runtime_manifest
    original_hash = module._input_hash
    source_final_count = 0
    manifest_built = False

    def build_alternate_manifest(*args, **kwargs):
        nonlocal manifest_built
        target.write_bytes(alternate)
        result = original_build(*args, **kwargs)
        manifest_built = True
        target.write_bytes(pinned)
        return result

    def restore_alternate_after_final_source_hash(root, relative):
        nonlocal source_final_count
        result = original_hash(root, relative)
        if root == source and relative == "assets/policy.json":
            source_final_count += 1
            if source_final_count == 2:
                target.write_bytes(alternate)
        return result

    monkeypatch.setattr(runtime_builder, "build_runtime_manifest", build_alternate_manifest)
    monkeypatch.setattr(module, "_input_hash", restore_alternate_after_final_source_hash)
    with pytest.raises(ValueError):
        assemble(inputs)
    assert manifest_built


@pytest.fixture
def multi_inputs(tmp_path):
    files = {
        'site/example/__init__.py': b'# synthetic\n',
        'model/config.json': b'{"model":"synthetic"}\n',
        'assets/mind/reader.py': b'# synthetic reader\n',
    }
    roots = {kind: tmp_path / ('source_' + kind) for kind in ('site', 'model', 'assets')}
    for relative, data in files.items():
        kind, rest = relative.split('/', 1)
        path = roots[kind].joinpath(*rest.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    pins = {name: {'size': len(data), 'sha256': digest(data)} for name, data in files.items()}
    audit_output = tmp_path / '.codex-audit'
    audit_output.mkdir()
    return roots, audit_output / 'multi-candidate', audit_output / 'multi-manifest.json', files, pins


def assemble_multi(inputs, **kwargs):
    from tools.build_q4_runtime_candidate import build_candidate_from_sources

    roots, candidate, manifest, _, pins = inputs
    args = {'pins': pins, 'generation': GENERATION, 'revision': REVISION, 'max_bytes': 4096}
    args.update(kwargs)
    return build_candidate_from_sources(roots, candidate, manifest, **args)


def test_multi_fixture_has_explicit_audit_subdirectory(multi_inputs):
    assert multi_inputs[1].parent.name == '.codex-audit'
    assert multi_inputs[2].parent == multi_inputs[1].parent


@pytest.mark.parametrize('inside_audit', [False, True])
def test_multi_simulated_ci_temp_respects_explicit_audit_boundary(multi_inputs, monkeypatch, inside_audit):
    """Use fake CI output paths; nothing is created outside the real audit folder."""
    import shutil
    from tools import build_q4_runtime_candidate as module

    simulated = Path(multi_inputs[1].anchor) / 'ci-temp-synthetic' / 'job'
    if inside_audit:
        simulated /= '.codex-audit'
    monkeypatch.setattr(module, '_new_path', lambda path: simulated / Path(path).name)
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=3 * 1024**3))
    with pytest.raises(ValueError, match='disk_floor' if inside_audit else 'audit_root_required'):
        assemble_multi(multi_inputs)
    assert not multi_inputs[1].exists() and not multi_inputs[2].exists()


def test_multi_three_sources_make_one_exact_candidate(multi_inputs):
    roots, candidate, manifest, files, _ = multi_inputs
    original = {name: tree_snapshot(root) for name, root in roots.items()}
    result = assemble_multi(multi_inputs)
    assert tree_snapshot(candidate) == files
    assert {name: tree_snapshot(root) for name, root in roots.items()} == original
    assert result['file_count'] == 3
    assert runtime_manifest.verify_runtime(candidate, manifest, result['manifest_sha256'],
        expected_generation=GENERATION, expected_revision=REVISION)['file_count'] == 3


@pytest.mark.parametrize('fault', ['same_root', 'nested_root', 'wrong_prefix', 'missing_root',
                                   'wrong_pin', 'path_escape', 'case_collision', 'budget'])
def test_multi_rejects_bad_pins_roots_and_budget_before_output(multi_inputs, fault):
    roots, candidate, manifest, _, pins = multi_inputs
    roots = dict(roots)
    pins = {path: dict(pin) for path, pin in pins.items()}
    args = {'pins': pins, 'generation': GENERATION, 'revision': REVISION, 'max_bytes': 4096}
    if fault == 'same_root':
        roots['model'] = roots['site']
    elif fault == 'nested_root':
        roots['model'] = roots['site'] / 'example'
    elif fault == 'wrong_prefix':
        pins['other/x'] = {'size': 1, 'sha256': digest(b'x')}
    elif fault == 'missing_root':
        del roots['assets']
    elif fault == 'wrong_pin':
        pins['model/config.json']['sha256'] = '0' * 64
    elif fault == 'path_escape':
        pins['model/../escape'] = {'size': 1, 'sha256': digest(b'x')}
    elif fault == 'case_collision':
        pins['model/Config.json'] = dict(pins['model/config.json'])
    else:
        args['max_bytes'] = 1
    from tools.build_q4_runtime_candidate import build_candidate_from_sources
    with pytest.raises(ValueError):
        build_candidate_from_sources(roots, candidate, manifest, **args)
    assert not candidate.exists() and not manifest.exists()


def _multi_overlap_sources(multi_inputs):
    roots, candidate, _, files, _ = multi_inputs
    roots = dict(roots)
    roots['site'] = candidate.parent / 'source_site'
    module = roots['site'] / 'example' / '__init__.py'
    module.parent.mkdir(parents=True)
    module.write_bytes(files['site/example/__init__.py'])
    return roots


def test_multi_overlap_source_is_explicitly_audit_bound(multi_inputs):
    roots = _multi_overlap_sources(multi_inputs)
    assert roots['site'] == multi_inputs[1].parent / 'source_site'
    assert roots['site'].parent.name == '.codex-audit'
    assert (roots['site'] / 'example' / '__init__.py').read_bytes() == multi_inputs[3]['site/example/__init__.py']


def test_multi_rejects_source_output_overlap_and_existing_target(multi_inputs):
    from tools.build_q4_runtime_candidate import build_candidate_from_sources

    roots, candidate, manifest, _, pins = multi_inputs
    overlap_roots = _multi_overlap_sources(multi_inputs)
    with pytest.raises(ValueError, match='^output_overlap$'):
        build_candidate_from_sources(overlap_roots, overlap_roots['site'] / 'nested-candidate', manifest,
            pins=pins, generation=GENERATION, revision=REVISION, max_bytes=4096)
    candidate.mkdir()
    marker = candidate / 'existing'
    marker.write_bytes(b'keep')
    with pytest.raises(ValueError, match='^output_exists$'):
        assemble_multi(multi_inputs)
    assert marker.read_bytes() == b'keep' and not manifest.exists()


def test_multi_low_disk_refuses_before_output(multi_inputs, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=3 * 1024**3))
    with pytest.raises(ValueError, match='disk_floor'):
        assemble_multi(multi_inputs)
    assert not multi_inputs[1].exists() and not multi_inputs[2].exists()


@pytest.mark.parametrize('route', ['single', 'multi'])
def test_copy_stops_before_next_block_would_cross_disk_floor(route, request, monkeypatch):
    import shutil

    values = request.getfixturevalue('inputs' if route == 'single' else 'multi_inputs')
    source, candidate, manifest, files, pins = values
    data = b'x' * (1024 * 1024 + 9)
    source_file = ((source / 'site' / 'large.bin') if route == 'single'
                   else (source['site'] / 'large.bin'))
    source_file.write_bytes(data)
    pins['site/large.bin'] = {'size': len(data), 'sha256': digest(data)}
    partial = candidate / 'site' / 'large.bin'
    def sample(_):
        # Preflight and first chunk pass. Once the first chunk reaches the
        # synthetic file, only eight bytes remain above the 3 GiB floor.
        free = (3 * 1024**3 + 8 if partial.exists() and partial.stat().st_size >= 1024 * 1024
                else 20 * 1024**3)
        return SimpleNamespace(free=free)
    monkeypatch.setattr(shutil, 'disk_usage', sample)
    with pytest.raises(ValueError, match='disk_floor'):
        (assemble(values, max_bytes=len(data) + 4096) if route == 'single'
         else assemble_multi(values, max_bytes=len(data) + 4096))
    assert candidate.is_dir() and not manifest.exists()
    assert partial.read_bytes() == b'x' * (1024 * 1024)
    assert (candidate / 'site' / 'example' / '__init__.py').read_bytes() == files['site/example/__init__.py']


def test_multi_source_changes_between_preflight_and_copy_refused(multi_inputs, monkeypatch):
    original = runtime_manifest._open_regular
    path = multi_inputs[0]['model'] / 'config.json'
    count = 0
    def alter_before_copy(filename, limit):
        nonlocal count
        if Path(filename) == path:
            count += 1
            if count == 2:
                path.write_bytes(b'changed synthetic data')
        return original(filename, limit)
    monkeypatch.setattr(runtime_manifest, '_open_regular', alter_before_copy)
    with pytest.raises(ValueError):
        assemble_multi(multi_inputs)
    assert count == 2
    assert not multi_inputs[2].exists()


def test_multi_source_changes_while_copy_reads_refused(multi_inputs, monkeypatch):
    original = runtime_manifest._open_regular
    path = multi_inputs[0]['model'] / 'config.json'
    count = 0
    changed = False

    class ChangeDuringRead:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            self.handle.__enter__()
            return self
        def __exit__(self, *args):
            return self.handle.__exit__(*args)
        def fileno(self):
            return self.handle.fileno()
        def read(self, size=-1):
            nonlocal changed
            block = self.handle.read(size)
            if block and not changed:
                info = path.stat()
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000_000))
                changed = True
            return block

    def altered_stream(filename, limit):
        nonlocal count
        stream, identity = original(filename, limit)
        if Path(filename) == path:
            count += 1
            if count == 2:
                return ChangeDuringRead(stream), identity
        return stream, identity

    monkeypatch.setattr(runtime_manifest, '_open_regular', altered_stream)
    with pytest.raises(ValueError, match='input_changed'):
        assemble_multi(multi_inputs)
    assert changed and not multi_inputs[2].exists()


def test_multi_extra_file_before_manifest_refused(multi_inputs, monkeypatch):
    from sandbox.mind.step2 import runtime_builder
    original = runtime_builder.build_runtime_manifest
    def add_extra(*args, **kwargs):
        (multi_inputs[1] / 'assets' / 'extra.py').write_bytes(b'extra')
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime_builder, 'build_runtime_manifest', add_extra)
    with pytest.raises(ValueError):
        assemble_multi(multi_inputs)


def test_multi_manifest_replacement_refused(multi_inputs, monkeypatch):
    from sandbox.mind.step2 import runtime_builder
    original = runtime_builder.build_runtime_manifest
    def change_manifest(*args, **kwargs):
        result = original(*args, **kwargs)
        multi_inputs[2].write_bytes(b'{}')
        return result
    monkeypatch.setattr(runtime_builder, 'build_runtime_manifest', change_manifest)
    with pytest.raises(ValueError):
        assemble_multi(multi_inputs)
