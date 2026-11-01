"""RED contract for an offline Q4 wheel plan, using synthetic bytes only.

Plan validation does not make a site, execute pip, prove dependency closure,
or establish that a wheel is safe. ``free_bytes`` is injected test evidence,
not a measurement of disk space. No network, real wheel, or installer is used
here; all test inputs and paths are local synthetic fixtures.
"""

import copy
import hashlib
import os
from pathlib import Path
import subprocess

import pytest


def sha(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(root):
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*") if p.is_file() and not p.is_symlink()
    }


@pytest.fixture
def plan(tmp_path, monkeypatch):
    def no_process(*args, **kwargs):
        raise AssertionError("validation must not launch a process")

    monkeypatch.setattr(subprocess, "run", no_process)
    monkeypatch.setattr(subprocess, "Popen", no_process)
    roots = [tmp_path / "wheelhouse-a", tmp_path / "wheelhouse-b"]
    for root in roots:
        root.mkdir()
    wheels = []
    for root, filename, data in (
        (roots[0], "alpha-1.0-py3-none-any.whl", b"synthetic-alpha-not-a-wheel"),
        (roots[1], "beta-1.0-py3-none-any.whl", b"synthetic-beta-not-a-wheel"),
    ):
        path = root / filename
        path.write_bytes(data)
        wheels.append({"path": path, "size": len(data), "sha256": sha(data)})
    interpreter = tmp_path / "python.exe"
    interpreter.write_bytes(b"synthetic-not-an-executable")
    lock = (f"alpha==1.0 --hash=sha256:{wheels[0]['sha256']}\n"
            f"beta==1.0 --hash=sha256:{wheels[1]['sha256']}\n").encode("ascii")
    return {
        "wheel_roots": roots,
        "wheels": wheels,
        "target": tmp_path / "new-site",
        "python_executable": interpreter,
        "python_sha256": sha(interpreter.read_bytes()),
        "lock_bytes": lock,
        "lock_sha256": sha(lock),
        "target_tag": "cp313-cp313-win_amd64",
        "max_bytes": 4096,
        "free_bytes": 20 * 1024**3,
    }


def validate(plan):
    from tools.q4_offline_site_candidate import validate_plan

    return validate_plan(**plan)


def test_valid_two_source_plan_only_checks_inputs_and_never_writes(plan, tmp_path):
    before = snapshot(tmp_path)
    result = validate(plan)
    assert result["interpreter_abi"] == "NOT_VERIFIED"
    assert result["wheel_archive_metadata"] == "NOT_VERIFIED"
    assert snapshot(tmp_path) == before
    assert not plan["target"].exists()


def test_local_version_wheel_matches_exact_lock_without_materialization(plan, tmp_path):
    """A PEP 440 local-version suffix is part of the pinned wheel version."""
    data = b"synthetic-torch-not-a-real-wheel"
    wheel = plan["wheel_roots"][0] / "torch-2.13.0+cpu-cp313-cp313-win_amd64.whl"
    wheel.write_bytes(data)
    plan["wheels"][0] = {"path": wheel, "size": len(data), "sha256": sha(data)}
    lock = (f"torch==2.13.0+cpu --hash=sha256:{sha(data)}\n"
            f"beta==1.0 --hash=sha256:{plan['wheels'][1]['sha256']}\n").encode("ascii")
    plan["lock_bytes"] = lock
    plan["lock_sha256"] = sha(lock)
    before = snapshot(tmp_path)
    validate(plan)
    assert snapshot(tmp_path) == before
    assert not plan["target"].exists()


@pytest.mark.parametrize("name", [
    "alpha-1.0-notabuild-py3-none-any.whl",
    "alpha+beta-1.0-py3-none-any.whl",
    "alpha-+++-py3-none-any.whl",
    "alpha-1.0-cp31-abi3-win_amd64.whl",
    "alpha-1.___-py3-none-any.whl",
])
def test_invalid_wheel_filename_is_refused_independently_of_lock(name):
    """A lock-parse or mismatch error must not hide an accepted invalid name."""
    from tools.q4_offline_site_candidate import _wheel_name

    with pytest.raises(ValueError):
        _wheel_name(Path(name), "cp313-cp313-win_amd64")


@pytest.mark.parametrize(("name", "distribution", "version"), [
    ("alpha-1.0-notabuild-py3-none-any.whl", "alpha", "1.0"),
    ("alpha-+++-py3-none-any.whl", "alpha", "+++"),
    ("alpha-1.0-cp31-abi3-win_amd64.whl", "alpha", "1.0"),
])
def test_invalid_build_or_version_refused_even_when_lock_matches(plan, tmp_path, name, distribution, version):
    data = b"synthetic-invalid-wheel"
    wheel = plan["wheel_roots"][0] / name
    wheel.write_bytes(data)
    plan["wheels"][0] = {"path": wheel, "size": len(data), "sha256": sha(data)}
    lock = (f"{distribution}=={version} --hash=sha256:{sha(data)}\n"
            f"beta==1.0 --hash=sha256:{plan['wheels'][1]['sha256']}\n").encode("ascii")
    plan["lock_bytes"], plan["lock_sha256"] = lock, sha(lock)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        validate(plan)
    assert snapshot(tmp_path) == before
    assert not plan["target"].exists()


@pytest.mark.parametrize("mutation", [
    "wrong_hash", "wrong_size", "malformed_hash", "missing_file", "empty_lock",
    "wrong_python_hash", "missing_python", "incompatible_tag", "bad_filename",
    "duplicate_path", "duplicate_distribution", "outside_source_roots",
    "overlapping_roots", "source_child_target", "source_ancestor_target",
    "interpreter_as_target", "existing_target", "unknown_budget", "zero_budget",
    "unknown_free", "insufficient_free", "relative_target", "empty_target_tag",
    "lock_bad_digest", "lock_missing_line", "lock_extra_line", "lock_duplicate_line",
    "lock_wrong_version", "lock_wrong_wheel_hash",
])
def test_bad_plan_refused_before_any_output_write(plan, tmp_path, mutation):
    p = copy.deepcopy(plan)
    first = p["wheels"][0]
    if mutation == "wrong_hash":
        first["sha256"] = "0" * 64
    elif mutation == "wrong_size":
        first["size"] += 1
    elif mutation == "malformed_hash":
        first["sha256"] = "not-a-hash"
    elif mutation == "missing_file":
        first["path"] = first["path"].with_name("missing-1.0-py3-none-any.whl")
    elif mutation == "empty_lock":
        p["wheels"] = []
    elif mutation == "wrong_python_hash":
        p["python_sha256"] = "0" * 64
    elif mutation == "missing_python":
        p["python_executable"] = tmp_path / "missing-python.exe"
    elif mutation == "incompatible_tag":
        first["path"] = first["path"].with_name("alpha-1.0-cp312-cp312-manylinux_x86_64.whl")
        first["path"].write_bytes(b"synthetic-alpha-not-a-wheel")
    elif mutation == "bad_filename":
        first["path"] = first["path"].with_name("not-a-wheel.txt")
        first["path"].write_bytes(b"synthetic-alpha-not-a-wheel")
    elif mutation == "duplicate_path":
        p["wheels"].append(copy.deepcopy(first))
    elif mutation == "duplicate_distribution":
        duplicate = p["wheel_roots"][1] / "alpha-2.0-py3-none-any.whl"
        data = b"synthetic-duplicate-distribution"
        duplicate.write_bytes(data)
        p["wheels"].append({"path": duplicate, "size": len(data), "sha256": sha(data)})
    elif mutation == "outside_source_roots":
        outside = tmp_path / "gamma-1.0-py3-none-any.whl"
        data = b"synthetic-outside-source"
        outside.write_bytes(data)
        first.update(path=outside, size=len(data), sha256=sha(data))
    elif mutation == "overlapping_roots":
        p["wheel_roots"][1] = p["wheel_roots"][0] / "nested"
    elif mutation == "source_child_target":
        p["target"] = p["wheel_roots"][0] / "new-site"
    elif mutation == "source_ancestor_target":
        p["target"] = tmp_path
    elif mutation == "interpreter_as_target":
        p["target"] = p["python_executable"]
    elif mutation == "existing_target":
        p["target"].mkdir()
        (p["target"] / "sentinel").write_bytes(b"do-not-overwrite")
    elif mutation == "unknown_budget":
        p["max_bytes"] = None
    elif mutation == "zero_budget":
        p["max_bytes"] = 0
    elif mutation == "unknown_free":
        p["free_bytes"] = None
    elif mutation == "insufficient_free":
        p["free_bytes"] = 1
    elif mutation == "relative_target":
        p["target"] = Path("relative-site")
    elif mutation == "empty_target_tag":
        p["target_tag"] = ""
    elif mutation == "lock_bad_digest":
        p["lock_sha256"] = "0" * 64
    elif mutation == "lock_missing_line":
        p["lock_bytes"] = p["lock_bytes"].splitlines(keepends=True)[0]
        p["lock_sha256"] = sha(p["lock_bytes"])
    elif mutation == "lock_extra_line":
        p["lock_bytes"] += f"extra==1.0 --hash=sha256:{'a' * 64}\n".encode("ascii")
        p["lock_sha256"] = sha(p["lock_bytes"])
    elif mutation == "lock_duplicate_line":
        p["lock_bytes"] += p["lock_bytes"].splitlines(keepends=True)[0]
        p["lock_sha256"] = sha(p["lock_bytes"])
    elif mutation == "lock_wrong_version":
        p["lock_bytes"] = p["lock_bytes"].replace(b"alpha==1.0", b"alpha==2.0")
        p["lock_sha256"] = sha(p["lock_bytes"])
    elif mutation == "lock_wrong_wheel_hash":
        p["lock_bytes"] = p["lock_bytes"].replace(first["sha256"].encode("ascii"), b"a" * 64)
        p["lock_sha256"] = sha(p["lock_bytes"])
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        validate(p)
    assert snapshot(tmp_path) == before
    assert not plan["target"].exists() or mutation == "existing_target"


def test_wheel_source_symlink_is_refused_before_output(plan, tmp_path):
    source = plan["wheels"][0]["path"]
    link = source.with_name("linked-1.0-py3-none-any.whl")
    try:
        os.symlink(source, link)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks requires OS support")
    plan["wheels"][0]["path"] = link
    before = snapshot(tmp_path)
    with pytest.raises(ValueError):
        validate(plan)
    assert snapshot(tmp_path) == before
    assert not plan["target"].exists()


def test_child_environment_is_explicit_and_offline(tmp_path, monkeypatch):
    from tools.q4_offline_site_candidate import build_isolated_env

    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PIP_INDEX_URL",
                "PIP_EXTRA_INDEX_URL", "PIP_CONFIG_FILE", "PIP_TRUSTED_HOST", "PATH"):
        monkeypatch.setenv(key, "synthetic-untrusted-inherited-value")
    system_root = tmp_path / "system-root"
    temp_dir = tmp_path / "explicit-temp"
    system_root.mkdir()
    temp_dir.mkdir()
    env = build_isolated_env(system_root=system_root, temp_dir=temp_dir)
    assert all("synthetic-untrusted-inherited-value" not in str(v) for v in env.values())
    assert "PATH" not in env
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "PYTHONSTARTUP" not in env
    assert "PIP_INDEX_URL" not in env
    assert "PIP_EXTRA_INDEX_URL" not in env
    assert "PIP_TRUSTED_HOST" not in env
    assert env["PIP_NO_INDEX"] == "1"
    assert env["PIP_CONFIG_FILE"] == os.devnull
    assert env["PYTHONNOUSERSITE"] == "1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert Path(env["TEMP"]) == temp_dir
    assert Path(env["TMP"]) == temp_dir


@pytest.mark.parametrize("field", ["system_root", "temp_dir"])
def test_child_environment_refuses_relative_or_missing_paths(tmp_path, field):
    from tools.q4_offline_site_candidate import build_isolated_env

    args = {"system_root": tmp_path, "temp_dir": tmp_path}
    args[field] = Path("relative-path")
    with pytest.raises(ValueError):
        build_isolated_env(**args)
    args[field] = tmp_path / "does-not-exist"
    with pytest.raises(ValueError):
        build_isolated_env(**args)
