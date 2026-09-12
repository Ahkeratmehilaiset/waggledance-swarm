"""Benign pinned bridge startup and process-isolation contracts."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops/windows/reboot"
PS = shutil.which("powershell.exe") or shutil.which("pwsh")


def test_tools_initializes_pinned_context_before_consumer():
    source = (REBOOT / "start-wd-tools-consumer.ps1").read_text()
    assert "Initialize-WdBridgeCodeContext" in source
    assert source.index("Initialize-WdBridgeCodeContext") < source.index("$commonConsumerArguments =")


def test_package_entrypoints_exist_and_include_release_helpers():
    definition = json.loads((REBOOT / "bridge-code-files.json").read_text())
    for name in definition["python_files"]:
        assert (ROOT / name).is_file(), name
    assert set(definition["python_entrypoints"].values()) <= set(definition["python_files"])
    assert "tools/build_bridge_message_template.py" in definition["python_files"]
    assert "tools/agent_next_task.py" in definition["python_files"]


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
def test_discovery_environment_does_not_change_task_python():
    context = str(REBOOT / "BridgeCodeContext.ps1").replace("'", "''")
    definition = str(REBOOT / "bridge-code-files.json").replace("'", "''")
    script = f"""
    $ErrorActionPreference = 'Stop'
    . '{context}'
    $before = $env:PYTHONPATH
    $definition = (Get-WdBridgeCodePackageDefinition -Path '{definition}').Definition
    $map = Get-WdBridgeCodeDiscoveryEnvironment -Definition $definition -BundleRoot 'C:\\bundle' -PythonExecutable 'C:\\python.exe' -PythonSha256 ('A'*64) -Generation ('a'*40) -RuntimeRoot 'C:\\runtime'
    if (@($map.Keys | Where-Object {{ $_ -notlike 'WD_BRIDGE_*' }}).Count) {{ throw 'global Python pollution' }}
    if ($env:PYTHONPATH -cne $before) {{ throw 'task environment changed' }}
    $isolation = Get-WdBridgeCodeIsolationEnvironment -Definition $definition -CodeRoot 'C:\\bundle\\tools-bootstrap'
    if ($isolation.PYTHONSAFEPATH -ne '1') {{ throw 'missing scoped isolation' }}
    'PASS'
    """
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_stage_only_returns_before_machine_wrappers():
    source = (REBOOT / "Deploy-WdRebootBundle.ps1").read_text()
    stage = source.index("STAGE ONLY: commit-addressed bundle verified")
    assert source.index("return", stage) < source.index("$wrapperSpecs =", stage)
    assert "-not $SkipTaskRegistration -and -not $StageOnly" in source


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
def test_python_process_argument_roundtrip():
    context = str(REBOOT / "BridgeCodeContext.ps1").replace("'", "''")
    python = sys.executable.replace("'", "''")
    script = f"""
    $ErrorActionPreference = 'Stop'
    . '{context}'
    foreach ($value in @('C:\\Python\\a folder\\file.json', 'C:\\Python\\a folder\\', 'say "hello"')) {{
        $result = Invoke-WdBridgeCodePython -PythonExecutable '{python}' -Arguments @('-B','-c','import sys; print(sys.argv[1])',$value) -Label 'argument roundtrip'
        if ($result.StdOut.Trim() -cne $value) {{ throw 'argument changed' }}
    }}
    """
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_tool_output_is_not_combined_with_return_code():
    """The exit code travels out of band, and the tool's own output stays
    capturable: these tools emit JSON that callers parse, so routing it to the
    host (Out-Host) would make `$x = & wrapper ...` return nothing."""
    source = (REBOOT / "BridgeCodeContext.ps1").read_text()
    invocation = "& $python.Path @($script:WdBridgeCodePythonFlags) $toolPath @ToolArguments"
    assert invocation in source
    line = next(
        candidate for candidate in source.splitlines() if invocation in candidate
    )
    assert "|" not in line.split("@ToolArguments", 1)[1]
    assert "$script:WdBridgeCodeLastExitCode = $exitCode" in source
    assert "function Get-WdBridgeCodeLastExitCode" in source
    # the function itself must not emit the exit code onto the success stream
    body = source.split("function Invoke-WdBridgePythonTool", 1)[1]
    body = body.split("\nfunction ", 1)[0]
    assert "return $exitCode" not in body


# --- fable-5 additions: closure, pins, ordering, fail-closed, scoped isolation ---

import ast
import base64
import hashlib
import os
import re
import sys
import zipfile

DEFINITION_PATH = REBOOT / "bridge-code-files.json"
DEFINITION = json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))
FLEET = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
SUPERVISOR = json.loads((REBOOT / "wd_supervisor_loop.json").read_text(encoding="utf-8"))
PWSH = shutil.which("pwsh") or shutil.which("powershell.exe")
BRIDGE_PYTHON = FLEET["bridge_python"]["executable"]
HAS_BRIDGE_PYTHON = Path(BRIDGE_PYTHON).is_file()


def _module_relative(module: str) -> str | None:
    top = module.split(".")[0]
    if top not in {"tools", "waggledance"}:
        return None
    parts = module.split(".")
    for candidate in (
        ROOT.joinpath(*parts).with_suffix(".py"),
        ROOT.joinpath(*parts, "__init__.py"),
    ):
        if candidate.is_file():
            return candidate.relative_to(ROOT).as_posix()
    return None


def _intra_repo_closure(entrypoints):
    seen: set[str] = set()
    third_party: set[str] = set()
    queue = list(entrypoints)
    while queue:
        relative = queue.pop()
        if relative in seen:
            continue
        seen.add(relative)
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and not node.level:
                modules.add(node.module or "")
                for alias in node.names:
                    modules.add(f"{node.module}.{alias.name}")
        for module in modules:
            if not module:
                continue
            top = module.split(".")[0]
            if top in {"tools", "waggledance"}:
                target = _module_relative(module)
                if target and target not in seen:
                    queue.append(target)
                parts = module.split(".")
                for index in range(1, len(parts)):
                    init = "/".join(parts[:index]) + "/__init__.py"
                    if (ROOT / init).is_file() and init not in seen:
                        queue.append(init)
            elif top not in sys.stdlib_module_names:
                third_party.add(top)
    return seen, third_party


def test_package_closes_over_every_intra_repo_import_and_pins_its_third_party_closure():
    entrypoints = sorted(set(DEFINITION["python_entrypoints"].values()))
    closure, third_party = _intra_repo_closure(entrypoints)
    packaged = set(DEFINITION["python_files"])
    assert closure <= packaged, sorted(closure - packaged)
    assert third_party == {"pydantic"}, sorted(third_party)
    lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8")
    pinned = {
        match.group(1).lower().replace("_", "-"): match.group(2)
        for match in re.finditer(r"^([A-Za-z0-9._-]+)==([^\s;]+)", lock, re.MULTILINE)
    }
    for requirement in DEFINITION["python_requirements"]:
        name = requirement["name"].lower().replace("_", "-")
        assert pinned.get(name) == requirement["version"], (name, pinned.get(name))
        assert re.fullmatch(r"[0-9a-f]{64}", requirement["sha256"]), name
        assert requirement["wheel"].endswith(".whl")
    # pydantic itself must be present; its dependencies travel with it.
    assert any(item["name"] == "pydantic" for item in DEFINITION["python_requirements"])


def test_fleet_pins_the_same_interpreter_and_requires_the_package_definition():
    bridge_python = FLEET["bridge_python"]["executable"]
    assert bridge_python == FLEET["tools_supervisor"]["python_executable"]
    assert bridge_python == SUPERVISOR["tools_consumer"]["python_executable"]
    required = FLEET["deployment"]["required_bundle_files"]
    for name in (
        "BridgeCodeContext.ps1",
        "Invoke-WdBridgePython.ps1",
        "bridge-code-files.json",
    ):
        assert name in required, name
    # The package contents are named once, in the definition: every required
    # bundle file must resolve to a committed source, while wheels and the
    # extracted site are staged by the installer and hashed into the manifest.
    for name in required:
        assert not name.startswith("tools-bootstrap/tools/"), name
        assert not name.startswith("tools-bootstrap/waggledance/"), name
        assert not name.startswith("tools-bootstrap/python-"), name
        source = (
            ROOT / name[len("tools-bootstrap/") :]
            if name.startswith("tools-bootstrap/")
            else REBOOT / name
        )
        assert source.is_file(), name


def test_tools_prompt_uses_the_pinned_wrapper_and_keeps_the_worktree_cwd():
    prompt = SUPERVISOR["tools_consumer"]["prompt"]
    assert "$env:WD_BRIDGE_PYTHON_WRAPPER" in prompt
    assert "tools/bridge_next_action.py" in prompt
    assert "python tools\\bridge_next_action.py" not in prompt
    assert "keep this worktree as their cwd" in prompt


def test_lane_launcher_initializes_context_after_integrity_and_before_cli_launch():
    launcher = (REBOOT / "start-wd-agent.ps1").read_text(encoding="utf-8")
    integrity = launcher.index("Assert-LaneBootstrapIntegrity `")
    initialize = launcher.index("Initialize-WdBridgeCodeContext")
    launch = launcher.index("& $cliPath @launchArguments")
    set_location = launcher.index("Set-Location -LiteralPath $worktree")
    assert integrity < initialize < set_location < launch
    assert "pinned bridge code input is not covered by the anchored bundle" in launcher
    assert "$env:WD_BRIDGE_BIN" in launcher
    assert "$env:WD_BRIDGE_PYTHON_WRAPPER" in launcher
    assert "bridge_python_wrapper = [string]$bridgeCodeContext.python_wrapper" in launcher
    # Discovery only: the lane must not export Python isolation to the model shell.
    for polluting in ("$env:PYTHONPATH =", "$env:PYTHONSAFEPATH =", "$env:PYTHONNOUSERSITE ="):
        assert polluting not in launcher, polluting


def test_reader_prefers_the_pinned_wrapper_without_breaking_explicit_callers():
    reader = (ROOT / ".agent-bridge/bin/Read-AgentBridge.ps1").read_text(encoding="utf-8")
    assert "$env:WD_BRIDGE_PYTHON_WRAPPER" in reader
    assert "-not $PSBoundParameters.ContainsKey('PythonExecutable')" in reader
    assert "& $PythonExecutable $compactTool @compactArgs" in reader


def test_installer_stage_only_and_wheel_staging_are_hash_bound():
    source = (REBOOT / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    assert "[switch] $StageOnly" in source
    assert "-not $SkipGrokResolve -and -not $StageOnly" in source
    assert "Install-WdBridgePythonSite" in source
    assert "$bridgeCodeSourceFiles" in source
    context = (REBOOT / "BridgeCodeContext.ps1").read_text(encoding="utf-8")
    assert "'--require-hashes'," in context
    assert "'--only-binary=:all:'," in context
    assert "'--no-compile'," in context
    assert "'--no-index'," in context
    assert "pinned wheel hash mismatch after download" in context
    assert "compiled bytecode in the pinned python site" in context


def _write_fake_wheel(path: Path, name: str, version: str, module: str) -> str:
    dist_info = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{module}/__init__.py", "MARKER = 'pinned'\n")
        archive.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fake_definition(wheel_name: str, sha256: str, module: str) -> dict:
    return {
        "schema": "wd.bridge-code-package.v1",
        "purpose": "test fixture",
        "package_relative_root": "tools-bootstrap",
        "invocation_wrapper_relative": "Invoke-WdBridgePython.ps1",
        "python_files": ["tools/__init__.py", "tools/bridge_next_action.py"],
        "python_entrypoints": {"next_action": "tools/bridge_next_action.py"},
        "python_platform": {
            "implementation": "py",
            "python_version": "3.13",
            "abi": "none",
            "platform": "any",
        },
        "python_requirements": [
            {
                "name": "wdfake",
                "version": "0.1.0",
                "wheel": wheel_name,
                "sha256": sha256,
                "import_path": f"{module}/__init__.py",
            }
        ],
        "wheel_store_relative": "python-wheels",
        "python_site_relative": "python-site",
        "import_smoke": {
            "third_party_module": module,
            "package_modules": ["tools.bridge_next_action"],
        },
        "isolation_environment": {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONSAFEPATH": "1",
        },
    }


def _run_pwsh(script: str, timeout: int = 300, env: dict | None = None):
    assert PWSH is not None
    # A live lane session exports the real bundle anchor; fixtures must carry
    # their own so the wrapper's anchor check is exercised, not inherited.
    environment = dict(os.environ)
    environment.pop("WD_REBOOT_EXPECTED_MANIFEST_HASH", None)
    environment.update(env or {})
    return subprocess.run(
        [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=environment,
    )


def _manifest_anchor(bundle: Path) -> str:
    blob = (bundle / "deployment-manifest.json").read_bytes()
    return hashlib.sha256(blob).hexdigest().upper()


def _stage_fake_bundle(tmp_path: Path) -> Path:
    """Build a deployed-shaped bundle with a fake pinned wheel closure."""
    module = "wdfake"
    bundle = tmp_path / ("0" * 40)
    bundle.mkdir(parents=True)
    source_wheels = tmp_path / "src-wheels"
    source_wheels.mkdir()
    wheel_name = "wdfake-0.1.0-py3-none-any.whl"
    sha256 = _write_fake_wheel(source_wheels / wheel_name, "wdfake", "0.1.0", module)
    definition = _fake_definition(wheel_name, sha256, module)
    code_root = bundle / "tools-bootstrap"
    (code_root / "tools").mkdir(parents=True)
    (code_root / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (code_root / "tools" / "bridge_next_action.py").write_text(
        "import json, sys, os\n"
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "                  'pythonpath': os.environ.get('PYTHONPATH', ''),\n"
        "                  'safe_path': bool(getattr(sys.flags, 'safe_path', 0)),\n"
        "                  'no_site': bool(sys.flags.no_site)}))\n",
        encoding="utf-8",
    )
    (bundle / "wd-fleet.json").write_text(
        json.dumps(
            {"schema_version": 2, "bridge_python": {"executable": BRIDGE_PYTHON}}, indent=2
        )
        + chr(10),
        encoding="utf-8",
    )
    shutil.copy2(REBOOT / "BridgeCodeContext.ps1", bundle / "BridgeCodeContext.ps1")
    shutil.copy2(REBOOT / "Invoke-WdBridgePython.ps1", bundle / "Invoke-WdBridgePython.ps1")
    (bundle / "bridge-code-files.json").write_text(
        json.dumps(definition, indent=2) + "\n", encoding="utf-8"
    )
    context = str(REBOOT / "BridgeCodeContext.ps1").replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
. '{context}'
$definition = (Get-WdBridgeCodePackageDefinition -Path '{str(bundle / "bridge-code-files.json").replace("'", "''")}').Definition
$staged = Install-WdBridgePythonSite `
    -Definition $definition `
    -PythonExecutable '{BRIDGE_PYTHON.replace("'", "''")}' `
    -WheelDirectory '{str(code_root / "python-wheels").replace("'", "''")}' `
    -SiteDirectory '{str(code_root / "python-site").replace("'", "''")}' `
    -WorkDirectory '{str(tmp_path / "work").replace("'", "''")}' `
    -WheelSource '{str(source_wheels).replace("'", "''")}'
$map = [ordered]@{{}}
foreach ($key in @($staged.Wheels.Keys)) {{ $map[$key] = $staged.Wheels[$key] }}
foreach ($key in @($staged.Site.Keys)) {{ $map[$key] = $staged.Site[$key] }}
$map | ConvertTo-Json -Depth 4 -Compress
"""
    result = _run_pwsh(script)
    assert result.returncode == 0, result.stdout + result.stderr
    staged = json.loads([line for line in result.stdout.splitlines() if line.strip()][-1])
    files = {f"tools-bootstrap/{key}": value for key, value in staged.items()}
    for relative in definition["python_files"]:
        blob = (code_root / relative).read_bytes()
        files[f"tools-bootstrap/{relative}"] = hashlib.sha256(blob).hexdigest().upper()
    for name in (
        "BridgeCodeContext.ps1",
        "Invoke-WdBridgePython.ps1",
        "bridge-code-files.json",
        "wd-fleet.json",
    ):
        files[name] = hashlib.sha256((bundle / name).read_bytes()).hexdigest().upper()
    manifest = {"schema_version": 1, "source_commit": "0" * 40, "files": files}
    (bundle / "deployment-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return bundle


@pytest.mark.skipif(
    PWSH is None or not HAS_BRIDGE_PYTHON,
    reason="PowerShell or the pinned bridge interpreter is unavailable",
)
def test_staged_package_verifies_and_fails_closed_on_tampering(tmp_path: Path):
    bundle = _stage_fake_bundle(tmp_path)
    context = str(REBOOT / "BridgeCodeContext.ps1").replace("'", "''")
    bundle_literal = str(bundle).replace("'", "''")
    prelude = f"""
$ErrorActionPreference = 'Stop'
. '{context}'
$deployment = Get-Content -LiteralPath '{bundle_literal}\\deployment-manifest.json' -Raw | ConvertFrom-Json
$definition = (Get-WdBridgeCodePackageDefinition -Path '{bundle_literal}\\bridge-code-files.json').Definition
"""
    ok = _run_pwsh(
        prelude
        + "$r = Assert-WdBridgeCodePackageIntegrity -BundleRoot '"
        + bundle_literal
        + "' -Deployment $deployment -Definition $definition; "
        + "if ($r.WheelCount -ne 1) { throw 'wheel not counted' }; 'PASS'"
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "PASS" in ok.stdout

    site = bundle / "tools-bootstrap" / "python-site"
    cases = []
    # 1. hash mismatch
    tampered = bundle / "tools-bootstrap" / "tools" / "bridge_next_action.py"
    original = tampered.read_bytes()
    tampered.write_bytes(original + b"# tampered\n")
    cases.append(("pinned bridge code hash mismatch", lambda: tampered.write_bytes(original)))
    for expected, undo in cases:
        failed = _run_pwsh(
            prelude
            + "Assert-WdBridgeCodePackageIntegrity -BundleRoot '"
            + bundle_literal
            + "' -Deployment $deployment -Definition $definition"
        )
        assert failed.returncode != 0, failed.stdout
        assert expected in (failed.stdout + failed.stderr), failed.stdout + failed.stderr
        undo()
    # 2. unlisted file
    stray = site / "stray_module.py"
    stray.write_text("x = 1\n", encoding="utf-8")
    unlisted = _run_pwsh(
        prelude
        + "Assert-WdBridgeCodePackageIntegrity -BundleRoot '"
        + bundle_literal
        + "' -Deployment $deployment -Definition $definition"
    )
    assert unlisted.returncode != 0
    assert "unexpected file inside pinned bridge code package" in (unlisted.stdout + unlisted.stderr)
    stray.unlink()
    # 3. bytecode cache
    cache = site / "__pycache__"
    cache.mkdir()
    (cache / "stray.cpython-313.pyc").write_bytes(b"\x00")
    cached = _run_pwsh(
        prelude
        + "Assert-WdBridgeCodePackageIntegrity -BundleRoot '"
        + bundle_literal
        + "' -Deployment $deployment -Definition $definition"
    )
    assert cached.returncode != 0
    assert "bytecode cache inside pinned bridge code package" in (cached.stdout + cached.stderr)
    shutil.rmtree(cache)
    # 4a. an empty manifest cannot even anchor the invocation wrapper
    empty = _run_pwsh(
        prelude
        + "$deployment.files = [pscustomobject]@{}; "
        + "Assert-WdBridgeCodePackageIntegrity -BundleRoot '"
        + bundle_literal
        + "' -Deployment $deployment -Definition $definition"
    )
    assert empty.returncode != 0
    assert "is not covered by the anchored bundle" in (empty.stdout + empty.stderr)
    # 4b. a bundle that predates the package (top-level files anchored, no
    # tools-bootstrap entries) must refuse rather than fall back to local code.
    without_package = _run_pwsh(
        prelude
        + "$kept = [ordered]@{}; "
        + "foreach ($p in @($deployment.files.PSObject.Properties)) { "
        + "  if (-not ([string]$p.Name).StartsWith('tools-bootstrap/')) { $kept[$p.Name] = $p.Value } }; "
        + "$deployment.files = [pscustomobject]$kept; "
        + "Assert-WdBridgeCodePackageIntegrity -BundleRoot '"
        + bundle_literal
        + "' -Deployment $deployment -Definition $definition"
    )
    assert without_package.returncode != 0
    assert "refusing unpinned local helpers" in (
        without_package.stdout + without_package.stderr
    )
    # restore: the verified state must still pass
    again = _run_pwsh(
        prelude
        + "[void](Assert-WdBridgeCodePackageIntegrity -BundleRoot '"
        + bundle_literal
        + "' -Deployment $deployment -Definition $definition); 'PASS'"
    )
    assert again.returncode == 0, again.stdout + again.stderr


@pytest.mark.skipif(
    PWSH is None or not HAS_BRIDGE_PYTHON,
    reason="PowerShell or the pinned bridge interpreter is unavailable",
)
def test_wrapper_isolates_one_call_restores_env_and_refuses_unpackaged_tools(tmp_path: Path):
    bundle = _stage_fake_bundle(tmp_path)
    wrapper = str(bundle / "Invoke-WdBridgePython.ps1").replace("'", "''")
    caller_cwd = tmp_path / "caller"
    caller_cwd.mkdir()
    script = f"""
$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = 'C:\\task\\worktree'
$env:PYTHONSAFEPATH = $null
Set-Location -LiteralPath '{str(caller_cwd).replace("'", "''")}'
$output = & '{wrapper}' tools/bridge_next_action.py --agent fable-5
if ($LASTEXITCODE -ne 0) {{ throw "wrapper exit $LASTEXITCODE" }}
$report = $output | ConvertFrom-Json
$restored = [pscustomobject]@{{
    tool = $report
    exit_code = $LASTEXITCODE
    after_pythonpath = [string]$env:PYTHONPATH
    after_safepath = [string]$env:PYTHONSAFEPATH
    after_cwd = (Get-Location).Path
}}
$restored | ConvertTo-Json -Depth 5 -Compress
"""
    anchor = {
        "WD_REBOOT_EXPECTED_MANIFEST_HASH": _manifest_anchor(bundle),
        "WD_BRIDGE_PYTHON": BRIDGE_PYTHON,
    }
    result = _run_pwsh(script, env=anchor)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads([line for line in result.stdout.splitlines() if line.strip()][-1])
    assert report["tool"]["argv"] == ["--agent", "fable-5"]
    assert report["tool"]["safe_path"] is True
    assert report["tool"]["no_site"] is True
    assert str(bundle / "tools-bootstrap") in report["tool"]["pythonpath"]
    assert report["after_pythonpath"] == "C:\\task\\worktree"
    assert report["after_safepath"] == ""
    assert report["after_cwd"].rstrip("\\") == str(caller_cwd).rstrip("\\")
    assert report["exit_code"] == 0
    refused = _run_pwsh(
        f"$ErrorActionPreference='Stop'; & '{wrapper}' tools/not_packaged.py", env=anchor
    )
    assert refused.returncode != 0
    assert "outside the packaged entrypoints" in (refused.stdout + refused.stderr)
    # A deployment manifest that differs from the launcher's external anchor
    # must refuse before any tool runs.
    mismatched = _run_pwsh(
        f"$ErrorActionPreference='Stop'; & '{wrapper}' tools/bridge_next_action.py",
        env={"WD_REBOOT_EXPECTED_MANIFEST_HASH": "B" * 64},
    )
    assert mismatched.returncode != 0
    assert "differs from its external anchor" in (mismatched.stdout + mismatched.stderr)
    # Without WD_BRIDGE_PYTHON the wrapper falls back to the anchored fleet pin.
    fallback = _run_pwsh(
        f"$ErrorActionPreference='Stop'; & '{wrapper}' tools/bridge_next_action.py",
        env={"WD_REBOOT_EXPECTED_MANIFEST_HASH": _manifest_anchor(bundle)},
    )
    assert fallback.returncode == 0, fallback.stdout + fallback.stderr
    assert json.loads(fallback.stdout.strip().splitlines()[-1])["argv"] == []


def test_task_worktree_python_imports_are_unaffected_by_discovery_variables():
    """The model shell only sees WD_BRIDGE_*; ordinary repo imports must not move."""
    environment = dict(os.environ)
    environment.update(
        {
            "WD_BRIDGE_CODE_ROOT": r"C:\bundle\tools-bootstrap",
            "WD_BRIDGE_BIN": r"C:\bundle\tools-bootstrap\.agent-bridge\bin",
            "WD_BRIDGE_PYTHON": sys.executable,
            "WD_BRIDGE_PYTHON_WRAPPER": r"C:\bundle\Invoke-WdBridgePython.ps1",
            "WD_BRIDGE_PYTHON_SITE": r"C:\bundle\tools-bootstrap\python-site",
        }
    )
    for polluting in ("PYTHONPATH", "PYTHONSAFEPATH", "PYTHONNOUSERSITE"):
        environment.pop(polluting, None)
    probe = (
        "import json, waggledance, tools.bridge_next_action as t;"
        "print(json.dumps({'waggledance': waggledance.__file__, 'tool': t.__file__}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert Path(report["waggledance"]).resolve().is_relative_to(ROOT)
    assert Path(report["tool"]).resolve().is_relative_to(ROOT)
