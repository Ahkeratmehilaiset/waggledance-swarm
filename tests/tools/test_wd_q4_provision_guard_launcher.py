"""Synthetic pinned launcher tests; never call the real fleet or production state."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/windows/reboot/Invoke-WdQ4ProvisionGuard.ps1"
COMMIT = "a" * 40
PS = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(PS is None, reason="Windows PowerShell required")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def lit(value):
    return "'" + str(value).replace("'", "''") + "'"


def invoke(command):
    return subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", command],
                          capture_output=True, text=True, timeout=30)


@pytest.fixture
def synthetic(tmp_path):
    bundle = tmp_path / "bundle"
    (bundle / "tools-bootstrap/tools").mkdir(parents=True)
    own = bundle / SCRIPT.name
    own.write_bytes(SCRIPT.read_bytes())
    cli = bundle / "tools-bootstrap/tools/wd_q4_guard_state_provision_cli.py"
    cli.write_bytes(b"synthetic-cli")
    fleet = bundle / "wd-fleet.json"
    fleet.write_text(json.dumps({"runtime_root": str(tmp_path / "synthetic-root")}), encoding="utf-8")
    wrapper = bundle / "Invoke-WdBridgePython.ps1"
    wrapper.write_text("param($Tool,[Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest)\n"
        "if ($Tool -cne 'tools/wd_q4_guard_state_provision_cli.py' -or $Rest.Count -ne 3 -or "
        "$Rest[0] -cne '--enable') { throw 'wrong-arguments' }; "
        "$global:LASTEXITCODE=0; '{\"status\":\"READY\"}'\n", encoding="ascii")
    files = {p.relative_to(bundle).as_posix(): digest(p.read_bytes()) for p in (own, cli, fleet, wrapper)}
    manifest = json.dumps({"schema_version": 1, "source_commit": COMMIT, "files": files}).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    pin = digest(manifest)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"active_bundle": str(bundle), "source_commit": COMMIT, "final_commit": COMMIT,
                                 "manifest_sha256": pin, "final_manifest_sha256": pin}), encoding="utf-8")
    return bundle, own, wrapper, state, pin


def call(synthetic, *, apply=True, commit=COMMIT, pin=None):
    bundle, own, _, state, expected = synthetic
    flag = " -Apply" if apply else ""
    cmd = (f". {lit(SCRIPT)}; Invoke-WdQ4GuardProvision{flag} -BundleRoot {lit(bundle)} "
           f"-ExpectedCommit {lit(commit)} -ExpectedManifestSha256 {lit(pin or expected)} "
           f"-StatePath {lit(state)} -OwnScriptPath {lit(own)}")
    return invoke(cmd)


def test_default_off_without_inputs():
    result = invoke(f". {lit(SCRIPT)}; Invoke-WdQ4GuardProvision")
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "OFF"


def test_explicit_apply_calls_only_fixed_pinned_cli(synthetic):
    result = call(synthetic)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "READY"


@pytest.mark.parametrize("change", ["manifest_pin", "commit", "state_final", "wrapper_bytes", "own_bytes", "cli_bytes", "fleet_bytes"])
def test_mismatch_refuses_before_call(synthetic, change):
    bundle, own, wrapper, state, pin = synthetic
    kw = {}
    if change == "manifest_pin":
        kw["pin"] = "0" * 64
    elif change == "commit":
        kw["commit"] = "b" * 40
    elif change == "state_final":
        doc = json.loads(state.read_bytes()); doc["final_manifest_sha256"] = "0" * 64
        state.write_text(json.dumps(doc), encoding="utf-8")
    else:
        target = {"wrapper_bytes": wrapper, "own_bytes": own,
                  "cli_bytes": bundle / "tools-bootstrap/tools/wd_q4_guard_state_provision_cli.py",
                  "fleet_bytes": bundle / "wd-fleet.json"}[change]
        target.write_bytes(target.read_bytes() + b"\n")
    result = call(synthetic, **kw)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "REFUSED"


def test_entered_failure_is_unknown_and_never_retried(synthetic, tmp_path):
    bundle, _, wrapper, state, _ = synthetic
    calls = tmp_path / "calls.txt"
    wrapper.write_text("param($Tool,[Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest)\n"
        f"[IO.File]::AppendAllText({lit(calls)},'x'); "
        "$global:LASTEXITCODE=1; '{\"status\":\"REFUSED\"}'\n", encoding="ascii")
    manifest_path = bundle / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["Invoke-WdBridgePython.ps1"] = digest(wrapper.read_bytes())
    raw = json.dumps(manifest).encode()
    manifest_path.write_bytes(raw)
    pin = digest(raw)
    doc = json.loads(state.read_bytes())
    doc.update(manifest_sha256=pin, final_manifest_sha256=pin)
    state.write_text(json.dumps(doc), encoding="utf-8")
    result = call(synthetic, pin=pin)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "UNKNOWN"
    assert calls.read_text() == "x"
