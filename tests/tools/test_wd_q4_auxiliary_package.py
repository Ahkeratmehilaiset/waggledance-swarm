"""Synthetic packaging checks; these modules are not caller entrypoints."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEFINITION = ROOT / "ops/windows/reboot/bridge-code-files.json"
FILES = (
    "tools/wd_q4_fixed_plan_producer.py",
    "tools/wd_q4_guard_state_binding.py",
    "tools/wd_q4_guard_completion.py",
    "tools/wd_q4_lane_quarantine.py",
    "sandbox/mind/step2/q4_controlled_imports.py",
    "sandbox/mind/step2/q4_namespace_origin.py",
    "sandbox/mind/step2/q4_native_additions.py",
)


def _helpers():
    spec = importlib.util.spec_from_file_location(
        "q4_auxiliary_package_fixture", ROOT / "tests/tools/test_wd_bridge_code_context.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_each_auxiliary_module_is_packaged_once_without_entrypoint():
    definition = json.loads(DEFINITION.read_bytes())
    for path in FILES:
        assert definition["python_files"].count(path) == 1, path
        assert path not in definition["python_entrypoints"].values()


def test_staged_auxiliary_hashes_are_bound_and_tampering_is_refused(tmp_path):
    definition = json.loads(DEFINITION.read_bytes())
    assert all(definition["python_files"].count(path) == 1 for path in FILES)
    helpers = _helpers()
    assert helpers.PWSH and helpers.HAS_BRIDGE_PYTHON, "wrapper prerequisites unavailable; NOT verified"
    bundle = helpers._stage_fake_bundle(tmp_path, real_files=FILES)
    deployment = json.loads((bundle / "deployment-manifest.json").read_bytes())
    context = str(ROOT / "ops/windows/reboot/BridgeCodeContext.ps1").replace("'", "''")
    root = str(bundle).replace("'", "''")
    script = (f"$ErrorActionPreference='Stop'; . '{context}'; "
              f"$d=Get-Content -LiteralPath '{root}\\deployment-manifest.json' -Raw | ConvertFrom-Json; "
              f"$p=(Get-WdBridgeCodePackageDefinition -Path '{root}\\bridge-code-files.json').Definition; "
              f"Assert-WdBridgeCodePackageIntegrity -BundleRoot '{root}' -Deployment $d -Definition $p | Out-Null")
    assert helpers._run_pwsh(script).returncode == 0
    for path in FILES:
        staged = bundle / "tools-bootstrap" / path
        source = (ROOT / path).read_bytes()
        assert staged.read_bytes() == source
        assert deployment["files"]["tools-bootstrap/" + path] == hashlib.sha256(source).hexdigest().upper()
        try:
            staged.write_bytes(source + b"\n# synthetic tamper\n")
            failed = helpers._run_pwsh(script)
            assert failed.returncode != 0
            assert "pinned bridge code hash mismatch" in failed.stdout + failed.stderr
        finally:
            staged.write_bytes(source)
