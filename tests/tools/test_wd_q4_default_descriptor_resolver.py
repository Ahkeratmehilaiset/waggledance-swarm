"""Synthetic post-deploy pointer resolution, never installed machine state."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_wd_startup_recovery import load, q
from test_wd_q4_default_descriptor_bootstrap import staged, sha, canon  # noqa: F401


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/windows/reboot/Get-WdQ4DefaultDescriptor.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")


@pytest.mark.parametrize("case", ["off", "missing", "bad_pin", "stale_manifest", "stale_commit",
                                   "wrong_bundle", "wrong_descriptor", "wrong_generation", "manual", "conflict", "ok"])
def test_resolver_never_promotes_untrusted_or_stale_defaults(staged, case):
    item = staged
    pointer_path = item["bundle"].parent / "WD_Q4_DESCRIPTOR_CURRENT.json"
    pointer = {"schema": "wd.q4-descriptor-pointer.v1", "active_bundle": str(item["bundle"]),
        "manifest_sha256": item["manifest_sha"].upper(), "bundle_commit": "b" * 40,
        "descriptor_path": str(item["descriptor"]), "descriptor_sha256": item["descriptor_sha"],
        "generation": "b" * 40}
    if case == "stale_manifest": pointer["manifest_sha256"] = "0" * 64
    if case == "stale_commit": pointer["bundle_commit"] = "c" * 40
    if case == "wrong_bundle": pointer["active_bundle"] = str(item["bundle"].parent / "other")
    if case == "wrong_descriptor": pointer["descriptor_sha256"] = "0" * 64
    if case == "wrong_generation": pointer["generation"] = "c" * 40
    raw = canon(pointer)
    pointer_path.write_bytes(raw)
    pointer_sha = sha(raw)
    if case == "off": pointer_sha = ""; pointer_path.unlink()
    if case == "missing": pointer_path.unlink()
    if case == "bad_pin": pointer_sha = "0" * 64
    explicit_path, explicit_sha = "", ""
    if case == "manual":
        pointer_sha = ""; pointer_path.unlink()
        explicit_path, explicit_sha = str(item["descriptor"]), item["descriptor_sha"]
    if case == "conflict":
        explicit_path, explicit_sha = str(item["descriptor"]), "0" * 64
    script = load(HELPER, "Get-WdQ4DefaultDescriptor") + f'''
try {{
 $r=Get-WdQ4DefaultDescriptor -PointerPath {q(pointer_path)} -ExpectedPointerSha256 {q(pointer_sha)} `
   -ActiveBundle {q(item["bundle"])} -ExpectedManifestHash {q(item["manifest_sha"])} -ExpectedCommit {'b' * 40} `
   -ExplicitPath {q(explicit_path)} -ExplicitSha256 {q(explicit_sha)}
 @{{state=$r.State;path=$r.DescriptorPath;hash=$r.DescriptorSha256}}|ConvertTo-Json -Compress
}} catch {{ @{{state='ERROR'}}|ConvertTo-Json -Compress }}
'''
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    if case == "off":
        assert value["state"] == "OFF"
    elif case == "manual":
        assert value == {"state": "MANUAL", "path": explicit_path, "hash": explicit_sha}
    elif case == "ok":
        assert value == {"state": "READY", "path": str(item["descriptor"]), "hash": item["descriptor_sha"]}
    else:
        assert value["state"] == "UNAVAILABLE"
