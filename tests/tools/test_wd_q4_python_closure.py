"""Synthetic closure preflight tests; no real interpreter, installation or spawn."""
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only preflight")
SCRIPT = Path(__file__).resolve().parents[2] / "ops/windows/reboot/Test-WdQ4PythonClosure.ps1"


def synthetic(tmp_path):
    root = tmp_path / "closure"
    root.mkdir()
    files = {"python.exe": b"fake-exe", "python313.dll": b"fake-python-dll",
             "python313.zip": b"fake-stdlib", "DLLs/_ssl.pyd": b"fake-extension"}
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    lines = ["wd.q4-python-closure.v1", "version\t313"]
    lines.extend(f"{name}\t{len(value)}\t{hashlib.sha256(value).hexdigest()}" for name, value in files.items())
    manifest = tmp_path / "manifest.txt"
    manifest.write_bytes(("\n".join(lines) + "\n").encode("ascii"))
    return root, manifest


def run(root, manifest, digest=None):
    pin = digest or hashlib.sha256(manifest.read_bytes()).hexdigest()
    # PowerShell 5 may compile the native stream helper; keep its temp files in
    # this synthetic fixture's audit-backed directory, never in the closure.
    env = dict(os.environ, TEMP=str(manifest.parent), TMP=str(manifest.parent))
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT),
                           "-ClosureRoot", str(root), "-ManifestPath", str(manifest), "-ManifestSha256", pin],
                          capture_output=True, text=True, timeout=30, env=env)


def test_valid_synthetic_exact_closure_passes(tmp_path):
    root, manifest = synthetic(tmp_path)
    result = run(root, manifest)
    assert result.returncode == 0, result.stderr
    assert "closure_verified" in result.stdout


@pytest.mark.parametrize("mutation", ["bad_pin", "missing", "extra", "tamper", "bad_case", "extra_dir", "ads",
                                      "directory_ads", "root_directory_ads", "duplicate_case", "version_mismatch", "traversal"])
def test_mismatch_refuses(tmp_path, mutation):
    root, manifest = synthetic(tmp_path)
    pin = None
    if mutation == "bad_pin":
        pin = "0" * 64
    elif mutation == "missing":
        (root / "python313.dll").unlink()
    elif mutation == "extra":
        (root / "extra.py").write_bytes(b"fake")
    elif mutation == "extra_dir":
        (root / "unexpected").mkdir()
    elif mutation == "tamper":
        (root / "python.exe").write_bytes(b"fake-exf")
    elif mutation == "bad_case":
        manifest.write_bytes(manifest.read_bytes().replace(b"python.exe\t", b"Python.exe\t"))
    elif mutation == "ads":
        with open(str(root / "python.exe") + ":hidden", "wb") as stream:
            stream.write(b"synthetic")
    elif mutation == "directory_ads":
        with open(str(root / "DLLs") + ":hidden", "wb") as stream:
            stream.write(b"synthetic")
    elif mutation == "root_directory_ads":
        with open(str(root) + ":hidden", "wb") as stream:
            stream.write(b"synthetic")
    elif mutation == "duplicate_case":
        manifest.write_bytes(manifest.read_bytes() + b"Python.exe\t1\t" + b"0" * 64 + b"\n")
    elif mutation == "version_mismatch":
        manifest.write_bytes(manifest.read_bytes().replace(b"version\t313", b"version\t312"))
    elif mutation == "traversal":
        manifest.write_bytes(manifest.read_bytes() + b"../escape\t1\t" + b"0" * 64 + b"\n")
    result = run(root, manifest, pin)
    assert result.returncode != 0
    assert "closure_verified" not in result.stdout


def test_unc_root_is_refused_before_network_read(tmp_path):
    _, manifest = synthetic(tmp_path)
    result = run(r"\\nonexistent-synthetic.invalid\share", manifest)
    assert result.returncode != 0 and "closure_verified" not in result.stdout


def test_declared_directory_junction_is_refused(tmp_path):
    root, manifest = synthetic(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "synthetic.py").write_bytes(b"fake")
    created = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(root / "Lib"), str(outside)],
                             capture_output=True, text=True, timeout=10)
    if created.returncode:
        pytest.skip("Windows junction creation unavailable in this environment")
    manifest.write_bytes(manifest.read_bytes() + b"Lib/synthetic.py\t4\t" +
                         hashlib.sha256(b"fake").hexdigest().encode() + b"\n")
    result = run(root, manifest)
    assert result.returncode != 0 and "closure_verified" not in result.stdout
