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


@pytest.mark.parametrize("module", ["tools.wd_q4_lane_caller_cli", "tools.wd_q4_lane_caller_adapter"])
def test_caller_modules_are_explicitly_listed_for_import_smoke(module):
    definition = json.loads(MANIFEST.read_bytes())
    assert definition["python_files"].count(module.replace(".", "/") + ".py") == 1
    assert definition["import_smoke"]["package_modules"].count(module) == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows staged import smoke is required")
@pytest.mark.parametrize("missing", [None, "tools.wd_q4_lane_caller_cli",
                                    "tools.wd_q4_lane_caller_adapter"],
                         ids=["complete", "cli-missing", "adapter-missing"])
def test_real_import_smoke_accepts_caller_closure_and_rejects_missing_modules(
        tmp_path, monkeypatch, missing):
    definition = json.loads(MANIFEST.read_bytes())
    modules = ["tools.wd_q4_lane_caller_cli", "tools.wd_q4_lane_caller_adapter"]
    assert all(definition["import_smoke"]["package_modules"].count(m) == 1 for m in modules)
    spec = importlib.util.spec_from_file_location(
        "lane_caller_smoke_closure", ROOT / "tests/tools/test_bridge_code_package_closure.py")
    closure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(closure)
    monkeypatch.setitem(closure.__dict__, "INTERNAL_ROOTS", (*closure.INTERNAL_ROOTS, "core"))
    seeds = {m.replace(".", "/") + ".py" for m in modules}
    files, unresolved = closure._closure(sorted(seeds))
    files = set(files) | seeds
    assert not unresolved
    assert files <= set(definition["python_files"])
    helpers = _helpers()
    assert helpers.PWSH and helpers.HAS_BRIDGE_PYTHON, "import-smoke prerequisites unavailable"
    bundle = helpers._stage_fake_bundle(tmp_path, real_files=tuple(sorted(files)))
    root = bundle / "tools-bootstrap"
    for module in modules:
        relative = module.replace(".", "/") + ".py"
        assert (root / relative).read_bytes() == (ROOT / relative).read_bytes()
    if missing:
        relative = missing.replace(".", "/") + ".py"
        (root / relative).rename(tmp_path / "removed-module.py")
    context = str(bundle / "BridgeCodeContext.ps1").replace("'", "''")
    definition_path = str(bundle / "bridge-code-files.json").replace("'", "''")
    code_root = str(root).replace("'", "''")
    python = helpers.BRIDGE_PYTHON.replace("'", "''")
    script = f"""$ErrorActionPreference = 'Stop'
. '{context}'
$definition = Get-Content -LiteralPath '{definition_path}' -Raw | ConvertFrom-Json
$definition.import_smoke.package_modules = @('tools.wd_q4_lane_caller_cli', 'tools.wd_q4_lane_caller_adapter')
Test-WdBridgeCodeImports -PythonExecutable '{python}' -CodeRoot '{code_root}' -Definition $definition | ConvertTo-Json -Depth 8
"""
    result = helpers._run_pwsh(script)
    if missing:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "pinned bridge code import smoke" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        for module in modules:
            expected = root / (module.replace(".", "/") + ".py")
            assert Path(report["package_files"][module]).resolve() == expected.resolve()
        assert report["dont_write_bytecode"] and report["no_user_site"] and report["no_site"]
