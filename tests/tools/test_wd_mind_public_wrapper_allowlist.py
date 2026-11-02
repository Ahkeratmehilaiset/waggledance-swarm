"""Synthetic wrapper admission contract, never reads the live bridge or starts Mind.

The temporary bundle uses the real hash-checking PowerShell wrapper and the
checkout's entrypoint allowlist. The two tool bodies are inert sentinel scripts;
this tests admission/isolation, not either publisher's runtime behavior.
"""
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "ops/windows/reboot/bridge-code-files.json"
PUBLIC = ("tools/wd_mind_public_session_cli.py", "tools/wd_mind_public_publish_v3_cli.py")


def test_real_wrapper_admits_only_packaged_public_entrypoints(tmp_path):
    path = ROOT / "tests/tools/test_wd_bridge_code_context.py"
    spec = importlib.util.spec_from_file_location("synthetic_wd_bridge_context_fixture", path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    assert helpers.PWSH and helpers.HAS_BRIDGE_PYTHON, "wrapper test prerequisites unavailable; NOT verified"
    definition = json.loads(MANIFEST.read_bytes())
    assert all(definition["python_files"].count(tool) == 1 for tool in PUBLIC)
    listed = {name: target for name, target in definition["python_entrypoints"].items() if target in PUBLIC}
    bundle = helpers._stage_fake_bundle(tmp_path, real_files=PUBLIC, real_entrypoints=listed)
    deployment_path = bundle / "deployment-manifest.json"
    deployment = json.loads(deployment_path.read_bytes())
    stub = b"import json,sys\nprint(json.dumps({'synthetic':True,'no_site':bool(sys.flags.no_site)}))\n"
    for tool in PUBLIC:
        (bundle / "tools-bootstrap" / tool).write_bytes(stub)
        deployment["files"]["tools-bootstrap/" + tool] = hashlib.sha256(stub).hexdigest().upper()
    deployment_path.write_text(json.dumps(deployment), encoding="utf-8")
    anchor = {"WD_REBOOT_EXPECTED_MANIFEST_HASH": helpers._manifest_anchor(bundle),
              "WD_BRIDGE_PYTHON": helpers.BRIDGE_PYTHON}
    wrapper = str(bundle / "Invoke-WdBridgePython.ps1").replace("'", "''")

    def invoke(tool, env=anchor):
        return helpers._run_pwsh(f"$ErrorActionPreference='Stop'; & '{wrapper}' '{tool}'", env=env)

    control = invoke("tools/bridge_next_action.py")
    assert control.returncode == 0, control.stdout + control.stderr
    assert json.loads(control.stdout.strip())["no_site"] is True
    unauthorized = invoke("tools/synthetic-unlisted.py")
    assert unauthorized.returncode != 0
    assert "outside the packaged entrypoints" in unauthorized.stdout + unauthorized.stderr
    mismatch = invoke("tools/bridge_next_action.py", {**anchor, "WD_REBOOT_EXPECTED_MANIFEST_HASH": "B" * 64})
    assert mismatch.returncode != 0 and "differs from its external anchor" in mismatch.stdout + mismatch.stderr
    failures = {}
    for tool in PUBLIC:
        observed = invoke(tool)
        if observed.returncode != 0:
            failures[tool] = "entrypoint_refused" if "outside the packaged entrypoints" in (
                observed.stdout + observed.stderr) else "other_wrapper_failure"
        elif json.loads(observed.stdout.strip()) != {"synthetic": True, "no_site": True}:
            failures[tool] = "unexpected_synthetic_result"
    assert failures == {}, failures
