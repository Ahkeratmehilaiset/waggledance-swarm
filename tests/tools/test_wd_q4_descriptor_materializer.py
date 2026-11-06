"""Synthetic bytes only: the default-off materializer that writes ONE v2 shared-descriptor generation to disk.

No launcher, no live data, no runtime. Everything is written under pytest's tmp_path. The integration proof
builds a fixed plan with ``tools.wd_q4_fixed_plan_producer`` and a descriptor with
``tools.wd_q4_shared_descriptor_producer`` on the planned paths, materializes both, and then has the EXISTING
launcher validator ``Get-WdQ4SharedSessionConfig.ps1`` and the EXISTING caller reader accept the files from disk
with the returned pin. A matching hash is not authentication, freshness or a runtime proof.
"""
import base64
import builtins
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/windows/reboot/Get-WdQ4SharedSessionConfig.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
needs_shell = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")
needs_windows = pytest.mark.skipif(os.name != "nt", reason="drive-letter paths are Windows only")
LANES = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]
SCHEMA = "wd.q4-shared-session-config.v2"
ARTIFACTS = ("binding", "fixed_plan", "snapshot")
SENTINEL = "sentinelvalue7f3a"
GENERATION = "d" * 40
OUTER_KEYS = ("outer-path", "outer-sha256", "runtime-root", "runtime-generation", "runtime-revision",
              "verifier-sha256", "public-outer-sha256", "public-child-sha256", "public-query-sha256",
              "worker-sha256")
BINDING_KEYS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
                "public_policy_sha256", "descriptor_sha256", "projection_sha256")
REASONS = {"disabled", "path_refused", "generation_refused", "exists_refused", "artifact_refused",
           "descriptor_refused", "pin_refused", "write_refused"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def module():
    from tools import wd_q4_descriptor_materializer
    return wd_q4_descriptor_materializer


def descriptor_module():
    from tools import wd_q4_shared_descriptor_producer
    return wd_q4_shared_descriptor_producer


def fixed_plan_module():
    from tools import wd_q4_fixed_plan_producer
    return wd_q4_fixed_plan_producer


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def fleet_bytes():
    return json.dumps({"lanes": [{"agent": x} for x in LANES if x != "codex-tools-1"],
                       "tools_supervisor": {"agent": "codex-tools-1"}, "other": {"kept": True}}).encode()


def artifacts(**changes):
    out = {"binding": canonical({"schema": "wd.q4-lane-binding.v1", "lane": "fable-5"}),
           "fixed_plan": canonical({"schema": "wd.q4-fixed-plan.v2", "approved_tasks": ["task-a"]}),
           "snapshot": canonical({"schema": "wd.q4-public-snapshot.v1", "records": 0})}
    out.update(changes)
    return out


def descriptor_kwargs(tmp_path, plan, files):
    """The descriptor producer's own synthetic inputs, with the three materialized artifacts on the planned paths."""
    fleet = fleet_bytes()
    root = tmp_path / "runtime"
    out = {"fleet_bytes": fleet, "expected_fleet_sha256": sha(fleet), "lanes": list(LANES),
           "bundle_root": str(tmp_path / "bundle"), "public_plan": str(tmp_path / "public.json"),
           "interpreter": str(tmp_path / "python.exe"), "interpreter_sha256": "a" * 64,
           "interpreter_max_bytes": 10000, "cwd": str(tmp_path / "code"), "timeout_seconds": 30,
           "max_output_bytes": 32000,
           "runtime_manifest_path": str(tmp_path / "runtime_manifest.json"), "runtime_manifest_sha256": "9" * 64,
           "outer": {"outer-path": str(root / "assets" / "code" / "tools" / "wd_q4_session_outer_synthetic.py"),
                     "outer-sha256": "c" * 64, "runtime-root": str(root), "runtime-generation": GENERATION,
                     "runtime-revision": "e" * 64, "verifier-sha256": "1" * 64, "public-outer-sha256": "2" * 64,
                     "public-child-sha256": "3" * 64, "public-query-sha256": "4" * 64, "worker-sha256": "5" * 64},
           "expected_binding": {key: format(i + 1, "064x") for i, key in enumerate(BINDING_KEYS)}}
    for name in ARTIFACTS:
        out[name + "_path"] = plan[name + "_path"]
        out[name + "_sha256"] = sha(files[name])
    return out


def produced(tmp_path, files=None, target=None, generation=GENERATION, create=True):
    """Plan the paths, build a real producer descriptor on them, return (plan, kwargs, files, descriptor)."""
    files = files if files is not None else artifacts()
    target = target if target is not None else tmp_path / "generations"
    if create:
        target.mkdir(exist_ok=True)
    plan = module().plan_generation_paths(str(target), generation)
    kw = descriptor_kwargs(tmp_path, plan, files)
    result = descriptor_module().build_shared_descriptor(enabled=True, **kw)
    return plan, kw, files, result


def materialize(tmp_path, files=None, target=None, generation=GENERATION, **changes):
    plan, kw, files, result = produced(tmp_path, files=files, target=target, generation=generation)
    call = {"enabled": True, "target_dir": str(target if target is not None else tmp_path / "generations"),
            "generation": generation, "artifacts": files, "descriptor_bytes": result["descriptor_bytes"],
            "expected_descriptor_sha256": result["descriptor_sha256"]}
    call.update(changes)
    return plan, kw, files, result, module().materialize_descriptor(**call)


def refused(reason, call):
    m = module()
    with pytest.raises(m.MaterializeRefused) as caught:
        call()
    assert caught.value.args == (reason,) and reason in REASONS
    text = repr(caught.value) + str(caught.value) + repr(caught.value.__dict__)
    assert SENTINEL not in text and "\\" not in text.replace("\\\\", "")
    return caught.value


def tree(path):
    return sorted(str(p.relative_to(path)) for p in Path(path).rglob("*"))


# 1 off by default and the exact surface ------------------------------------------------------------------------
def test_disabled_returns_off_without_touching_anything(tmp_path, monkeypatch):
    m = module()

    def blocked(*args, **kwargs):
        raise AssertionError("I/O attempted while disabled")
    for owner, name in ((builtins, "open"), (os, "open"), (os, "mkdir"), (os, "stat"), (os, "lstat"),
                        (os, "replace"), (os, "scandir")):
        monkeypatch.setattr(owner, name, blocked)
    out = m.materialize_descriptor(target_dir="nonsense", generation="bad", artifacts="bad",
                                   descriptor_bytes=b"bad", expected_descriptor_sha256="bad")
    monkeypatch.undo()
    assert out == {"status": "OFF"}


@pytest.mark.parametrize("enabled", [1, "true", "1", [True], None])
def test_only_the_exact_true_enables(tmp_path, enabled):
    plan, kw, files, result = produced(tmp_path)
    out = module().materialize_descriptor(enabled=enabled, target_dir=str(tmp_path / "generations"),
                                          generation=GENERATION, artifacts=files,
                                          descriptor_bytes=result["descriptor_bytes"],
                                          expected_descriptor_sha256=result["descriptor_sha256"])
    assert out == {"status": "OFF"}
    assert tree(tmp_path / "generations") == []


def test_the_surface_is_keyword_only_and_closed():
    m = module()
    sig = inspect.signature(m.materialize_descriptor)
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values())
    assert set(sig.parameters) == {"enabled", "target_dir", "generation", "artifacts", "descriptor_bytes",
                                   "expected_descriptor_sha256"}
    assert sig.parameters["enabled"].default is False
    assert m.SCHEMA == SCHEMA and tuple(m.ARTIFACTS) == ARTIFACTS and set(m.REASONS) == REASONS
    assert m.LIMITS["descriptor"] == 65536 and m.LIMITS["fixed_plan"] == 4096
    assert m.LIMITS["snapshot"] == 16 * 1024 * 1024 and m.LIMITS["binding"] == 65536
    assert issubclass(m.MaterializeRefused, Exception)


# 2 path planning is pure and deterministic ----------------------------------------------------------------------
@needs_windows
def test_planning_is_pure_deterministic_and_stays_under_the_target(tmp_path, monkeypatch):
    m = module()

    def blocked(*args, **kwargs):
        raise AssertionError("I/O attempted while planning")
    for owner, name in ((builtins, "open"), (os, "stat"), (os, "lstat"), (os, "scandir"), (os, "mkdir")):
        monkeypatch.setattr(owner, name, blocked)
    target = r"C:\synthetic\generations"
    first = m.plan_generation_paths(target, GENERATION)
    second = m.plan_generation_paths(target, GENERATION)
    monkeypatch.undo()
    assert first == second
    assert set(first) == {"generation_dir", "binding_path", "fixed_plan_path", "snapshot_path", "descriptor_path"}
    assert first["generation_dir"] == target + "\\" + GENERATION
    for key in ("binding_path", "fixed_plan_path", "snapshot_path", "descriptor_path"):
        head, _, name = first[key].rpartition("\\")
        assert head == first["generation_dir"] and name and "\\" not in name and "/" not in name
        assert name.endswith(".json") and name.isascii() and name == name.strip()
    assert len({first[k] for k in first}) == 5


@needs_windows
@pytest.mark.parametrize("target", [
    r"relative\dir", r"\\server\share\dir", r"C:/forward/slash", r"C:\dir/mixed", r"C:\dir\sub/", "C:\\",
    r"C:\dir\..\other", r"C:\dir\.",
    r"C:\dir\NUL", r"C:\dir\COM1.txt", r"C:\dir\trailing.", r"C:\dir\trailing ", "C:\\dir\\bad\x01name",
    "C:\\dir\\unicode\u00e9", r"C:\dir\a<b", "C:\\dir\\" + "x" * 600, "", None, 7, b"C:\\dir",
])
def test_planning_refuses_every_unsafe_target(target):
    refused("path_refused", lambda: module().plan_generation_paths(target, GENERATION))


@needs_windows
@pytest.mark.parametrize("generation", ["", "D" * 40, "g" * 40, "d" * 39, "d" * 41, "d" * 63, "..", "d" * 40 + "\\",
                                        SENTINEL, None, 5, b"d" * 40])
def test_planning_refuses_a_generation_that_is_not_a_lowercase_hex_identity(generation):
    refused("generation_refused", lambda: module().plan_generation_paths(r"C:\synthetic\generations", generation))


@needs_windows
def test_planning_accepts_a_sha1_or_sha256_generation():
    for generation in ("a" * 40, "b" * 64):
        plan = module().plan_generation_paths(r"C:\synthetic\generations", generation)
        assert plan["generation_dir"].endswith("\\" + generation)


# 3 the happy path writes exactly the planned files ---------------------------------------------------------------
@needs_windows
def test_materializes_every_planned_file_with_matching_bytes_and_pins(tmp_path):
    plan, kw, files, result, out = materialize(tmp_path)
    assert out["status"] == "MATERIALIZED"
    assert out["generation_dir"] == plan["generation_dir"]
    assert out["descriptor_path"] == plan["descriptor_path"]
    assert out["descriptor_sha256"] == result["descriptor_sha256"]
    assert set(out["files"]) == set(ARTIFACTS) | {"descriptor"}
    for name in ARTIFACTS:
        entry = out["files"][name]
        assert entry["path"] == plan[name + "_path"]
        assert Path(entry["path"]).read_bytes() == files[name]
        assert entry["sha256"] == sha(files[name]) and entry["bytes"] == len(files[name])
    assert Path(out["descriptor_path"]).read_bytes() == result["descriptor_bytes"]
    assert sha(Path(out["descriptor_path"]).read_bytes()) == out["descriptor_sha256"]
    assert set(out) == {"status", "generation_dir", "descriptor_path", "descriptor_sha256", "files"}


@needs_windows
def test_only_the_four_final_files_remain_no_temp_leftovers(tmp_path):
    plan, kw, files, result, out = materialize(tmp_path)
    names = tree(plan["generation_dir"])
    assert len(names) == 4 and all(n.endswith(".json") for n in names)
    assert tree(tmp_path / "generations") == [GENERATION] + [GENERATION + "\\" + n for n in names]


@needs_windows
def test_the_descriptor_on_disk_names_the_files_beside_it(tmp_path):
    plan, kw, files, result, out = materialize(tmp_path)
    document = json.loads(Path(out["descriptor_path"]).read_bytes())
    for name in ARTIFACTS:
        assert document[name + "_path"] == out["files"][name]["path"]
        assert document[name + "_sha256"] == out["files"][name]["sha256"]
        assert Path(document[name + "_path"]).parent == Path(out["descriptor_path"]).parent


@needs_windows
def test_the_caller_inputs_are_not_mutated(tmp_path):
    files = artifacts()
    before = {k: bytes(v) for k, v in files.items()}
    plan, kw, files2, result, out = materialize(tmp_path, files=files)
    assert files == before and files is files2


@needs_windows
def test_a_large_snapshot_up_to_the_limit_is_accepted(tmp_path):
    big = b"{" + b'"schema":"wd.q4-public-snapshot.v1","pad":"' + b"x" * (16 * 1024 * 1024 - 60) + b'"}'
    assert len(big) <= 16 * 1024 * 1024
    plan, kw, files, result, out = materialize(tmp_path, files=artifacts(snapshot=big))
    assert out["files"]["snapshot"]["bytes"] == len(big)


# 4 refusals: never overwrite, never delete, never follow reparse points -------------------------------------------
@needs_windows
def test_an_existing_generation_directory_refuses_and_is_left_untouched(tmp_path):
    target = tmp_path / "generations"
    existing = target / GENERATION
    existing.mkdir(parents=True)
    keep = existing / "keep.txt"
    keep.write_bytes(b"operator data " + SENTINEL.encode())
    refused("exists_refused", lambda: materialize(tmp_path, target=target))
    assert tree(target) == [GENERATION, GENERATION + "\\keep.txt"]
    assert keep.read_bytes() == b"operator data " + SENTINEL.encode()


@needs_windows
def test_an_existing_generation_file_refuses(tmp_path):
    target = tmp_path / "generations"
    target.mkdir()
    (target / GENERATION).write_bytes(b"a file where the directory would go")
    refused("exists_refused", lambda: materialize(tmp_path, target=target))
    assert (target / GENERATION).read_bytes() == b"a file where the directory would go"


@needs_windows
def test_a_missing_target_directory_refuses_and_is_not_created(tmp_path):
    missing = tmp_path / "absent" / "generations"
    plan, kw, files, result = produced(tmp_path, target=missing, create=False)
    refused("path_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(missing), generation=GENERATION, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    assert not (tmp_path / "absent").exists()


@needs_windows
def test_a_target_that_is_a_file_refuses(tmp_path):
    target = tmp_path / "generations"
    target.write_bytes(b"not a directory")
    plan, kw, files, result = produced(tmp_path, target=target, create=False)
    refused("path_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(target), generation=GENERATION, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))


@needs_windows
def test_a_reparse_point_anywhere_on_the_target_chain_refuses(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    try:
        os.symlink(str(real), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    plan, kw, files, result = produced(tmp_path, target=link / "generations")
    (link / "generations").mkdir(exist_ok=True)
    refused("path_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(link / "generations"), generation=GENERATION, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    assert tree(real) == ["generations"]


# 5 refusals: artifacts and descriptor must agree byte for byte ---------------------------------------------------
@needs_windows
@pytest.mark.parametrize("bad", [
    {}, {"binding": b"{}"}, {"binding": b"{}", "fixed_plan": b"{}", "snapshot": b"{}", "extra": b"{}"},
    {"binding": "{}", "fixed_plan": b"{}", "snapshot": b"{}"}, {"binding": b"", "fixed_plan": b"{}", "snapshot": b"{}"},
    [("binding", b"{}")], None, "binding",
])
def test_artifacts_must_be_exactly_the_three_named_non_empty_byte_strings(tmp_path, bad):
    plan, kw, files, result = produced(tmp_path)
    refused("artifact_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=bad,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    assert tree(tmp_path / "generations") == []


@needs_windows
@pytest.mark.parametrize("name, limit", [("binding", 65536), ("fixed_plan", 4096), ("snapshot", 16 * 1024 * 1024)])
def test_an_artifact_over_its_limit_refuses_before_any_write(tmp_path, name, limit):
    files = artifacts(**{name: b"x" * (limit + 1)})
    plan, kw, files, result = produced(tmp_path, files=files)
    refused("artifact_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    assert tree(tmp_path / "generations") == []


@needs_windows
def test_a_changed_artifact_byte_no_longer_matches_the_descriptor_pin(tmp_path):
    plan, kw, files, result = produced(tmp_path)
    changed = dict(files, snapshot=files["snapshot"][:-1] + b" ")
    refused("pin_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=changed,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    assert tree(tmp_path / "generations") == []


@needs_windows
@pytest.mark.parametrize("pin", ["", "A" * 64, "a" * 63, "a" * 65, None, 7, SENTINEL])
def test_a_malformed_or_wrong_descriptor_pin_refuses(tmp_path, pin):
    plan, kw, files, result = produced(tmp_path)
    expected = pin if pin is not None and pin != SENTINEL else ("f" * 64 if pin == SENTINEL else pin)
    refused("pin_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=expected))
    assert tree(tmp_path / "generations") == []


@needs_windows
def test_a_descriptor_whose_own_pin_is_right_but_bytes_are_not_canonical_refuses(tmp_path):
    plan, kw, files, result = produced(tmp_path)
    document = json.loads(result["descriptor_bytes"])
    pretty = json.dumps(document, indent=1).encode("ascii")
    refused("descriptor_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=files,
        descriptor_bytes=pretty, expected_descriptor_sha256=sha(pretty)))
    assert tree(tmp_path / "generations") == []


def _edit(change):
    def apply(document):
        document = json.loads(json.dumps(document))
        change(document)
        return document
    return apply


DESCRIPTOR_CASES = [
    ("descriptor_refused", _edit(lambda d: d.__setitem__("schema", "wd.q4-shared-session-config.v1"))),
    ("descriptor_refused", _edit(lambda d: d.pop("snapshot_path"))),
    ("descriptor_refused", _edit(lambda d: d.__setitem__("extra", "x"))),
    ("descriptor_refused", _edit(lambda d: d.__setitem__("fixed_plan_path", d["fixed_plan_path"].lower()))),
    ("descriptor_refused", _edit(lambda d: d.__setitem__("binding_path", d["binding_path"] + " "))),
    ("descriptor_refused", _edit(lambda d: d.__setitem__("snapshot_path", d["fixed_plan_path"]))),
    ("descriptor_refused", _edit(lambda d: d.__setitem__("binding_path", SENTINEL))),
    ("pin_refused", _edit(lambda d: d.__setitem__("snapshot_sha256", "0" * 64))),
    ("pin_refused", _edit(lambda d: d.__setitem__("fixed_plan_sha256", d["snapshot_sha256"]))),
]


@needs_windows
@pytest.mark.parametrize("reason, change", DESCRIPTOR_CASES)
def test_a_descriptor_that_does_not_name_the_planned_paths_and_supplied_bytes_refuses(tmp_path, reason, change):
    plan, kw, files, result = produced(tmp_path)
    document = change(json.loads(result["descriptor_bytes"]))
    raw = canonical(document)
    refused(reason, lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=files,
        descriptor_bytes=raw, expected_descriptor_sha256=sha(raw)))
    assert tree(tmp_path / "generations") == []


@needs_windows
@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b'"x"', b"{", b'{"a":1,"a":2}', b"\xef\xbb\xbf{}", b"{}" + b" " * 70000],
                         ids=["empty", "list", "null", "string", "truncated", "duplicate-key", "bom", "oversized"])
def test_bytes_that_are_not_one_strict_json_object_refuse(tmp_path, raw):
    plan, kw, files, result = produced(tmp_path)
    reason = "descriptor_refused"
    refused(reason, lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=files,
        descriptor_bytes=raw, expected_descriptor_sha256=sha(raw)))
    assert tree(tmp_path / "generations") == []


@needs_windows
def test_a_descriptor_planned_for_another_generation_or_target_refuses(tmp_path):
    plan, kw, files, result = produced(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    refused("descriptor_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(other), generation=GENERATION, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    refused("descriptor_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation="e" * 40, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    assert tree(other) == [] and tree(tmp_path / "generations") == []


# 6 write discipline: exclusive, verified, nothing deleted ----------------------------------------------------------
@needs_windows
def test_every_file_is_created_exclusively_at_its_final_path(tmp_path, monkeypatch):
    opened = []
    real_open = os.open

    def spy_open(path, flags, *args, **kwargs):
        opened.append((str(path), flags))
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", spy_open)
    plan, kw, files, result, out = materialize(tmp_path)
    creates = [(p, f) for p, f in opened if f & os.O_CREAT]
    assert len(creates) == 4 and all(f & os.O_EXCL and f & os.O_WRONLY for _, f in creates)
    assert all(p.startswith(plan["generation_dir"] + "\\") for p, _ in creates)
    assert [p for p, _ in creates] == [plan[n + "_path"] for n in ARTIFACTS] + [plan["descriptor_path"]]
    reads = [p for p, f in opened if not f & os.O_CREAT]
    assert reads == [p for p, _ in creates]  # each final file is re-read right after it is created


@needs_windows
def test_the_descriptor_is_written_last_after_the_artifacts_it_pins(tmp_path, monkeypatch):
    order = []
    real_open = os.open

    def spy_open(path, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            order.append(str(path))
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", spy_open)
    plan, kw, files, result, out = materialize(tmp_path)
    assert order[-1] == plan["descriptor_path"] and len(order) == 4


@needs_windows
def test_a_failed_write_leaves_no_descriptor_and_deletes_nothing(tmp_path, monkeypatch):
    plan, kw, files, result = produced(tmp_path)
    real_open = os.open
    calls = {"n": 0}

    def failing_open(path, flags, *args, **kwargs):
        if flags & os.O_CREAT:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(5, "injected " + SENTINEL)
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", failing_open)
    refused("write_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    assert not Path(plan["descriptor_path"]).exists()
    assert Path(plan["binding_path"]).read_bytes() == files["binding"]  # the first artifact is kept for inspection
    monkeypatch.undo()
    refused("exists_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))


@needs_windows
def test_bytes_that_read_back_differently_refuse_instead_of_reporting_success(tmp_path, monkeypatch):
    plan, kw, files, result = produced(tmp_path)
    real_fsync = os.fsync

    def corrupt_fsync(handle):
        real_fsync(handle)
        if os.fstat(handle).st_size == len(files["snapshot"]):
            with open(plan["snapshot_path"], "r+b") as other:
                other.seek(0)
                other.write(b" ")
    monkeypatch.setattr(os, "fsync", corrupt_fsync)
    refused("write_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=GENERATION, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    assert not Path(plan["descriptor_path"]).exists()


@needs_windows
def test_refusals_never_carry_paths_bytes_or_caller_values(tmp_path):
    target = tmp_path / ("generations-" + SENTINEL)
    target.mkdir()
    files = artifacts(snapshot=canonical({"schema": "wd.q4-public-snapshot.v1", "leak": SENTINEL}))
    plan, kw, files, result = produced(tmp_path, files=files, target=target)
    changed = dict(files, snapshot=files["snapshot"] + b" ")
    caught = refused("pin_refused", lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(target), generation=GENERATION, artifacts=changed,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"]))
    assert caught.__cause__ is None and caught.__context__ is None


@needs_windows
def test_no_write_happens_outside_the_generation_directory(tmp_path, monkeypatch):
    touched = []
    real_open = os.open

    def spy_open(path, flags, *args, **kwargs):
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
            touched.append(str(path))
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(builtins, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("builtins.open used")))
    plan, kw, files, result, out = materialize(tmp_path)
    assert touched and all(p.startswith(plan["generation_dir"] + "\\") for p in touched)


def test_the_module_imports_nothing_that_could_log_or_reach_out():
    source = inspect.getsource(module())
    for token in ("import logging", "print(", "subprocess", "urllib", "socket", "os.environ", "import sys",
                  "getpass", "tempfile", "shutil", "os.remove", "os.unlink", "rmtree", "rmdir"):
        assert token not in source, token


# 7 integration proof: the existing launcher validator and caller reader accept the files from disk --------------
def validate_from_disk(tmp_path, kw, descriptor_path, descriptor_pin, lane="fable-5"):
    fleet = tmp_path / "fleet.json"
    fleet.write_bytes(kw["fleet_bytes"])
    script = ("$ErrorActionPreference='Stop'\ntry {\n"
              f". {quote(HELPER)}\n"
              f"$v=Get-WdQ4SharedSessionConfig -FleetPath {quote(fleet)} -FleetSha256 {quote(sha(kw['fleet_bytes']))} "
              f"-DescriptorPath {quote(descriptor_path)} -DescriptorSha256 {quote(descriptor_pin)} "
              f"-Lane {quote(lane)} -Enabled\n"
              "[ordered]@{ok=$true;value=$v}|ConvertTo-Json -Depth 8 -Compress\n"
              "} catch { [ordered]@{ok=$false;reason=$_.Exception.Message}|ConvertTo-Json -Compress }\n")
    code = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    process = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", code],
                             capture_output=True, text=True, timeout=120)
    assert process.returncode == 0, (process.stdout, process.stderr)
    return json.loads(process.stdout)


def real_fixed_plan(tmp_path):
    m = fixed_plan_module()
    pins = {name: format(index + 1, "x") * 64 for index, name in enumerate(m.PINS)}
    plan = m.build_fixed_plan(enabled=True, **pins, runtime_generation=GENERATION, runtime_revision="b" * 64,
                              runtime_root=str(tmp_path / "runtime"),
                              runtime_manifest_path=str(tmp_path / "runtime_manifest.json"),
                              bundle_bytes=102400, approved_tasks=["task-a", "task-b"])
    assert set(plan) == {"plan_bytes", "plan_sha256"}
    return plan


@needs_shell
@pytest.mark.parametrize("lane", LANES)
def test_the_launcher_validator_accepts_the_materialized_descriptor_from_disk(tmp_path, lane):
    fixed = real_fixed_plan(tmp_path)
    files = artifacts(fixed_plan=fixed["plan_bytes"])
    plan, kw, files, result, out = materialize(tmp_path, files=files)
    assert out["files"]["fixed_plan"]["sha256"] == fixed["plan_sha256"]
    checked = validate_from_disk(tmp_path, kw, out["descriptor_path"], out["descriptor_sha256"], lane=lane)
    assert checked["ok"], checked
    value = checked["value"]
    assert value["lane"] == lane
    assert value["worker"]["snapshot"] == out["files"]["snapshot"]["path"]
    assert value["worker"]["snapshot-sha256"] == out["files"]["snapshot"]["sha256"]
    assert value["worker"]["fixed-plan"] == out["files"]["fixed_plan"]["path"]
    assert value["worker"]["fixed-plan-sha256"] == fixed["plan_sha256"]
    assert value["worker"]["binding"] == out["files"]["binding"]["path"]
    assert "query" not in value


@needs_shell
def test_the_launcher_validator_refuses_the_materialized_descriptor_under_a_wrong_pin(tmp_path):
    plan, kw, files, result, out = materialize(tmp_path)
    checked = validate_from_disk(tmp_path, kw, out["descriptor_path"], "0" * 64)
    assert checked["ok"] is False and "refused" in checked["reason"] and "value" not in checked


@needs_windows
def test_the_caller_reader_accepts_every_materialized_file_with_its_pin(tmp_path):
    from tools import wd_q4_lane_caller_cli as caller
    fixed = real_fixed_plan(tmp_path)
    plan, kw, files, result, out = materialize(tmp_path, files=artifacts(fixed_plan=fixed["plan_bytes"]))
    assert caller._read(out["descriptor_path"], 65536, out["descriptor_sha256"]) == result["descriptor_bytes"]
    for name in ARTIFACTS:
        entry = out["files"][name]
        assert caller._read(entry["path"], 16 * 1024 * 1024, entry["sha256"]) == files[name]
    with pytest.raises(Exception):
        caller._read(out["descriptor_path"], 65536, "0" * 64)


# 8 documentation: what this is and is not ---------------------------------------------------------------------------
def test_the_docstring_states_the_boundaries():
    doc = module().__doc__
    for phrase in ("default-off", "never overwrites", "never deletes", "O_EXCL", "no os.replace", "re-read", "identity",
                   "before every write",
                   "not authentication", "not freshness", "not a runtime proof", "metadata-only",
                   "snapshot producer", "closed reason", "never a caller value", "reparse",
                   "wd_q4_shared_descriptor_producer.py", "Get-WdQ4SharedSessionConfig.ps1",
                   "WD_Q4_SHARED_DESCRIPTOR_PATH", "runtime manifest", "no scheduler", "no activation"):
        assert phrase in doc, phrase


# 9 RCO2 M1/M2: foreign final files and directory swaps after the checks ---------------------------------------------
FOREIGN = b'{"foreign": "' + SENTINEL + b'"}' if False else ('{"foreign": "' + SENTINEL + '"}').encode()


def _after_mkdir(monkeypatch, hook):
    real_mkdir = os.mkdir

    def planted_mkdir(path, *args, **kwargs):
        real_mkdir(path, *args, **kwargs)
        hook(str(path))
    monkeypatch.setattr(os, "mkdir", planted_mkdir)


def _call(tmp_path, files, result, generation=GENERATION):
    return lambda: module().materialize_descriptor(
        enabled=True, target_dir=str(tmp_path / "generations"), generation=generation, artifacts=files,
        descriptor_bytes=result["descriptor_bytes"], expected_descriptor_sha256=result["descriptor_sha256"])


@needs_windows
@pytest.mark.parametrize("victim", ["binding", "snapshot", "descriptor"])
def test_a_foreign_final_file_planted_after_mkdir_keeps_its_bytes_and_no_descriptor_is_accepted(tmp_path, monkeypatch, victim):
    plan, kw, files, result = produced(tmp_path)
    _after_mkdir(monkeypatch, lambda _gen: Path(plan[victim + "_path"]).write_bytes(FOREIGN))
    refused("write_refused", _call(tmp_path, files, result))
    assert Path(plan[victim + "_path"]).read_bytes() == FOREIGN
    if victim != "descriptor":
        assert not Path(plan["descriptor_path"]).exists()
    assert not any(p.endswith(".part") for p in tree(tmp_path / "generations"))


@needs_windows
def test_the_generation_directory_swapped_for_a_reparse_point_after_mkdir_refuses_and_writes_nothing(tmp_path, monkeypatch):
    plan, kw, files, result = produced(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def swap(generation_dir):
        os.rename(generation_dir, generation_dir + "-moved")
        try:
            os.symlink(str(elsewhere), generation_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted")
    _after_mkdir(monkeypatch, swap)
    refused("path_refused", _call(tmp_path, files, result))
    assert tree(elsewhere) == []
    assert tree(Path(plan["generation_dir"] + "-moved")) == []


@needs_windows
def test_the_target_directory_swapped_for_a_reparse_point_after_the_check_refuses_and_writes_nothing(tmp_path, monkeypatch):
    plan, kw, files, result = produced(tmp_path)
    target = tmp_path / "generations"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real_mkdir = os.mkdir

    def swapping_mkdir(path, *args, **kwargs):
        os.rename(str(target), str(target) + "-moved")
        try:
            os.symlink(str(elsewhere), str(target), target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted")
        real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr(os, "mkdir", swapping_mkdir)
    refused("path_refused", _call(tmp_path, files, result))
    assert tree(elsewhere) in ([], [GENERATION])
    assert tree(Path(str(target) + "-moved")) == []


@needs_windows
def test_a_generation_directory_replaced_by_another_plain_directory_between_writes_refuses(tmp_path, monkeypatch):
    plan, kw, files, result = produced(tmp_path)
    real_close = os.close
    closes = []

    def swapping_close(handle):
        real_close(handle)
        closes.append(handle)
        if len(closes) == 2:  # the first artifact is written and re-read: move the directory away, plant a plain twin
            os.rename(plan["generation_dir"], plan["generation_dir"] + "-moved")
            os.mkdir(plan["generation_dir"])
    monkeypatch.setattr(os, "close", swapping_close)
    refused("path_refused", _call(tmp_path, files, result))
    assert tree(Path(plan["generation_dir"])) == []  # the twin never receives a file
    assert tree(Path(plan["generation_dir"] + "-moved")) == [module().FILE_NAMES["binding"]]


@needs_windows
def test_publication_uses_no_part_file_and_no_replace(tmp_path, monkeypatch):
    opened = []
    real_open = os.open

    def spy_open(path, flags, *args, **kwargs):
        opened.append((str(path), flags))
        return real_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "replace", lambda *a, **k: pytest.fail("os.replace must not be used"))
    monkeypatch.setattr(os, "rename", lambda *a, **k: pytest.fail("os.rename must not be used"))
    plan, kw, files, result, out = materialize(tmp_path)
    creates = [(p, f) for p, f in opened if f & os.O_CREAT]
    assert [p for p, _ in creates] == [plan[n + "_path"] for n in ARTIFACTS] + [plan["descriptor_path"]]
    assert all(f & os.O_EXCL and f & os.O_WRONLY for _, f in creates)
    assert not any(p.endswith(".part") for p, _ in opened)


@needs_windows
def test_the_generation_directory_swapped_for_a_junction_after_mkdir_refuses_and_writes_nothing(tmp_path, monkeypatch):
    """A junction is a directory under lstat (S_ISDIR holds), so only the reparse attribute distinguishes it."""
    plan, kw, files, result = produced(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def swap(generation_dir):
        os.rename(generation_dir, generation_dir + "-moved")
        done = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", generation_dir, str(elsewhere)],
                              capture_output=True, text=True, timeout=60)
        if done.returncode != 0:
            pytest.skip("junction creation not permitted")
        info = os.lstat(generation_dir)
        assert stat.S_ISDIR(info.st_mode) and info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    _after_mkdir(monkeypatch, swap)
    refused("path_refused", _call(tmp_path, files, result))
    assert tree(elsewhere) == []
    assert tree(Path(plan["generation_dir"] + "-moved")) == []
