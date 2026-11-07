"""Synthetic integration of the real provision launcher and bridge wrapper.

The fixture changes the CLI's hard-coded CURRENT path to its synthetic
counterpart. The partial-failure case also injects a deterministic mkdir fault.
All PowerShell wrapper bytes and provisioning logic are real. No claim is made
about the live CURRENT, the installed bundle, or the production Python bytes.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLI = "tools/wd_q4_guard_state_provision_cli.py"
PS = ROOT / "ops/windows/reboot/Invoke-WdQ4ProvisionGuard.ps1"
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
spec = importlib.util.spec_from_file_location("bridge_context_fixture", ROOT / "tests/tools/test_wd_bridge_code_context.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
pytestmark = pytest.mark.skipif(fixture.PWSH is None or not fixture.HAS_BRIDGE_PYTHON,
                                reason="pinned bridge interpreter unavailable")


def sha(data):
    return hashlib.sha256(data).hexdigest().upper()


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def stage(tmp_path, *, fail_second_lane=False):
    paths, _ = fixture._intra_repo_closure([CLI])
    bundle = fixture._stage_fake_bundle(tmp_path, tuple(sorted(paths)), {"q4_guard_provision": CLI})
    cli = bundle / "tools-bootstrap" / CLI
    pointer = tmp_path / "synthetic-current.json"
    original = cli.read_bytes()
    needle = b'_STATE_POINTER = Path(r"C:\\Python\\WD_REBOOT_STATE_CURRENT.json")'
    assert original.count(needle) == 1
    # The process runs the real CLI code, but against a synthetic CURRENT.
    source = original.replace(needle, ('_STATE_POINTER = Path(' + repr(str(pointer)) + ')').encode())
    if fail_second_lane:
        newline = b"\r\n" if b"\r\n" in source else b"\n"
        marker = b'if __name__ == "__main__":' + newline + b'    sys.exit(main())'
        assert source.count(marker) == 1
        injection = b'''if __name__ == "__main__":
    _real_mkdir = Path.mkdir
    def _fixture_mkdir(path, *args, **kwargs):
        if path.name == "q4-caller-state" and path.parent.name == "codex-tools-1":
            raise OSError("synthetic interruption")
        return _real_mkdir(path, *args, **kwargs)
    Path.mkdir = _fixture_mkdir
    sys.exit(main())'''
        source = source.replace(marker, injection.replace(b"\n", newline))
    cli.write_bytes(source)
    own = bundle / PS.name
    shutil.copyfile(PS, own)
    runtime = tmp_path / "runtime"
    audit = runtime / "shared/.codex-audit"
    audit.mkdir(parents=True)
    (bundle / "wd-fleet.json").write_text(json.dumps({"schema_version": 2,
        "bridge_python": {"executable": fixture.BRIDGE_PYTHON}, "runtime_root": str(runtime)}), encoding="utf-8")
    manifest_path = bundle / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    for target in (own, cli, bundle / "wd-fleet.json"):
        manifest["files"][target.relative_to(bundle).as_posix()] = sha(target.read_bytes())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pin = sha(manifest_path.read_bytes())
    pointer.write_text(json.dumps({"active_bundle": str(bundle), "source_commit": "0" * 40,
        "final_commit": "0" * 40, "manifest_sha256": pin, "final_manifest_sha256": pin}), encoding="utf-8")
    return bundle, own, pointer, audit, pin


def invoke(bundle, own, pointer, pin):
    script = (f". {literal(own)}; Invoke-WdQ4GuardProvision -Apply -BundleRoot {literal(bundle)} "
              f"-ExpectedCommit {'0' * 40} -ExpectedManifestSha256 {pin.lower()} "
              f"-StatePath {literal(pointer)} -OwnScriptPath {literal(own)}")
    result = fixture._run_pwsh(script, env={"WD_BRIDGE_PYTHON": fixture.BRIDGE_PYTHON})
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])["status"]


def test_real_wrapper_provisions_synthetic_guard_directories(tmp_path):
    bundle, own, pointer, audit, pin = stage(tmp_path)
    assert invoke(bundle, own, pointer, pin) == "READY"
    assert all((audit / lane / "q4-caller-state").is_dir() for lane in LANES)


def test_pin_mismatch_refuses_before_any_directory(tmp_path):
    bundle, own, pointer, audit, _ = stage(tmp_path)
    assert invoke(bundle, own, pointer, "0" * 64) == "REFUSED"
    assert not list(audit.iterdir())


def test_partial_provision_is_unknown_not_refused(tmp_path):
    # Deterministic test-only mkdir fault after the first lane was created.
    bundle, own, pointer, audit, pin = stage(tmp_path, fail_second_lane=True)
    assert invoke(bundle, own, pointer, pin) == "UNKNOWN"
    assert (audit / LANES[0] / "q4-caller-state").is_dir()
    assert not (audit / LANES[1] / "q4-caller-state").exists()


def test_bridge_context_tampering_is_unknown_without_provision(tmp_path):
    bundle, own, pointer, audit, pin = stage(tmp_path)
    context = bundle / "BridgeCodeContext.ps1"
    context.write_bytes(context.read_bytes() + b"\n# tampered synthetic copy\n")
    assert invoke(bundle, own, pointer, pin) == "UNKNOWN"
    assert not list(audit.iterdir())
