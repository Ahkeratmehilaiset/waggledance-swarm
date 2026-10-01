# SPDX-License-Identifier: BUSL-1.1
"""F28 dry plan: ops/windows/reboot/Restore-WdRebootBundle.ps1 under pwsh 7 and Windows PowerShell 5.1.

Every fixture (bundles root, state pointer, lane journal, intent directory) lives under
tmp_path; each run asserts that no file under tmp_path changed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops" / "windows" / "reboot" / "Restore-WdRebootBundle.ps1"
WINDOWS_POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
SHELLS = [shell for shell in (shutil.which("pwsh"), str(WINDOWS_POWERSHELL) if WINDOWS_POWERSHELL.is_file() else None) if shell]
pytestmark = [pytest.mark.skipif(os.name != "nt" or not SHELLS, reason="Windows PowerShell hosts are required")]

TARGET, CURRENT = "8" * 40, "7" * 40
LEGACY_RELAY = ("if ($previous.schema -cne 'wd.native-tools-wake.v1' -or $previous.status -cnotin @('queued','watching')) {\n"
                "    throw 'Previous native bridge queue attempt is unresolved'\n}\n")
NAMED_RELAY = LEGACY_RELAY.replace("@('queued','watching')", "@('queued','watching','rejected','claiming')")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


class Fixture:
    def __init__(self, root: Path, relay: str = LEGACY_RELAY, status: str = "queued") -> None:
        self.root = root
        self.bundles = root / "bundles"
        self.bundle = self.bundles / TARGET
        self.bundle.mkdir(parents=True)
        files = {"start-wd-tools-consumer.ps1": relay.encode("utf-8"), "tools-bootstrap/a.txt": b"a\n"}
        for name, raw in files.items():
            (self.bundle / name).parent.mkdir(parents=True, exist_ok=True)
            (self.bundle / name).write_bytes(raw)
        manifest = json.dumps({"schema_version": 1, "source_commit": TARGET,
                               "files": {name: sha(raw) for name, raw in files.items()}}, indent=2).encode("utf-8")
        (self.bundle / "deployment-manifest.json").write_bytes(manifest)
        self.manifest_sha = sha(manifest)
        self.pointer = root / "WD_REBOOT_STATE_CURRENT.json"
        self.pointer.write_text(json.dumps({"final_commit": CURRENT, "source_commit": CURRENT}), encoding="utf-8")
        self.journal = root / "lead" / ".codex-audit" / "wd-turn-loop"
        self.journal.mkdir(parents=True)
        (self.journal / "native-bridge-wake.json").write_text(json.dumps({"schema": "wd.native-tools-wake.v1",
                                                                          "status": status}), encoding="utf-8")
        self.intents = root / "intents"
        self.intents.mkdir()

    def snapshot(self) -> dict:
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in sorted(self.root.rglob("*")) if path.is_file()}

    def run(self, shell: str, **overrides) -> tuple[int, dict]:
        params = {"TargetCommit": TARGET, "ExpectedTargetManifestSha256": self.manifest_sha,
                  "BundlesRoot": self.bundles, "StatePointerPath": self.pointer, "LaneJournals": self.journal,
                  "IntentDirectory": self.intents}
        params.update(overrides)
        argv = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)]
        for name, value in params.items():
            if value is None:  # leave the parameter out
                continue
            argv += ["-" + name] if value is True else ["-" + name, str(value)]
        before = self.snapshot()
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        assert self.snapshot() == before, "the dry plan must not change any file"
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        assert lines, result.stderr
        return result.returncode, json.loads(lines[-1])


@pytest.mark.parametrize("shell", SHELLS)
def test_a_compatible_anchored_bundle_is_planned(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = fixture.run(shell)
    assert code == 0 and plan["verdict"] == "plan" and plan["reasons"] == [], plan
    assert plan["schema"] == "wd.bundle-rollback-plan.v1" and plan["applied"] is False
    assert plan["target_manifest_sha256"] == fixture.manifest_sha
    assert plan["target_relay_statuses"] == ["queued", "watching"]
    assert any("operator signature" in action for action in plan["actions"])
    assert "restore old runtime data" in plan["never"] and "touch a lane worktree or its WIP" in plan["never"]


@pytest.mark.parametrize("shell", SHELLS)
def test_apply_is_refused(tmp_path, shell):
    code, plan = Fixture(tmp_path).run(shell, Apply=True)
    assert code == 3 and plan["reasons"] == ["apply_requires_signed_activation"] and plan["verdict"] == "hold"


@pytest.mark.parametrize("shell", SHELLS)
def test_provenance_and_integrity_failures_hold(tmp_path, shell):
    fixture = Fixture(tmp_path)
    assert "target_manifest_unanchored" in fixture.run(shell, ExpectedTargetManifestSha256="0" * 64)[1]["reasons"]
    (fixture.bundle / "tools-bootstrap" / "a.txt").write_bytes(b"changed\n")
    code, plan = fixture.run(shell)
    assert code == 2 and "bundle_file_changed:tools-bootstrap/a.txt" in plan["reasons"]
    (fixture.bundle / "tools-bootstrap" / "a.txt").unlink()
    assert "bundle_file_missing:tools-bootstrap/a.txt" in fixture.run(shell)[1]["reasons"]
    code, plan = fixture.run(shell, TargetCommit="9" * 40)
    assert code == 2 and "target_bundle_missing" in plan["reasons"]


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relay_state_the_legacy_target_cannot_own_holds(tmp_path, shell):
    fixture = Fixture(tmp_path, status="rejected")
    code, plan = fixture.run(shell)
    assert code == 2 and plan["reasons"] == ["relay_state_incompatible:" + str(fixture.journal) + ":rejected"]
    (fixture.journal / "native-bridge-wake.json").write_text('{"status": "queued"}', encoding="utf-8")
    (fixture.journal / ("native-bridge-wake.json.wake." + "a" * 32)).write_text("{}", encoding="utf-8")
    (fixture.journal / "native-bridge-wake.json.refusal-1").write_text("{}", encoding="utf-8")
    reasons = fixture.run(shell)[1]["reasons"]
    assert any(reason.startswith("named_snapshot_incompatible:") for reason in reasons)
    assert any(reason.startswith("refusal_receipt_incompatible:") for reason in reasons)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_named_snapshot_target_accepts_its_own_statuses_and_legacy_set_asides_are_ignored(tmp_path, shell):
    fixture = Fixture(tmp_path, relay=NAMED_RELAY, status="rejected")
    (fixture.journal / ("native-bridge-wake.json.wake." + "b" * 32)).write_text("{}", encoding="utf-8")
    (fixture.journal / "native-bridge-wake.json.wake.legacy-639263678490628930").write_text("{}", encoding="utf-8")
    code, plan = fixture.run(shell)
    assert code == 0 and plan["verdict"] == "plan", plan
    assert plan["target_relay_statuses"] == ["queued", "watching", "rejected", "claiming"]


@pytest.mark.parametrize("shell", SHELLS)
def test_other_holds(tmp_path, shell):
    fixture = Fixture(tmp_path)
    fixture.pointer.write_text(json.dumps({"final_commit": TARGET}), encoding="utf-8")
    assert "target_is_current" in fixture.run(shell)[1]["reasons"]
    fixture.pointer.write_text(json.dumps({"final_commit": CURRENT}), encoding="utf-8")
    (fixture.intents / "intent-1.json").write_text("{}", encoding="utf-8")
    assert fixture.run(shell)[1]["reasons"] == ["outstanding_intents"]
    (fixture.intents / "intent-1.json").unlink()
    (fixture.bundle / "start-wd-tools-consumer.ps1").write_text("# no relay contract here\n", encoding="utf-8")
    reasons = fixture.run(shell)[1]["reasons"]
    assert "target_relay_contract_unknown" in reasons and "bundle_file_changed:start-wd-tools-consumer.ps1" in reasons
    code, plan = fixture.run(shell, TargetCommit="not-a-commit")
    assert code == 2 and plan["reasons"] == ["target_commit_invalid"]


# --- fable-5 review 04:42:17Z (R-A, R-B, R-C): unknown rollback state is a HOLD, and the bundle and intents are
# enumerated recursively without following links.

def junction(link: Path, target: Path) -> None:
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, check=True)


@pytest.mark.parametrize("shell", SHELLS)
def test_no_lane_journal_is_a_hold_even_for_a_known_relay_contract(tmp_path, shell):
    code, plan = Fixture(tmp_path).run(shell, LaneJournals=None)
    assert code == 2 and plan["reasons"] == ["lane_journals_not_given"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_an_unknown_relay_contract_is_a_hold_with_or_without_journals(tmp_path, shell):
    fixture = Fixture(tmp_path, relay="# no relay contract here\n")
    code, plan = fixture.run(shell, LaneJournals=None)
    assert code == 2 and plan["reasons"] == ["target_relay_contract_unknown", "lane_journals_not_given"], plan
    code, plan = fixture.run(shell)
    assert code == 2 and plan["reasons"] == ["target_relay_contract_unknown"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_an_oversized_relay_source_is_an_unknown_contract(tmp_path, shell):
    fixture = Fixture(tmp_path, relay=LEGACY_RELAY + "#" * (4 * 1024 * 1024) + "\n")
    code, plan = fixture.run(shell)
    assert code == 2 and plan["reasons"] == ["target_relay_contract_unknown"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_unlisted_files_anywhere_in_the_bundle_hold(tmp_path, shell):
    fixture = Fixture(tmp_path)
    (fixture.bundle / "Invoke-Planted.ps1").write_text("throw 'planted'\n", encoding="utf-8")
    nested = fixture.bundle / "tools-bootstrap" / ".agent-bridge" / "bin"
    nested.mkdir(parents=True)
    (nested / "Extra.ps1").write_text("throw 'planted'\n", encoding="utf-8")
    (fixture.bundle / "empty-folder").mkdir()
    code, plan = fixture.run(shell)
    assert code == 2 and sorted(plan["reasons"]) == ["bundle_file_unexpected:Invoke-Planted.ps1",
                                                     "bundle_file_unexpected:tools-bootstrap/.agent-bridge/bin/Extra.ps1"]


@pytest.mark.parametrize("shell", SHELLS)
def test_a_link_inside_the_bundle_holds_and_is_never_followed(tmp_path, shell):
    fixture = Fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "unlisted.ps1").write_text("throw 'outside'\n", encoding="utf-8")
    link = fixture.bundle / "tools-bootstrap" / "linked"
    junction(link, outside)
    try:
        code, plan = fixture.run(shell)
    finally:
        os.rmdir(link)  # removes the junction only
    assert code == 2 and plan["reasons"] == ["bundle_reparse:tools-bootstrap/linked"], plan
    assert (outside / "unlisted.ps1").is_file()


@pytest.mark.parametrize("shell", SHELLS)
def test_an_intent_in_a_subfolder_holds_and_an_empty_folder_tree_does_not(tmp_path, shell):
    fixture = Fixture(tmp_path)
    (fixture.intents / "lane-a" / "deeper").mkdir(parents=True)
    code, plan = fixture.run(shell)
    assert code == 0 and plan["verdict"] == "plan", plan
    (fixture.intents / "lane-a" / "deeper" / "intent.json").write_text("{}", encoding="utf-8")
    code, plan = fixture.run(shell)
    assert code == 2 and plan["reasons"] == ["outstanding_intents"], plan
