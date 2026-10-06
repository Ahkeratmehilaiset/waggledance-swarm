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
    assert source.count("Initialize-WdBridgeCodeContext") == 1
    assert source.index("Initialize-WdBridgeCodeContext") < source.index("$commonConsumerArguments =")


def test_bridge_output_is_utf8_even_with_legacy_parent_encoding():
    import os
    definition = json.loads((REBOOT / "bridge-code-files.json").read_text())
    environment = dict(os.environ, PYTHONIOENCODING="cp1252")
    environment.update(definition["isolation_environment"])
    result = subprocess.run([sys.executable, "-S", "-B", "-c",
                             "print('\\u03bb\\U0001f41d')"],
                            env=environment, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode("utf-8").strip() == "\u03bb\U0001f41d"


def test_package_entrypoints_exist_and_include_release_helpers():
    definition = json.loads((REBOOT / "bridge-code-files.json").read_text())
    for name in definition["python_files"]:
        assert (ROOT / name).is_file(), name
    assert set(definition["python_entrypoints"].values()) <= set(definition["python_files"])
    assert "tools/build_bridge_message_template.py" in definition["python_files"]
    assert "tools/agent_next_task.py" in definition["python_files"]


@pytest.mark.skipif(
    PS is None or sys.platform != "win32",
    reason="Exercises the Windows-only fleet launcher's native path checks",
)
def test_fleet_integrity_accepts_hash_pinned_empty_and_binary_files(tmp_path):
    import hashlib

    files = {"__init__.py": b"", "dependency.pyd": bytes(range(256))}
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    manifest = {"schema_version": 1, "files": {
        name: hashlib.sha256(content).hexdigest().upper() for name, content in files.items()
    }}
    manifest_path = tmp_path / "deployment-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    anchor = hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper()
    launcher = str(REBOOT / "start-wd-all.ps1").replace("'", "''")
    bundle = str(tmp_path).replace("'", "''")
    script = f"""
    $ErrorActionPreference = 'Stop'
    $ast = [Management.Automation.Language.Parser]::ParseFile('{launcher}',[ref]$null,[ref]$null)
    foreach ($function in $ast.FindAll({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]}},$false)) {{
        . ([scriptblock]::Create($function.Extent.Text.Replace('$PSScriptRoot', "'{bundle}'")))
    }}
    $env:WD_REBOOT_EXPECTED_MANIFEST_HASH = '{anchor}'
    $fleet = [pscustomobject]@{{ deployment = [pscustomobject]@{{ manifest_file='deployment-manifest.json'; required_bundle_files=@('__init__.py','dependency.pyd') }} }}
        if ((Assert-DeployedBundle -Manifest $fleet) -cne 'deployed') {{ throw 'verification failed' }}
    """
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


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
            elif isinstance(node, ast.ImportFrom):
                # A relative import resolves inside the importing file's own package.
                base = Path(relative).parent
                for _ in range(node.level - 1):
                    base = base.parent
                package = ".".join(base.parts)
                target = f"{package}.{node.module}" if node.module else package
                modules.add(target)
                modules.update(f"{target}.{alias.name}" for alias in node.names)
        for module in modules:
            if not module:
                continue
            top = module.split(".")[0]
            if top not in {"tools", "waggledance"} and (ROOT / "tools" / f"{top}.py").is_file():
                # A flat-layout fallback ("from bridge_capacity_advisor import ...") names a sibling
                # tools module: it is intra-repo code and must be packaged, never a third-party name.
                module, top = f"tools.{module}", "tools"
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
    # Seed with every entrypoint AND every module the import smoke loads: the gate modules reach
    # jsonschema only through modules that no entrypoint imports.
    smoke = [_module_relative(module) for module in DEFINITION["import_smoke"]["package_modules"]]
    assert None not in smoke, DEFINITION["import_smoke"]["package_modules"]
    seeds = sorted(set(DEFINITION["python_entrypoints"].values()) | set(smoke))
    closure, third_party = _intra_repo_closure(seeds)
    packaged = set(DEFINITION["python_files"])
    assert closure <= packaged, sorted(closure - packaged)
    assert third_party == {"pydantic", "jsonschema"}, sorted(third_party)
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
            "PYTHONIOENCODING": "utf-8",
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
$env:PYTHONIOENCODING = 'cp1252'
$env:PYTHONSAFEPATH = $null
Set-Location -LiteralPath '{str(caller_cwd).replace("'", "''")}'
$output = & '{wrapper}' tools/bridge_next_action.py --agent fable-5
if ($LASTEXITCODE -ne 0) {{ throw "wrapper exit $LASTEXITCODE" }}
$report = $output | ConvertFrom-Json
$restored = [pscustomobject]@{{
    tool = $report
    exit_code = $LASTEXITCODE
    after_pythonpath = [string]$env:PYTHONPATH
    after_encoding = [string]$env:PYTHONIOENCODING
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
    assert report["after_encoding"] == "cp1252"
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


# --- Case-duplicate parent environment (Tools StageOnly PS5 exit 1, 2026-10-06: BridgeCodeContext.ps1:185
# "Cannot index into a null array"). An MSYS or Git Bash parent can pass both Path and PATH. Python's subprocess
# dedupes such keys, so the block is built raw and passed to CreateProcessW.

ENV_SHELLS = [shell for shell in ("powershell.exe", "pwsh.exe") if sys.platform == "win32" and shutil.which(shell)]
PROBES = {f"WD_ENVFIX_PROBE_{index:03d}": f"probe-{index}" for index in range(200)}
FIXTURE_SECRET = "fixture-not-a-secret-value"
CHILD_SOURCE = """import json, os, sys
keys = ("WD_ENVFIX_SECRET", "PIP_REQUIRE_VIRTUALENV", "WD_ENVFIX_CHILD_ONLY", "PYTHONDONTWRITEBYTECODE")
probes = {k: v for k, v in os.environ.items() if k.startswith("WD_ENVFIX_PROBE_")}
print(json.dumps({"probes": probes, "keys": sorted(os.environ), "has_path": bool(os.environ.get("PATH")),
                  **{k: os.environ.get(k) for k in keys}}))
"""


def _start_with_raw_environment(shell: str, command: str, pairs: list[tuple[str, str]]) -> int:
    import base64
    import ctypes
    from ctypes import wintypes

    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR), ("desktop", wintypes.LPWSTR),
                    ("title", wintypes.LPWSTR)] + [(name, wintypes.DWORD) for name in
                    ("x", "y", "x_size", "y_size", "x_chars", "y_chars", "fill", "flags")] + [
                    ("show", wintypes.WORD), ("reserved2_size", wintypes.WORD), ("reserved2", ctypes.c_void_p),
                    ("stdin", wintypes.HANDLE), ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE)]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
                    ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    block = "".join(f"{key}={value}\0" for key, value in pairs) + "\0"
    encoded = base64.b64encode(command.encode("utf-16-le")).decode()
    line = ctypes.create_unicode_buffer(f'"{shell}" -NoProfile -NonInteractive -EncodedCommand {encoded}')
    startup, info = StartupInfo(cb=ctypes.sizeof(StartupInfo)), ProcessInformation()
    if not kernel32.CreateProcessW(None, line, None, None, False, 0x00000400 | 0x08000000,  # unicode env, no window
                                   ctypes.create_unicode_buffer(block, len(block)), None,
                                   ctypes.byref(startup), ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        assert kernel32.WaitForSingleObject(info.process, 180_000) == 0, "child timed out"
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.process, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(info.process)
        kernel32.CloseHandle(info.thread)


@pytest.mark.skipif(not ENV_SHELLS, reason="Windows PowerShell is required")
@pytest.mark.parametrize("shell", ENV_SHELLS, ids=lambda value: value.split(".")[0])
@pytest.mark.parametrize("duplicate", (True, False), ids=("Path_and_PATH", "single_Path"))
def test_python_call_gets_a_complete_isolated_environment_from_a_case_duplicate_parent(
    tmp_path: Path, shell: str, duplicate: bool,
):
    child = tmp_path / "child.py"
    child.write_text(CHILD_SOURCE, encoding="utf-8")
    result_path = tmp_path / "result.json"
    quote = lambda value: str(value).replace("'", "''")  # noqa: E731
    command = f"""
$ErrorActionPreference = 'Stop'
. '{quote(REBOOT / "BridgeCodeContext.ps1")}'
$out = [ordered]@{{ path_keys = @(@([Environment]::GetEnvironmentVariables().Keys) | Where-Object {{ $_ -match '^path$' }}) }}
try {{
    $call = Invoke-WdBridgeCodePython -PythonExecutable '{quote(sys.executable)}' -Arguments @('-B', '{quote(child)}') `
        -Label 'environment fixture' -Environment @{{
            PYTHONDONTWRITEBYTECODE = '1'; PIP_REQUIRE_VIRTUALENV = ''; WD_ENVFIX_SECRET = ''; WD_ENVFIX_CHILD_ONLY = 'child'
        }}
    $out.child = $call.StdOut.Trim()
}} catch {{ $out.error = $_.Exception.Message }}
$out.parent_after = [ordered]@{{}}
foreach ($key in @('WD_ENVFIX_SECRET', 'PIP_REQUIRE_VIRTUALENV', 'WD_ENVFIX_CHILD_ONLY', 'PYTHONDONTWRITEBYTECODE', 'WD_ENVFIX_PROBE_199')) {{
    $out.parent_after[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
}}
[IO.File]::WriteAllText('{quote(result_path)}', ($out | ConvertTo-Json -Depth 5 -Compress), (New-Object Text.UTF8Encoding($false)))
"""
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("WD_", "PIP_", "PYTHON")) and key.upper() != "PATH"}
    path = os.environ.get("PATH", "")
    pairs = [("Path", path)] + ([("PATH", path)] if duplicate else []) + sorted(environment.items())
    pairs += sorted(PROBES.items()) + [("WD_ENVFIX_SECRET", FIXTURE_SECRET), ("PIP_REQUIRE_VIRTUALENV", "1")]
    assert _start_with_raw_environment(shutil.which(shell), command, pairs) == 0
    report = json.loads(result_path.read_text(encoding="utf-8"))
    assert len(report["path_keys"]) == (2 if duplicate else 1), report     # the fixture really is case-duplicate
    assert "error" not in report, report["error"]
    seen = json.loads(report["child"])
    assert seen["probes"] == PROBES
    # Complete: .NET Framework keeps a PARTIAL copy after the duplicate-key throw (58 of 299 measured), so every
    # parent variable must reach the child, not only the ones that happened to be copied first.
    expected = {key.upper() for key, _ in pairs} - {"WD_ENVFIX_SECRET", "PIP_REQUIRE_VIRTUALENV"}
    assert expected <= {key.upper() for key in seen["keys"]}, sorted(expected - {key.upper() for key in seen["keys"]})
    assert seen["has_path"] is True
    assert seen["WD_ENVFIX_SECRET"] is None          # an empty override still removes a parent variable
    assert seen["PIP_REQUIRE_VIRTUALENV"] is None
    assert seen["WD_ENVFIX_CHILD_ONLY"] == "child"
    assert seen["PYTHONDONTWRITEBYTECODE"] == "1"
    assert report["parent_after"] == {               # only the child's dictionary changed, never this process
        "WD_ENVFIX_SECRET": FIXTURE_SECRET, "PIP_REQUIRE_VIRTUALENV": "1", "WD_ENVFIX_CHILD_ONLY": None,
        "PYTHONDONTWRITEBYTECODE": None, "WD_ENVFIX_PROBE_199": "probe-199",
    }

# --- The one admitted non-.py/.json package file is the exact charter path (Lead v2 2026-10-06).

CHARTER = "docs/architecture/IDLE_AUTONOMY_CHARTER.md"
PACKAGE_PATH_CASES = {
    CHARTER: None,
    "docs/architecture/OTHER.md": "must be .py or .json",
    "README.md": "must be .py or .json",
    "docs/architecture/idle_autonomy_charter.md": "must be .py or .json",
    "Docs/architecture/IDLE_AUTONOMY_CHARTER.md": "must be .py or .json",
    "x/docs/architecture/IDLE_AUTONOMY_CHARTER.md": "must be .py or .json",
    "docs/architecture/IDLE_AUTONOMY_CHARTER.md.txt": "must be .py or .json",
    "docs/architecture/IDLE_AUTONOMY_CHARTER.MD": "must be .py or .json",
    "../docs/architecture/IDLE_AUTONOMY_CHARTER.md": "unsafe bridge code package path",
    "docs/../docs/architecture/IDLE_AUTONOMY_CHARTER.md": "unsafe bridge code package path",
    "docs/./architecture/IDLE_AUTONOMY_CHARTER.md": "unsafe bridge code package path",
    "/docs/architecture/IDLE_AUTONOMY_CHARTER.md": "unsafe bridge code package path",
    "docs\\architecture\\IDLE_AUTONOMY_CHARTER.md": "unsafe bridge code package path",
    "C:/docs/architecture/IDLE_AUTONOMY_CHARTER.md": "unsafe bridge code package path",
    "docs//architecture/IDLE_AUTONOMY_CHARTER.md": "unsafe bridge code package path",
}


@pytest.mark.skipif(not ENV_SHELLS, reason="Windows PowerShell is required")
@pytest.mark.parametrize("shell", ENV_SHELLS, ids=lambda value: value.split(".")[0])
@pytest.mark.parametrize("relative", sorted(PACKAGE_PATH_CASES))
def test_only_the_exact_charter_path_is_admitted_beside_py_and_json(tmp_path: Path, shell: str, relative: str):
    definition = json.loads(json.dumps(DEFINITION))
    definition["python_files"] = [path for path in definition["python_files"] if path != relative] + [relative]
    path = tmp_path / "bridge-code-files.json"
    path.write_text(json.dumps(definition), encoding="utf-8")
    script = (f"$ErrorActionPreference = 'Stop'; . '{str(REBOOT / 'BridgeCodeContext.ps1').replace(chr(39), chr(39) * 2)}'; "
              f"try {{ $d = (Get-WdBridgeCodePackageDefinition -Path '{str(path).replace(chr(39), chr(39) * 2)}').Definition; "
              "'ADMITTED:' + @($d.python_files).Count } catch { 'REFUSED:' + $_.Exception.Message }")
    result = subprocess.run([shutil.which(shell), "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=120)
    lines = [line for line in result.stdout.splitlines() if line.startswith(("ADMITTED:", "REFUSED:"))]
    assert len(lines) == 1, result.stdout + result.stderr
    expected = PACKAGE_PATH_CASES[relative]
    if expected is None:
        assert lines[0] == f"ADMITTED:{len(definition['python_files'])}"
    else:
        assert lines[0].startswith("REFUSED:") and expected in lines[0], lines[0]

# --- Declared data files (charter + schema JSON) are admitted by exact path into the manifest entries and the
# integrity enumeration (Lead P0 2026-10-06: entries returned 90 of 96 for the ca6f664e definition).

DATA_FILES = {
    "docs/architecture/IDLE_AUTONOMY_CHARTER.md": b"# charter fixture\n",
    "schemas/v3_13_0/fixture.v1.json": b'{"type": "object"}\n',
}


def _stage_bundle_with_data(tmp_path: Path) -> Path:
    bundle = _stage_fake_bundle(tmp_path)
    code_root = bundle / "tools-bootstrap"
    definition = json.loads((bundle / "bridge-code-files.json").read_text(encoding="utf-8"))
    manifest = json.loads((bundle / "deployment-manifest.json").read_text(encoding="utf-8"))
    for relative, blob in DATA_FILES.items():
        (code_root / relative).parent.mkdir(parents=True, exist_ok=True)
        (code_root / relative).write_bytes(blob)
        definition["python_files"].append(relative)
        manifest["files"][f"tools-bootstrap/{relative}"] = hashlib.sha256(blob).hexdigest().upper()
    (bundle / "bridge-code-files.json").write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")
    manifest["files"]["bridge-code-files.json"] = hashlib.sha256(
        (bundle / "bridge-code-files.json").read_bytes()).hexdigest().upper()
    (bundle / "deployment-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return bundle


def _integrity(bundle: Path, extra: str = "") -> subprocess.CompletedProcess:
    context = str(REBOOT / "BridgeCodeContext.ps1").replace("'", "''")
    literal = str(bundle).replace("'", "''")
    return _run_pwsh(
        f"$ErrorActionPreference = 'Stop'; . '{context}'; "
        f"$deployment = Get-Content -LiteralPath '{literal}\\deployment-manifest.json' -Raw | ConvertFrom-Json; "
        f"$definition = (Get-WdBridgeCodePackageDefinition -Path '{literal}\\bridge-code-files.json').Definition; "
        f"{extra}"
        f"$r = Assert-WdBridgeCodePackageIntegrity -BundleRoot '{literal}' -Deployment $deployment "
        "-Definition $definition; 'FILES:' + $r.FileCount"
    )


@pytest.mark.skipif(
    PWSH is None or not HAS_BRIDGE_PYTHON,
    reason="PowerShell or the pinned bridge interpreter is unavailable",
)
def test_declared_data_files_are_verified_by_exact_path_and_extras_fail_closed(tmp_path: Path):
    bundle = _stage_bundle_with_data(tmp_path)
    code_root = bundle / "tools-bootstrap"
    manifest = json.loads((bundle / "deployment-manifest.json").read_text(encoding="utf-8"))
    package_entries = [name for name in manifest["files"] if name.startswith("tools-bootstrap/")]
    ok = _integrity(bundle)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert f"FILES:{len(package_entries)}" in ok.stdout          # every declared data file is an entry

    charter = code_root / "docs" / "architecture" / "IDLE_AUTONOMY_CHARTER.md"
    original = charter.read_bytes()
    charter.write_bytes(original + b"tampered\n")
    tampered = _integrity(bundle)
    assert tampered.returncode != 0 and "pinned bridge code hash mismatch: docs/architecture/IDLE_AUTONOMY_CHARTER.md" \
        in tampered.stdout + tampered.stderr
    charter.write_bytes(original)

    for stray in (code_root / "docs" / "architecture" / "OTHER.md", code_root / "docs" / "README.md",
                  code_root / "schemas" / "v3_13_0" / "extra.v1.json"):
        stray.write_bytes(b"extra\n")
        extra = _integrity(bundle)
        assert extra.returncode != 0, stray
        assert "unexpected file inside pinned bridge code package" in extra.stdout + extra.stderr, stray
        stray.unlink()

    # A manifest entry that the definition does not declare is never admitted by its folder.
    undeclared = code_root / "docs" / "architecture" / "UNDECLARED.md"
    undeclared.write_bytes(b"undeclared\n")
    digest = hashlib.sha256(b"undeclared\n").hexdigest().upper()
    listed = _integrity(bundle, "$deployment.files | Add-Member -NotePropertyName "
                                "'tools-bootstrap/docs/architecture/UNDECLARED.md' -NotePropertyValue "
                                f"'{digest}'; ")
    assert listed.returncode != 0
    assert "unexpected file inside pinned bridge code package: docs/architecture/UNDECLARED.md" \
        in listed.stdout + listed.stderr
    undeclared.unlink()

    missing = _integrity(bundle, "$deployment.files.PSObject.Properties.Remove("
                                 "'tools-bootstrap/schemas/v3_13_0/fixture.v1.json'); ")
    assert missing.returncode != 0
    assert "deployed bundle does not carry pinned bridge code file: schemas/v3_13_0/fixture.v1.json" \
        in missing.stdout + missing.stderr

    cache = code_root / "schemas" / "__pycache__"
    cache.mkdir()
    cached = _integrity(bundle)
    assert cached.returncode != 0 and "bytecode cache inside pinned bridge code package" in cached.stdout + cached.stderr
    cache.rmdir()

    target = tmp_path / "outside"
    target.mkdir()
    junction = code_root / "docs" / "linked"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    try:
        reparse = _integrity(bundle)
        assert reparse.returncode != 0
        assert "reparse point inside pinned bridge code package" in reparse.stdout + reparse.stderr
    finally:
        os.rmdir(junction)

    again = _integrity(bundle)
    assert again.returncode == 0, again.stdout + again.stderr
