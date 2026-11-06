"""Synthetic process exit and stdin contract for the pinned bridge wrapper."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests/tools/test_wd_bridge_code_context.py"
HOSTS = [path for path in (shutil.which("powershell.exe"), shutil.which("pwsh")) if path]
PAYLOAD = b"synthetic-marker\n"


@pytest.mark.skipif(sys.platform != "win32" or not HOSTS, reason="Windows PowerShell required")
@pytest.mark.parametrize("host", HOSTS, ids=lambda path: Path(path).name)
def test_process_exit_opt_in_preserves_inprocess_callers_and_stdin(tmp_path, host):
    spec = importlib.util.spec_from_file_location("wd_bridge_fixture_for_exit", FIXTURE_PATH)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    bundle = fixture._stage_fake_bundle(tmp_path)
    stub = bundle / "tools-bootstrap/tools/bridge_next_action.py"
    stub.write_text(
        "import hashlib,json,sys\n"
        "payload=sys.stdin.buffer.read()\n"
        "print(json.dumps({'sha256':hashlib.sha256(payload).hexdigest()}))\n"
        "sys.exit(23 if '--exit-23' in sys.argv[1:] else 0)\n",
        encoding="utf-8",
    )
    manifest_path = bundle / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["tools-bootstrap/tools/bridge_next_action.py"] = hashlib.sha256(stub.read_bytes()).hexdigest().upper()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    wrapper = bundle / "Invoke-WdBridgePython.ps1"
    literal = str(wrapper).replace("'", "''")
    env = dict(os.environ)
    env["WD_REBOOT_EXPECTED_MANIFEST_HASH"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper()
    env["WD_BRIDGE_PYTHON"] = fixture.BRIDGE_PYTHON
    if Path(host).name.lower() == "powershell.exe":
        env["PSModulePath"] = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/Modules")

    def run(arguments):
        return subprocess.run([host, "-NoProfile", "-NonInteractive", *arguments], input=PAYLOAD,
                              capture_output=True, timeout=90, env=env,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    for expected, suffix in ((0, []), (23, ["--exit-23"])):
        result = run(["-File", str(wrapper), "-Tool", "tools/bridge_next_action.py", "-ExitProcess", *suffix])
        assert result.returncode == expected, result.stderr.decode("utf-8", "replace")
        assert json.loads(result.stdout)["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()

    for operator in ("&", "."):
        command = (f"$answer={operator} '{literal}' 'tools/bridge_next_action.py' '--exit-23'; "
                   "$code=$LASTEXITCODE; $parsed=$answer|ConvertFrom-Json; "
                   "@{code=$code;sha256=$parsed.sha256;continued=$true}|ConvertTo-Json -Compress; exit 0")
        result = run(["-Command", command])
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        assert json.loads(result.stdout) == {"code": 23, "sha256": hashlib.sha256(PAYLOAD).hexdigest(), "continued": True}
