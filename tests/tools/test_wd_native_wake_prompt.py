"""Pure, pinned native-wake prompt construction; never touches a live queue."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops" / "windows" / "reboot"
HELPER = REBOOT / "Get-WdNativeWakePrompt.ps1"
DELIVERY = "a" * 32
SHELLS = list(dict.fromkeys(filter(None, [
    shutil.which("pwsh") or shutil.which("powershell"),
    str(Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
    if sys.platform == "win32" and Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").is_file()
    else None,
])))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def bundle_fixture(tmp_path: Path) -> tuple[Path, str]:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    files = {}
    for name in ("WAKE_PROCEDURE_LEAD.md", "WAKE_PROCEDURE_TOOLS.md"):
        shutil.copyfile(REBOOT / name, bundle / name)
        files[name] = digest(bundle / name)
    manifest = bundle / "deployment-manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "files": files}), encoding="utf-8")
    return bundle, digest(manifest)


def run_helper(ps: str, bundle: Path, anchor: str, *, agent: str = "codex-tools-1",
               delivery: str = DELIVERY) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [ps, "-NoProfile", "-NonInteractive", "-File", str(HELPER),
         "-Agent", agent, "-DeliveryId", delivery, "-BundleRoot", str(bundle),
         "-ExpectedManifestHash", anchor],
        cwd=ROOT, text=True, capture_output=True, check=False, timeout=30,
    )


@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("agent,name", [
    ("codex-lead-1", "WAKE_PROCEDURE_LEAD.md"),
    ("codex-tools-1", "WAKE_PROCEDURE_TOOLS.md"),
])
def test_exact_agent_and_delivery_get_only_pinned_absolute_procedure(tmp_path, ps, agent, name):
    bundle, anchor = bundle_fixture(tmp_path)
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in bundle.iterdir()}
    result = run_helper(ps, bundle, anchor, agent=agent)
    assert result.returncode == 0, result.stdout + result.stderr
    message = result.stdout.strip()
    assert message.startswith(f"Automatic bridge wake for {agent}; delivery_id={DELIVERY}.")
    assert str(bundle / name) in message
    assert digest(bundle / name) in message
    assert "Read and follow the verified procedure" in message
    assert "WAKE_PROCEDURE_TOOLS.md" not in message if agent == "codex-lead-1" else "WAKE_PROCEDURE_LEAD.md" not in message
    assert len(message.encode("utf-8")) < 512
    assert before == {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in bundle.iterdir()}


@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("agent,delivery", [
    ("other-agent", DELIVERY),
    ("CODEX-LEAD-1", DELIVERY),
    ("codex-tools-1", "abc"),
    ("codex-tools-1", "A" * 32),
    ("codex-tools-1", "a" * 31 + "/"),
])
def test_invalid_agent_or_delivery_refuses_without_prompt(tmp_path, ps, agent, delivery):
    bundle, anchor = bundle_fixture(tmp_path)
    result = run_helper(ps, bundle, anchor, agent=agent, delivery=delivery)
    assert result.returncode != 0
    assert not result.stdout.strip()


@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("damage", [
    "wrong_anchor", "missing_manifest", "missing_pin", "missing_procedure",
    "mutated_procedure", "oversized_procedure", "relative_root",
])
def test_missing_mutated_or_unsafe_bundle_refuses(tmp_path, ps, damage):
    bundle, anchor = bundle_fixture(tmp_path)
    manifest = bundle / "deployment-manifest.json"
    procedure = bundle / "WAKE_PROCEDURE_TOOLS.md"
    if damage == "wrong_anchor":
        anchor = "0" * 64
    elif damage == "missing_manifest":
        manifest.unlink()
    elif damage == "missing_pin":
        content = json.loads(manifest.read_text(encoding="utf-8"))
        del content["files"][procedure.name]
        manifest.write_text(json.dumps(content), encoding="utf-8")
        anchor = digest(manifest)
    elif damage == "missing_procedure":
        procedure.unlink()
    elif damage == "mutated_procedure":
        procedure.write_text(procedure.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
    elif damage == "oversized_procedure":
        procedure.write_bytes(b"x" * 65537)
    elif damage == "relative_root":
        bundle = Path("relative-bundle")
    result = run_helper(ps, bundle, anchor)
    assert result.returncode != 0
    assert not result.stdout.strip()


def test_procedures_retain_control_and_reply_instructions():
    tools = (REBOOT / "WAKE_PROCEDURE_TOOLS.md").read_text(encoding="utf-8")
    lead = (REBOOT / "WAKE_PROCEDURE_LEAD.md").read_text(encoding="utf-8")
    assert "TRUNCATED ROUTING SUMMARY" in tools
    assert "Start-BridgeRequestTurn.ps1" in tools
    assert "Write-BridgeTaskReply.ps1" in tools
    assert "Preserve explicit task HOLDs, cancellations and peer write scopes." in tools
    assert "Get-BridgeReplySnapshot.ps1" in lead
    assert "Record-BridgeReplyObservation.ps1" in lead
    assert "user_reported" in lead
    assert "HOLDs, cancellations and peer write scopes" in lead
    assert "Queue acceptance is not task completion." in tools
    assert "Queue acceptance is not task completion." in lead


@pytest.mark.skipif(sys.platform != "win32", reason="Windows drive-relative path contract")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda p: Path(p).stem)
def test_root_relative_bundle_does_not_inherit_current_drive(tmp_path, ps):
    bundle, anchor = bundle_fixture(tmp_path)
    result = run_helper(ps, Path(str(bundle)[2:]), anchor)
    assert result.returncode != 0
    assert not result.stdout.strip()
