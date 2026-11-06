"""Synthetic package admission for the default-off lane caller CLI."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "ops/windows/reboot/bridge-code-files.json"
CLI = "tools/wd_q4_lane_caller_cli.py"
ENTRYPOINT = "q4_lane_caller"


def _helpers():
    path = ROOT / "tests/tools/test_wd_bridge_code_context.py"
    spec = importlib.util.spec_from_file_location("lane_caller_package_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_caller_cli_is_packaged_once_registered_and_closes_over_internal_imports(monkeypatch):
    definition = json.loads(MANIFEST.read_bytes())
    assert definition["python_files"].count(CLI) == 1
    assert definition["python_entrypoints"][ENTRYPOINT] == CLI
    path = ROOT / "tests/tools/test_bridge_code_package_closure.py"
    spec = importlib.util.spec_from_file_location("lane_caller_package_closure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(module.__dict__, "INTERNAL_ROOTS", (*module.INTERNAL_ROOTS, "core"))
    reached, unresolved = module._closure([CLI])
    assert not unresolved, unresolved
    assert set(reached) <= set(definition["python_files"]), sorted(set(reached) - set(definition["python_files"]))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows staged wrapper is required")
def test_real_wrapper_accepts_exact_packaged_lane_caller_in_off_mode(tmp_path):
    definition = json.loads(MANIFEST.read_bytes())
    assert definition["python_files"].count(CLI) == 1
    assert definition["python_entrypoints"][ENTRYPOINT] == CLI
    helpers = _helpers()
    assert helpers.PWSH and helpers.HAS_BRIDGE_PYTHON, "wrapper prerequisites unavailable; NOT verified"
    bundle = helpers._stage_fake_bundle(tmp_path, real_files=(CLI,), real_entrypoints={ENTRYPOINT: CLI})
    packaged = bundle / "tools-bootstrap" / CLI
    assert packaged.read_bytes() == (ROOT / CLI).read_bytes()
    deployment = json.loads((bundle / "deployment-manifest.json").read_bytes())
    assert deployment["files"]["tools-bootstrap/" + CLI] == hashlib.sha256(packaged.read_bytes()).hexdigest().upper()
    anchor = {"WD_REBOOT_EXPECTED_MANIFEST_HASH": helpers._manifest_anchor(bundle),
              "WD_BRIDGE_PYTHON": helpers.BRIDGE_PYTHON}
    wrapper = str(bundle / "Invoke-WdBridgePython.ps1").replace("'", "''")
    result = helpers._run_pwsh(f"$ErrorActionPreference='Stop'; & '{wrapper}' '{CLI}'", env=anchor)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip()) == {"status": "OFF"}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows staged wrapper is required")
def test_real_wrapper_refuses_packaged_lane_caller_without_entrypoint(tmp_path):
    helpers = _helpers()
    assert helpers.PWSH and helpers.HAS_BRIDGE_PYTHON, "wrapper prerequisites unavailable; NOT verified"
    bundle = helpers._stage_fake_bundle(tmp_path, real_files=(CLI,), real_entrypoints={})
    assert (bundle / "tools-bootstrap" / CLI).read_bytes() == (ROOT / CLI).read_bytes()
    anchor = {"WD_REBOOT_EXPECTED_MANIFEST_HASH": helpers._manifest_anchor(bundle),
              "WD_BRIDGE_PYTHON": helpers.BRIDGE_PYTHON}
    wrapper = str(bundle / "Invoke-WdBridgePython.ps1").replace("'", "''")
    control = helpers._run_pwsh(f"$ErrorActionPreference='Stop'; & '{wrapper}' tools/bridge_next_action.py", env=anchor)
    assert control.returncode == 0, control.stdout + control.stderr
    result = helpers._run_pwsh(f"$ErrorActionPreference='Stop'; & '{wrapper}' '{CLI}'", env=anchor)
    assert result.returncode != 0
    assert "outside the packaged entrypoints" in result.stdout + result.stderr
