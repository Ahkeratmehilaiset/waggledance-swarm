# SPDX-License-Identifier: Apache-2.0
"""Actual pinned wrapper admission, with inert drivers; never a real merge.

The synthetic package is fully hash checked (including a real wheel-shaped
fixture and its extracted import). These tests do not mock a gate verdict.
Existing Python executor tests own consensus/CI/RCO/receipt semantics.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops/windows/reboot"
BRIDGE_PYTHON = json.loads((OPS / "wd-fleet.json").read_text(encoding="utf-8"))["bridge_python"]["executable"]
MERGE = "tools/merge_with_bridge_receipt.py"
RECEIPT = "tools/write_bridge_consensus_merge_receipt.py"
MANUAL = {"merge": MERGE, "receipt": RECEIPT}
EXECUTORS = (*MANUAL.values(), "tools/idle_consensus_auto_merge.py",
             "tools/bridge_rule12_review_eligibility.py",
             "tools/bridge_v2_identity_registry.py",
             "tools/rule12_grok_ledger_adapter.py")
MARKER = "--wd-manual-execution"
HEAD = "1" * 40
BASE = "2" * 40
CAPABILITY = {"schema": "wd.bridge-manual-execution.v1",
              "entrypoints": MANUAL, "invocation_marker": MARKER}
SHELLS = ("pwsh", "powershell")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(params=SHELLS)
def shell(request):
    if os.name != "nt":
        pytest.skip("Windows pinned package launch requires Windows paths")
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} unavailable")
    if not Path(BRIDGE_PYTHON).is_file():
        pytest.skip("Pinned fleet Python interpreter unavailable")
    return executable


@pytest.fixture
def bundle(tmp_path: Path):
    """No installed bundle is read or edited. Local package has inert drivers."""
    path = tmp_path / ("0" * 40)
    path.mkdir()
    code = path / "tools-bootstrap"
    (code / "tools").mkdir(parents=True)
    (code / "tools/__init__.py").write_text("", encoding="utf-8")
    for tool in (*EXECUTORS, "tools/bridge_next_action.py"):
        (code / tool).write_text(
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['WD_TEST_LAUNCHED']).write_text('launched')\n"
            "print(json.dumps(dict(argv=sys.argv[1:], cwd=os.getcwd(), "
            "no_site=bool(sys.flags.no_site), bytecode=sys.dont_write_bytecode)))\n",
            encoding="utf-8",
        )
    wheels = code / "python-wheels"
    site = code / "python-site"
    wheels.mkdir()
    (site / "manualfixture").mkdir(parents=True)
    (site / "manualfixture/__init__.py").write_text("MARKER = 'fixture'\n")
    wheel = wheels / "manualfixture-0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("manualfixture/__init__.py", "MARKER = 'fixture'\n")
        archive.writestr("manualfixture-0.1.dist-info/METADATA",
                         "Metadata-Version: 2.1\nName: manualfixture\nVersion: 0.1\n")
        archive.writestr("manualfixture-0.1.dist-info/WHEEL",
                         "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("manualfixture-0.1.dist-info/RECORD", "")
    definition = {
        "schema": "wd.bridge-code-package.v1",
        "package_relative_root": "tools-bootstrap",
        "invocation_wrapper_relative": "Invoke-WdBridgePython.ps1",
        "python_files": ["tools/__init__.py", *EXECUTORS, "tools/bridge_next_action.py"],
        "python_entrypoints": {"next": "tools/bridge_next_action.py"},
        "manual_execution_capability": CAPABILITY,
        "python_requirements": [{"name": "manualfixture", "version": "0.1",
                                 "wheel": wheel.name, "sha256": _sha(wheel),
                                 "import_path": "manualfixture/__init__.py"}],
        "wheel_store_relative": "python-wheels", "python_site_relative": "python-site",
        "import_smoke": {"third_party_module": "manualfixture",
                         "package_modules": ["tools.bridge_next_action"]},
        "isolation_environment": {"PYTHONDONTWRITEBYTECODE": "1",
                                  "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1"},
    }
    _write(path / "bridge-code-files.json", definition)
    _write(path / "wd-fleet.json", {"bridge_python": {"executable": BRIDGE_PYTHON}})
    for name in ("BridgeCodeContext.ps1", "Invoke-WdBridgePython.ps1"):
        shutil.copy2(OPS / name, path / name)
    _anchor(path)
    return path


def _anchor(bundle: Path, activation: dict | None = None) -> None:
    files = {p.relative_to(bundle).as_posix(): _sha(p)
             for p in bundle.rglob("*")
             if p.is_file() and p.name != "deployment-manifest.json"}
    manifest = {"schema_version": 1, "source_commit": "0" * 40, "files": files}
    if activation is not None:
        manifest["manual_execution"] = activation
    _write(bundle / "deployment-manifest.json", manifest)


def _activation(bundle: Path) -> dict:
    return {"schema": "wd.bridge-manual-execution-activation.v1", "enabled": True,
            "definition_sha256": _sha(bundle / "bridge-code-files.json"),
            "source_commit": "0" * 40, "agent": "codex-lead-1",
            "tools": list(MANUAL.values()), "approval_reference_sha256": "A" * 64}


def _args(tool: str = MERGE) -> list[str]:
    prefix = ["17"] if tool == MERGE else ["--pr-status-file", "unused-status.json"]
    return [MARKER, *prefix, "--expected-head", HEAD, "--expected-base-sha", BASE,
            "--from-agent", "codex-lead-1", "--repo", "Ahkeratmehilaiset/waggledance-swarm",
            "--out-dir", "unused-receipt", "--consensus-proposal-id", "proposal-17",
            "--bridge-task-id", "example/task", "--json"]


def _invoke(shell, bundle: Path, tool=MERGE, args=None, *, anchor=True, agent="codex-lead-1", python_pin=True):
    signal = bundle.parent / "driver-launched.txt"
    assert not signal.exists()
    environment = dict(os.environ)
    # Fixture root must never inherit the real lane's deployed package or anchor.
    cleared = {"WD_BRIDGE_PYTHON", "WD_BRIDGE_PYTHON_SHA256",
               "WD_REBOOT_EXPECTED_MANIFEST_HASH", "PSMODULEPATH"}
    for name in list(environment):
        if name.upper() in cleared:
            del environment[name]
    environment.update(AGENT_BRIDGE_AGENT=agent, WD_TEST_LAUNCHED=str(signal))
    if python_pin:
        environment["WD_BRIDGE_PYTHON_SHA256"] = (
            _sha(Path(BRIDGE_PYTHON)) if python_pin is True else python_pin)
    if anchor:
        environment["WD_REBOOT_EXPECTED_MANIFEST_HASH"] = (
            _sha(bundle / "deployment-manifest.json") if anchor is True else anchor)
    actual = _args(tool) if args is None else args
    # Script argument array keeps PS5 and PS7 native quoting identical.
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = bundle.parent / "invoke.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$null=Get-Command Get-FileHash -ErrorAction Stop\n"
        f"$argv=@({','.join(quote(x) for x in actual)})\n"
        f"& {quote(bundle / 'Invoke-WdBridgePython.ps1')} -Tool {quote(tool)} @argv\n"
        "exit $LASTEXITCODE\n", encoding="utf-8",
    )
    run = subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive",
                          "-ExecutionPolicy", "Bypass", "-File", str(script)],
                         cwd=ROOT, env=environment, text=True, capture_output=True, timeout=45)
    return run, signal


@pytest.mark.parametrize("tool", MANUAL.values())
def test_manual_launch_requires_anchored_activation_and_exact_args(shell, bundle, tool):
    _anchor(bundle, _activation(bundle))
    run, signal = _invoke(shell, bundle, tool)
    assert run.returncode == 0, run.stdout + run.stderr
    assert signal.read_text() == "launched"
    output = json.loads(run.stdout.strip())
    assert output["argv"] == _args(tool)[1:]
    assert output["cwd"] == str(ROOT)
    assert output["no_site"] and output["bytecode"]


@pytest.mark.parametrize("tool", EXECUTORS)
def test_default_invocation_denies_all_six_executors_even_when_activated(shell, bundle, tool):
    _anchor(bundle, _activation(bundle))
    run, signal = _invoke(shell, bundle, tool, args=_args()[1:])
    assert run.returncode != 0
    assert not signal.exists(), run.stdout + run.stderr
    assert "outside the packaged entrypoints" in run.stdout + run.stderr


@pytest.mark.parametrize("change", ["missing", "disabled", "string_true", "wrong_schema",
                                   "wrong_definition", "wrong_commit", "wrong_path",
                                   "wrong_agent", "extra", "missing_reference"])
def test_activation_refuses_before_python(shell, bundle, change):
    activation = _activation(bundle)
    if change == "missing":
        activation = None
    elif change == "disabled":
        activation["enabled"] = False
    elif change == "string_true":
        activation["enabled"] = "true"
    elif change == "wrong_schema":
        activation["schema"] = "unknown"
    elif change == "wrong_definition":
        activation["definition_sha256"] = "B" * 64
    elif change == "wrong_commit":
        activation["source_commit"] = HEAD
    elif change == "wrong_path":
        activation["tools"][0] = "tools/idle_consensus_auto_merge.py"
    elif change == "wrong_agent":
        activation["agent"] = "operator"
    elif change == "extra":
        activation["allow_anything"] = True
    elif change == "missing_reference":
        del activation["approval_reference_sha256"]
    _anchor(bundle, activation)
    run, signal = _invoke(shell, bundle)
    assert run.returncode != 0
    assert not signal.exists(), run.stdout + run.stderr
    assert "manual bridge execution" in run.stdout + run.stderr


@pytest.mark.parametrize("change", ["no_anchor", "wrong_anchor", "no_python_pin", "wrong_python_pin", "wrong_lane", "short_head", "uppercase_head",
                                   "empty_base", "duplicate_head", "abbreviation",
                                   "historical_now", "grok", "rule12", "operator_exception",
                                   "extra_marker", "wrong_repo", "missing_task", "option_value",
                                   "idle_path", "ordinary_marker"])
def test_manual_arguments_refuse_before_python(shell, bundle, change):
    _anchor(bundle, _activation(bundle))
    args = _args()
    tool = MERGE
    if change == "short_head":
        args[args.index(HEAD)] = HEAD[:8]
    elif change == "uppercase_head":
        args[args.index(HEAD)] = "A" * 40
    elif change == "empty_base":
        args[args.index(BASE)] = ""
    elif change == "duplicate_head":
        args += ["--expected-head=" + HEAD]
    elif change == "abbreviation":
        args[args.index("--expected-head")] = "--expected-h"
    elif change == "historical_now":
        args += ["--now", "2020-01-01T00:00:00Z"]
    elif change == "grok":
        args += ["--grok-fallback"]
    elif change == "rule12":
        args += ["--review-policy", "rule12"]
    elif change == "operator_exception":
        args += ["--operator-path-exception-json", "{}"]
    elif change == "extra_marker":
        args += [MARKER]
    elif change == "wrong_repo":
        args[args.index("Ahkeratmehilaiset/waggledance-swarm")] = "other/repo"
    elif change == "missing_task":
        i = args.index("--bridge-task-id")
        del args[i:i+2]
    elif change == "option_value":
        args[args.index("--out-dir")+1] = "--help"
    elif change == "idle_path":
        tool = "tools/idle_consensus_auto_merge.py"
    elif change == "ordinary_marker":
        tool = "tools/bridge_next_action.py"
    anchor = False if change == "no_anchor" else "B" * 64 if change == "wrong_anchor" else True
    run, signal = _invoke(shell, bundle, tool, args, anchor=anchor,
                          agent="codex-tools-1" if change == "wrong_lane" else "codex-lead-1",
                          python_pin=False if change == "no_python_pin" else "B" * 64 if change == "wrong_python_pin" else True)
    assert run.returncode != 0
    assert not signal.exists(), run.stdout + run.stderr
    assert any(message in run.stdout + run.stderr for message in
               ("manual bridge execution", "manual bridge merge", "manual bridge receipt",
                "differs from its external anchor", "Python changed after the lane handshake"))


@pytest.mark.parametrize("relative", ["bridge-code-files.json", MERGE, "Invoke-WdBridgePython.ps1",
                                      "python-site/manualfixture/__init__.py"])
def test_manual_package_tamper_refuses_before_python(shell, bundle, relative):
    _anchor(bundle, _activation(bundle))
    path = bundle / relative if relative in ("bridge-code-files.json", "Invoke-WdBridgePython.ps1") else bundle / "tools-bootstrap" / relative
    path.write_bytes(path.read_bytes() + b"\n# tamper\n")
    run, signal = _invoke(shell, bundle)
    assert run.returncode != 0
    assert not signal.exists(), run.stdout + run.stderr
    assert "hash mismatch" in run.stdout + run.stderr


def test_manual_tools_junction_refuses_before_python(shell, bundle):
    _anchor(bundle, _activation(bundle))
    tools = bundle / "tools-bootstrap/tools"
    target = bundle.parent / "inert-tools-target"
    tools.rename(target)
    quote = lambda p: "'" + str(p).replace("'", "''") + "'"
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                             f"$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                             f"-Path {quote(tools)} -Target {quote(target)} | Out-Null"],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    run, signal = _invoke(shell, bundle)
    assert run.returncode != 0
    assert not signal.exists()
    assert "reparse point" in run.stdout + run.stderr


def test_ordinary_diagnostic_still_launches_without_activation(shell, bundle):
    run, signal = _invoke(shell, bundle, "tools/bridge_next_action.py", ["--json"])
    assert run.returncode == 0, run.stdout + run.stderr
    assert signal.exists()
    assert json.loads(run.stdout)["argv"] == ["--json"]
