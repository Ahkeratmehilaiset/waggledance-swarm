"""Exercise the operator helpers against disposable Windows fixtures."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "ops/windows/mind-operator"
POWERSHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell and NTFS required")

MACHINE_FILES = (
    "CheckedPowerShellResult.ps1", "Invoke-WdGrok.ps1", "start-wd-all.ps1",
    "start-wd-agent.ps1", "start-wd-codex-lead.ps1", "start-wd-codex-tools.ps1",
    "start-wd-claude-rco.ps1", "start-wd-claude-rco2.ps1", "start-wd-fable-5.ps1",
    "wd_supervisor.ps1", "start-wd-tools-consumer.ps1", "wd_supervisor_loop.json",
    "wd_supervisor_loop_snapshot.json", "BOOT_AFTER_REBOOT.md", "Set-WdTaskConsoleContainment.ps1",
    "WD_LOCAL_GPU_GUIDE.md", "WD_SWARM_PARALLEL_POLICY_V1.md", "Write-WdLaneCurrentState.ps1",
    "Get-WdSwarmParallelStatus.ps1", "WD_REBOOT_STATE_CURRENT.json", "WD_REBOOT_STATE_CURRENT.md",
    "WD_REBOOT_INTEGRITY_CURRENT.sha256", "WD_REBOOT_INTEGRITY_CURRENT.json",
    "WD_GROK_MODEL_CURRENT.json", "WD_GROK_MODEL_CURRENT.md",
)


@pytest.fixture
def workspace():
    # The helper fixtures live in this worktree's audit directory, even on a fresh checkout.
    audit = ROOT / ".codex-audit"
    audit.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="operator-aids-", dir=audit) as folder:
        # Saved inventories contain the process user's SID and ACL. Restrict the
        # fixture before creating any snapshots or native compiler temporaries.
        user = subprocess.check_output(["whoami"], text=True).strip()
        permissions = subprocess.run(
            ["icacls", folder, "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
            capture_output=True,
        )
        assert permissions.returncode == 0
        yield Path(folder)


def run_aid(workspace, script, *args, expected_code, expected_detail=None):
    result = subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", SCRIPTS / script, *args],
        cwd=workspace, env={**os.environ, "TEMP": str(workspace), "TMP": str(workspace)},
        capture_output=True, text=True, timeout=30,
    )
    if result.stderr or re.search(r"S-1-\d-|synthetic-payload|fixture-owner", result.stdout, re.I):
        pytest.fail("The helper emitted diagnostics or fixture data")
    assert result.returncode == expected_code
    if expected_detail is not None and expected_detail not in result.stdout.splitlines():
        pytest.fail("Expected inventory detail missing")
    return result.stdout.strip().splitlines()[-1]


def test_cache_creates_two_levels_and_preserves_existing_cache(workspace):
    parent = workspace / "cache-parent"
    parent.mkdir()
    args = ("New-WdMindCacheFolders.ps1", "-Mode", "Synthetic", "-SyntheticRoot", parent)
    assert run_aid(workspace, *args, expected_code=0) == (
        "wd-mind-cache-folders synthetic both_created owner=process_user dacl=protected "
        "entries=2 allowed=user_and_system inherit=files_and_folders"
    )
    marker = parent / "wd-mind-private/vector-cache/marker"
    marker.write_bytes(b"synthetic-payload")
    assert run_aid(workspace, *args, expected_code=2) == (
        "wd-mind-cache-folders synthetic nothing_created reason=level1_exists"
    )
    assert marker.read_bytes() == b"synthetic-payload"


def test_inventory_save_compare_and_refuse_overwrite(workspace):
    machine = workspace / "machine"
    machine.mkdir()
    for name in MACHINE_FILES:
        (machine / name).write_bytes(b"synthetic-payload")
    baseline = workspace / "baseline.txt"
    save = ("Get-WdMachineRootInventory.ps1", "-Mode", "Save", "-MachineRoot", machine, "-Out", baseline)
    compare = ("Get-WdMachineRootInventory.ps1", "-Mode", "Compare", "-MachineRoot", machine, "-Baseline", baseline)
    saved = run_aid(workspace, *save, expected_code=0)
    assert saved.startswith("wd-machine-inventory save saved_all_files files=25 absent=0 ")
    digest = re.search(r"inventory_sha256=([a-f0-9]{64})$", saved).group(1)
    unchanged = run_aid(workspace, *compare, expected_code=0)
    assert unchanged.startswith("wd-machine-inventory compare all_same same=25 ")
    assert unchanged.endswith(f"baseline_sha256={digest}")

    before = hashlib.sha256(baseline.read_bytes()).digest()
    refused = run_aid(workspace, *save, expected_code=2)
    assert refused == "wd-machine-inventory save nothing_read reason=out_exists"
    assert before == hashlib.sha256(baseline.read_bytes()).digest()

    target = machine / MACHINE_FILES[0]
    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    touched = run_aid(workspace, *compare, expected_code=5)
    assert touched.startswith("wd-machine-inventory compare same_content_touched same=25 ")
    assert "identity_changed=0 time_changed=1 " in touched
    target.unlink()
    missing = run_aid(workspace, *compare, expected_code=3)
    assert missing.startswith("wd-machine-inventory compare not_same same=24 different=0 missing=1 ")


def test_inventory_owner_difference_prints_only_the_change(workspace):
    machine = workspace / "machine"
    machine.mkdir()
    for name in MACHINE_FILES:
        (machine / name).touch()
    baseline = workspace / "baseline.txt"
    script = "Get-WdMachineRootInventory.ps1"
    run_aid(workspace, script, "-Mode", "Save", "-MachineRoot", machine, "-Out", baseline, expected_code=0)

    # Model a previous owner in the private synthetic inventory without changing the machine ACL.
    rows = baseline.read_bytes().splitlines()
    index = next(i for i, row in enumerate(rows) if row.startswith(MACHINE_FILES[0].encode() + b"|"))
    fields = rows[index].split(b"|")
    fields[7] = b"fixture-owner"
    rows[index] = b"|".join(fields)
    body = b"\n".join(rows[:-1]) + b"\n"
    baseline.write_bytes(body + b"inventory_sha256=" + hashlib.sha256(body).hexdigest().encode() + b"\n")

    run_aid(workspace, script, "-Mode", "Compare", "-MachineRoot", machine, "-Baseline", baseline,
            expected_code=3, expected_detail=f"  {MACHINE_FILES[0]} different fields=owner owner_changed")
