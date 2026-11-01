"""Synthetic PowerShell interpreter-tree checks; never executes the interpreter."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only")
SCRIPT = Path(__file__).resolve().parents[2] / "ops/windows/reboot/WdQ4InterpreterIdentity.ps1"


def _sha(value):
    return hashlib.sha256(value).hexdigest()


class _RedactedEnv(dict):
    def __repr__(self):
        return "<redacted child environment>"


def _child_env():
    return _RedactedEnv((name, value) for name, value in os.environ.items()
                        if name.upper() in ("SYSTEMROOT", "WINDIR", "PYTHONPATH")
                        or name.upper().startswith(("WD_", "PYTEST_")))


def _fixture(tmp_path):
    root = tmp_path / "identity"
    root.mkdir()
    files = {"python.exe": ("interpreter", b"synthetic exe"),
             "python.zip": ("stdlib", b"synthetic stdlib"),
             "DLLs/a.pyd": ("dll", b"synthetic extension")}
    records = []
    for name, (role, content) in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        records.append({"role": role, "path": name, "size": len(content), "sha256": _sha(content)})
    descriptor = tmp_path / "identity.json"
    descriptor.write_text(json.dumps({"schema": "wd.q4-interpreter-identity.v1", "files": records}), encoding="utf-8")
    return root, descriptor, files


def _run(root, descriptor, digest=None, interpreter=None, interpreter_sha=None, enabled="$true"):
    env = _child_env()
    audit_root = SCRIPT.parents[3] / ".codex-audit"
    audit_root.mkdir(exist_ok=True)
    short_temp = tempfile.mkdtemp(prefix="ps-id-", dir=audit_root)
    env.update({"TEMP": short_temp, "TMP": short_temp,
                "Q4_ID_ROOT": str(root), "Q4_ID_DOC": str(descriptor),
                "Q4_ID_PIN": _sha(descriptor.read_bytes()) if digest is None else digest,
                "Q4_ID_EXEC": str(interpreter or root / "python.exe"),
                "Q4_ID_EXEC_PIN": _sha(b"synthetic exe") if interpreter_sha is None else interpreter_sha})
    switch = "" if enabled is None else "-Enabled " + enabled + " "
    command = ("$ErrorActionPreference='Stop'; . '" + str(SCRIPT).replace("'", "''") + "'; "
               "try { $r = Test-WdQ4InterpreterIdentity " + switch + "-Root $env:Q4_ID_ROOT "
               "-DescriptorBytes ([IO.File]::ReadAllBytes($env:Q4_ID_DOC)) "
               "-ExpectedSha256 $env:Q4_ID_PIN -ExpectedInterpreter $env:Q4_ID_EXEC "
               "-ExpectedInterpreterSha256 $env:Q4_ID_EXEC_PIN; [Console]::Out.Write($r.status) } "
               "catch { [Console]::Error.Write('interpreter_identity_refused'); exit 2 }")
    return subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                           "-NoProfile", "-NonInteractive", "-Command", command], env=env,
                          capture_output=True, text=True, timeout=60)


def test_exact_synthetic_tree_is_verified(tmp_path):
    root, descriptor, _ = _fixture(tmp_path)
    result = _run(root, descriptor)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "VERIFIED"


def test_verifier_never_hashes_open_file_to_unbounded_eof():
    source = SCRIPT.read_text(encoding="utf-8")
    assert ".ComputeHash($stream)" not in source


@pytest.mark.parametrize("length,expected_size,expected_status,expected_position", [
    (0, 0, "OK", 0), (1, 0, "REFUSED", 1), (8, 8, "OK", 8), (64, 8, "REFUSED", 9),
    (7, 8, "REFUSED", 7), (1048583, 1048583, "OK", 1048583),
])
def test_synthetic_stream_hash_is_strictly_bounded(tmp_path, length, expected_size, expected_status, expected_position):
    system = Path(os.environ["SystemRoot"]) / "System32"
    expected_hash = _sha(bytes(length))
    command = ("$ErrorActionPreference='Stop'; . '" + str(SCRIPT).replace("'", "''") + "'; "
               f"$s = New-Object IO.MemoryStream(,[byte[]]::new({length})); $status='REFUSED'; "
               f"try {{ $h=Get-WdQ4BoundedHash -Stream $s -ExpectedSize {expected_size}; "
               f"if ($h -cne '{expected_hash}') {{ exit 9 }}; $status='OK' }} catch {{ }}; "
               f"if ($status -cne '{expected_status}' -or $s.Position -ne {expected_position}) {{ exit 2 }}; "
               "[Console]::Out.Write('MATCH')")
    result = subprocess.run([str(system / "WindowsPowerShell/v1.0/powershell.exe"), "-NoProfile",
                             "-NonInteractive", "-Command", command], env=_child_env(), cwd=str(tmp_path),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout == "MATCH"


@pytest.mark.parametrize("mutation", ["bad_pin", "bad_executable", "bad_executable_pin", "missing", "extra",
                                      "tamper", "hardlink", "stream", "directory_stream", "duplicate_key"])
def test_mismatch_refuses(tmp_path, mutation):
    root, descriptor, _ = _fixture(tmp_path)
    kwargs = {}
    if mutation == "bad_pin":
        kwargs["digest"] = "0" * 64
    elif mutation == "bad_executable":
        kwargs["interpreter"] = root / "python.zip"
    elif mutation == "bad_executable_pin":
        kwargs["interpreter_sha"] = "0" * 64
    elif mutation == "missing":
        (root / "python.zip").unlink()
    elif mutation == "extra":
        (root / "unexpected").write_bytes(b"x")
    elif mutation == "tamper":
        (root / "python.exe").write_bytes(b"synthetic exf")
    elif mutation == "hardlink":
        os.link(root / "python.exe", tmp_path / "other-link")
    elif mutation == "stream":
        Path(str(root / "python.exe") + ":hidden").write_bytes(b"x")
    elif mutation == "directory_stream":
        Path(str(root / "DLLs") + ":hidden").write_bytes(b"x")
    elif mutation == "duplicate_key":
        descriptor.write_bytes(descriptor.read_bytes().replace(b'"schema":', b'"schema":"x", "schema":', 1))
    result = _run(root, descriptor, **kwargs)
    assert result.returncode != 0
    assert result.stdout != "VERIFIED"


@pytest.mark.parametrize("enabled", [None, "$false"])
def test_disabled_does_not_inspect_nonexistent_inputs(tmp_path, enabled):
    root, descriptor, _ = _fixture(tmp_path)
    result = _run(root / "does-not-exist", descriptor, digest="", interpreter_sha="", enabled=enabled)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "OFF"


@pytest.mark.parametrize("missing", ["descriptor_pin", "interpreter_pin"])
def test_missing_independent_pin_refuses(tmp_path, missing):
    root, descriptor, _ = _fixture(tmp_path)
    kwargs = {"digest": ""} if missing == "descriptor_pin" else {"interpreter_sha": ""}
    result = _run(root, descriptor, **kwargs)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "interpreter_identity_refused"


def test_non_boolean_enabled_does_not_enable_verification(tmp_path):
    root, descriptor, _ = _fixture(tmp_path)
    result = _run(root, descriptor, enabled="1")
    assert result.returncode != 0
    assert result.stdout != "VERIFIED"


def test_ancestor_junction_refuses(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    root, descriptor, _ = _fixture(target)
    junction = tmp_path / "junction"
    system_root = Path(os.environ["SystemRoot"])
    env = _child_env()
    created = subprocess.run([str(system_root / "System32/cmd.exe"), "/c", "mklink", "/J", str(junction), str(target)],
                             env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=10)
    if created.returncode:
        pytest.skip("synthetic Windows junction creation unavailable")
    result = _run(junction / root.name, descriptor)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "interpreter_identity_refused"


@pytest.mark.parametrize("child", ["powershell", "cmd"])
def test_child_preserves_synthetic_inherited_pins_without_printing_values(tmp_path, monkeypatch, child):
    synthetic = "synthetic-d15-pin-check"
    monkeypatch.setenv("WD_SYNTHETIC_D15_TEST", synthetic)
    monkeypatch.setenv("PYTEST_SYNTHETIC_D15_TEST", synthetic)
    env = _child_env()
    for name in os.environ:
        if name.upper().startswith(("WD_", "PYTEST_")) or name.upper() == "PYTHONPATH":
            if env.get(name) != os.environ.get(name):
                pytest.fail("inherited child settings differ", pytrace=False)
    system = Path(os.environ["SystemRoot"]) / "System32"
    if child == "powershell":
        argv = [str(system / "WindowsPowerShell/v1.0/powershell.exe"), "-NoProfile", "-NonInteractive", "-Command",
                "if ($env:WD_SYNTHETIC_D15_TEST -ceq 'synthetic-d15-pin-check' -and "
                "$env:PYTEST_SYNTHETIC_D15_TEST -ceq 'synthetic-d15-pin-check') "
                "{ [Console]::Out.Write('MATCH') } else { exit 2 }"]
    else:
        for name in ("WD_SYNTHETIC_D15_TEST", "PYTEST_SYNTHETIC_D15_TEST"):
            argv = [str(system / "cmd.exe"), "/d", "/c", "if", f"%{name}%==synthetic-d15-pin-check",
                    "(exit", "/b", "0)", "else", "(exit", "/b", "2)"]
            result = subprocess.run(argv, env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=10)
            assert result.returncode == 0
            bad_env = _RedactedEnv(env, **{name: "synthetic-other"})
            negative = subprocess.run(argv, env=bad_env, cwd=str(tmp_path), capture_output=True, text=True, timeout=10)
            assert negative.returncode != 0
        return
    result = subprocess.run(argv, env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert result.stdout.strip() == "MATCH"
    assert result.stderr == ""
