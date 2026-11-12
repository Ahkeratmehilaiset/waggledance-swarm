# SPDX-License-Identifier: BUSL-1.1
"""F28 dry plan: ops/windows/reboot/Restore-WdRebootBundle.ps1 under pwsh 7 and Windows PowerShell 5.1.

Every fixture (bundles root, state pointer, lane journal, intent directory) lives under
tmp_path; each run asserts that no file under tmp_path changed.
"""
from __future__ import annotations

import contextlib
import ctypes
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

    def run(self, shell: str, around=None, **overrides) -> tuple[int, dict]:
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
        with (around() if around else contextlib.nullcontext()):  # e.g. an injected deny ACE, undone before the check
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
def test_unreadable_relay_contract_produces_structured_hold(tmp_path, shell):
    fixture = Fixture(tmp_path)

    @contextlib.contextmanager
    def locked_relay():
        # Hold only this fixture's relay source; never lock the installed bundle.
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel32.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        handle = create(str(fixture.bundle / 'start-wd-tools-consumer.ps1'), 0x80000000, 0, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            assert close(handle)

    code, plan = fixture.run(shell, around=locked_relay)
    assert code == 2 and plan['verdict'] == 'hold'
    assert 'target_relay_contract_unknown' in plan['reasons']
    assert plan['authority_effect'] == 'none' and plan['applied'] is False


@pytest.mark.parametrize("shell", SHELLS)
def test_unreadable_manifest_produces_structured_hold(tmp_path, shell):
    fixture = Fixture(tmp_path)

    @contextlib.contextmanager
    def locked_manifest():
        # Exclusive handle is confined to this test's synthetic manifest.
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel32.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        handle = create(str(fixture.bundle / 'deployment-manifest.json'), 0x80000000, 0, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            assert close(handle)

    code, plan = fixture.run(shell, around=locked_manifest)
    assert code == 2 and plan['verdict'] == 'hold'
    assert 'target_manifest_unreadable' in plan['reasons']
    assert plan['authority_effect'] == 'none' and plan['applied'] is False


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
@pytest.mark.parametrize("record", [
    {"schema": "wd.other.v1", "status": "queued"},
    {"status": "queued"},
    {"schema": None, "status": "queued"},
    {"schema": ["wd.native-tools-wake.v1"], "status": "queued"},
    {"schema": "WD.NATIVE-TOOLS-WAKE.V1", "status": "queued"},
])
def test_a_relay_record_with_invalid_schema_is_a_hold(tmp_path, shell, record):
    fixture = Fixture(tmp_path)
    (fixture.journal / "native-bridge-wake.json").write_text(json.dumps(record), encoding="utf-8")
    code, plan = fixture.run(shell)
    assert code == 2 and plan["verdict"] == "hold", plan
    assert "relay_state_schema_invalid:" + str(fixture.journal) in plan["reasons"]


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relay_record_with_exact_schema_and_accepted_status_is_planned(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = fixture.run(shell)
    assert code == 0 and plan["verdict"] == "plan" and plan["reasons"] == [], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_a_named_snapshot_target_accepts_its_own_statuses_and_legacy_set_asides_are_ignored(tmp_path, shell):
    fixture = Fixture(tmp_path, relay=NAMED_RELAY, status="rejected")
    (fixture.journal / ("native-bridge-wake.json.wake." + "b" * 32)).write_text("{}", encoding="utf-8")
    (fixture.journal / "native-bridge-wake.json.wake.legacy-639263678490628930").write_text("{}", encoding="utf-8")
    code, plan = fixture.run(shell)
    assert code == 0 and plan["verdict"] == "plan", plan
    assert plan["target_relay_statuses"] == ["queued", "watching", "rejected", "claiming"]


@pytest.mark.parametrize("shell", SHELLS)
def test_valid_lowercase_manifest_and_pointer_are_accepted(tmp_path, shell):
    fixture = Fixture(tmp_path)
    fixture.pointer.write_text(json.dumps({"final_commit": "abcdef" * 6 + "abcd"}), encoding="utf-8")
    code, plan = fixture.run(shell, ExpectedTargetManifestSha256=fixture.manifest_sha.lower())
    assert code == 0 and plan["verdict"] == "plan"
    assert plan["target_manifest_sha256"] == fixture.manifest_sha


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("field,reason", [("TargetCommit", "target_commit_invalid"),
                                         ("ExpectedTargetManifestSha256", "target_manifest_digest_invalid")])
def test_input_digest_trailing_newline_holds(tmp_path, shell, field, reason):
    fixture = Fixture(tmp_path)
    value = TARGET if field == "TargetCommit" else fixture.manifest_sha
    code, plan = fixture.run(shell, **{field: value + "\n"})
    assert code == 2 and plan["verdict"] == "hold"
    assert plan["reasons"] == [reason]
    assert plan["authority_effect"] == "none" and plan["applied"] is False


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("pointer", [{}, {"final_commit": None}, {"final_commit": 7},
                                     {"final_commit": True}, {"final_commit": ["7" * 40]},
                                     {"Final_Commit": "7" * 40}, {"final_commit": "A" * 40},
                                     {"final_commit": "7" * 39}, {"final_commit": "7" * 40 + "\n"}])
def test_invalid_current_pointer_commit_holds(tmp_path, shell, pointer):
    fixture = Fixture(tmp_path)
    fixture.pointer.write_text(json.dumps(pointer), encoding="utf-8")
    code, plan = fixture.run(shell)
    assert code == 2 and plan["verdict"] == "hold"
    assert "state_pointer_commit_invalid" in plan["reasons"]
    assert plan["authority_effect"] == "none" and plan["applied"] is False


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
@pytest.mark.parametrize("kind", ["journal-root", "journal-ancestor", "intent-ancestor"])
def test_journal_or_intent_junction_ancestor_is_untrusted(tmp_path, shell, kind):
    fixture = Fixture(tmp_path)
    link = tmp_path / "synthetic-state-alias"
    if kind == "journal-root":
        junction(link, fixture.journal)
        overrides = {"LaneJournals": link}
        expected = "lane_journal_untrusted:" + str(link)
    elif kind == "journal-ancestor":
        junction(link, fixture.journal.parent)
        target = link / fixture.journal.name
        overrides = {"LaneJournals": target}
        expected = "lane_journal_untrusted:" + str(target)
    else:
        (fixture.intents / "synthetic-intent.json").write_text("{}", encoding="utf-8")
        junction(link, tmp_path)
        overrides = {"IntentDirectory": link / fixture.intents.name}
        expected = "intent_directory_invalid"
    try:
        code, plan = fixture.run(shell, **overrides)
    finally:
        os.rmdir(link)  # remove only this synthetic junction, never its target
    assert code == 2 and plan["verdict"] == "hold", plan
    assert expected in plan["reasons"], plan
    if kind == "intent-ancestor":
        assert "outstanding_intents" not in plan["reasons"]
    assert plan["applied"] is False and plan["authority_effect"] == "none"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("link_position", ["root", "ancestor"])
def test_bundle_root_and_ancestor_junctions_hold(tmp_path, shell, link_position):
    fixture = Fixture(tmp_path)
    # All objects, including the junction target, stay inside this synthetic fixture.
    link = tmp_path / "synthetic-alias"
    if link_position == "root":
        junction(link, fixture.bundles)
        alternate_root = link
    else:
        junction(link, tmp_path)
        alternate_root = link / "bundles"
    try:
        code, plan = fixture.run(shell, BundlesRoot=alternate_root)
    finally:
        os.rmdir(link)
    assert code == 2 and plan["verdict"] == "hold", plan
    assert plan["reasons"] == ["bundle_root_untrusted"], plan
    assert plan["applied"] is False and plan["authority_effect"] == "none"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("kind", ["missing", "file"])
def test_uninspectable_bundle_root_holds_before_target_reads(tmp_path, shell, kind):
    fixture = Fixture(tmp_path)
    path = tmp_path / "not-a-bundle-root"
    if kind == "file":
        path.write_text("not a directory", encoding="utf-8")
    code, plan = fixture.run(shell, BundlesRoot=path)
    assert code == 2 and plan["verdict"] == "hold", plan
    assert plan["reasons"] == ["bundle_root_untrusted"], plan
    assert plan["target_bundle"] is None and plan["applied"] is False


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
def test_manifest_listed_junction_target_is_not_hashed(tmp_path, shell):
    fixture = Fixture(tmp_path)
    outside = tmp_path / "synthetic-outside"
    outside.mkdir()
    (outside / "input.bin").write_bytes(b"synthetic target\n")
    link = fixture.bundle / "synthetic-link"
    junction(link, outside)
    manifest_path = fixture.bundle / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # A changed-hash reason proves the target was opened and read, not just inspected.
    manifest["files"]["synthetic-link/input.bin"] = "0" * 64
    raw = json.dumps(manifest, indent=2).encode("utf-8")
    manifest_path.write_bytes(raw)
    fixture.manifest_sha = sha(raw)
    try:
        code, plan = fixture.run(shell)
    finally:
        os.rmdir(link)
    assert code == 2 and plan["verdict"] == "hold", plan
    assert "bundle_reparse:synthetic-link" in plan["reasons"], plan
    assert "bundle_file_changed:synthetic-link/input.bin" not in plan["reasons"], plan
    assert plan["applied"] is False and plan["authority_effect"] == "none"


@pytest.mark.parametrize("shell", SHELLS)
def test_linked_manifest_is_not_hashed(tmp_path, shell):
    fixture = Fixture(tmp_path)
    manifest = fixture.bundle / "deployment-manifest.json"
    outside = tmp_path / "synthetic-manifest.json"
    outside.write_bytes(manifest.read_bytes())
    manifest.unlink()
    try:
        try:
            os.symlink(outside, manifest)
        except OSError as exc:
            pytest.skip(f"file symlinks unavailable: {exc}")
        code, plan = fixture.run(shell)
    finally:
        if manifest.is_symlink():
            manifest.unlink()
    assert code == 2 and plan["verdict"] == "hold", plan
    assert plan["target_manifest_sha256"] is None, plan
    assert "target_manifest_unreadable" in plan["reasons"], plan
    assert plan["applied"] is False and plan["authority_effect"] == "none"


@pytest.mark.parametrize("shell", SHELLS)
def test_manifest_listed_file_symlink_target_is_not_hashed(tmp_path, shell):
    fixture = Fixture(tmp_path)
    outside = tmp_path / "synthetic-outside.bin"
    outside.write_bytes(b"synthetic target\n")
    link = fixture.bundle / "synthetic-file-link.bin"
    try:
        try:
            os.symlink(outside, link)
        except OSError as exc:
            pytest.skip(f"file symlinks unavailable: {exc}")
        manifest_path = fixture.bundle / "deployment-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][link.name] = "0" * 64
        raw = json.dumps(manifest, indent=2).encode("utf-8")
        manifest_path.write_bytes(raw)
        fixture.manifest_sha = sha(raw)
        code, plan = fixture.run(shell)
    finally:
        if link.is_symlink():
            link.unlink()
    assert code == 2 and plan["verdict"] == "hold", plan
    assert "bundle_reparse:synthetic-file-link.bin" in plan["reasons"], plan
    assert "bundle_file_changed:synthetic-file-link.bin" not in plan["reasons"], plan
    assert plan["applied"] is False and plan["authority_effect"] == "none"


@pytest.mark.parametrize("shell", SHELLS)
def test_an_intent_in_a_subfolder_holds_and_an_empty_folder_tree_does_not(tmp_path, shell):
    fixture = Fixture(tmp_path)
    (fixture.intents / "lane-a" / "deeper").mkdir(parents=True)
    code, plan = fixture.run(shell)
    assert code == 0 and plan["verdict"] == "plan", plan
    (fixture.intents / "lane-a" / "deeper" / "intent.json").write_text("{}", encoding="utf-8")
    code, plan = fixture.run(shell)
    assert code == 2 and plan["reasons"] == ["outstanding_intents"], plan


# --- claude-rco-1 review 05:22:19Z of 0df900fe (F28-1, F28-2, F28-3): an intent directory that is not given or
# not there, and a relay record path that is not a regular file, are unknown state and HOLD; a link inside the
# intent directory is an outstanding intent (a directory junction needs no symlink privilege).

@pytest.mark.parametrize("shell", SHELLS)
def test_no_intent_directory_is_a_hold(tmp_path, shell):
    code, plan = Fixture(tmp_path).run(shell, IntentDirectory=None)
    assert code == 2 and plan["reasons"] == ["intent_directory_not_given"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_a_missing_intent_directory_is_a_hold(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = fixture.run(shell, IntentDirectory=str(fixture.intents) + "-typo")
    assert code == 2 and plan["reasons"] == ["intent_directory_missing"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_a_file_or_a_linked_folder_as_the_intent_directory_is_invalid(tmp_path, shell):
    fixture = Fixture(tmp_path)
    as_file = tmp_path / "intents-file"
    as_file.write_text("{}", encoding="utf-8")
    code, plan = fixture.run(shell, IntentDirectory=as_file)
    assert code == 2 and plan["reasons"] == ["intent_directory_invalid"], plan
    linked = tmp_path / "intents-linked"
    junction(linked, fixture.intents)  # the target is the empty, valid intent directory itself
    try:
        code, plan = fixture.run(shell, IntentDirectory=linked)
    finally:
        os.rmdir(linked)  # removes the junction only
    assert code == 2 and plan["reasons"] == ["intent_directory_invalid"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_a_link_inside_the_intent_directory_is_an_outstanding_intent_and_a_real_empty_folder_is_not(tmp_path, shell):
    fixture = Fixture(tmp_path)
    empty = tmp_path / "empty-elsewhere"
    empty.mkdir()
    (fixture.intents / "real-empty").mkdir()
    code, plan = fixture.run(shell)
    assert code == 0 and plan["verdict"] == "plan", plan
    link = fixture.intents / "linked-empty"
    junction(link, empty)  # an empty target: only the link itself can count
    try:
        code, plan = fixture.run(shell)
    finally:
        os.rmdir(link)  # removes the junction only
    assert code == 2 and plan["reasons"] == ["outstanding_intents"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_a_directory_at_the_relay_record_path_is_unreadable_state(tmp_path, shell):
    fixture = Fixture(tmp_path)
    record = fixture.journal / "native-bridge-wake.json"
    record.unlink()
    record.mkdir()
    (record / "x.json").write_text('{"status": "queued"}', encoding="utf-8")
    code, plan = fixture.run(shell)
    assert code == 2 and plan["reasons"] == ["relay_state_unreadable:" + str(fixture.journal)], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_a_linked_relay_record_is_unreadable_state_and_no_record_at_all_still_plans(tmp_path, shell):
    fixture = Fixture(tmp_path)
    record = fixture.journal / "native-bridge-wake.json"
    record.unlink()
    code, plan = fixture.run(shell)
    assert code == 0 and plan["verdict"] == "plan", plan  # no record: nothing for the target relay to own
    elsewhere = tmp_path / "record-elsewhere"
    elsewhere.mkdir()
    junction(record, elsewhere)
    try:
        code, plan = fixture.run(shell)
    finally:
        os.rmdir(record)  # removes the junction only
    assert code == 2 and plan["reasons"] == ["relay_state_unreadable:" + str(fixture.journal)], plan


# --- fable-5 residual of cf3102e1 (Lead 05:41:18Z): a record path the plan cannot inspect is unknown state and
# HOLDs; only a record that is provably absent (ItemNotFound) may plan. The access failures below are INJECTED by
# the test itself: deny ACEs for the current user on private files and folders under tmp_path, removed before the
# no-change check (never READ_CONTROL, so the ACE can always be removed). They are not an observation of any
# production ACL. A dangling directory junction needs no symlink privilege.

WHOAMI = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"


def _me() -> str:
    # The System32 whoami (DOMAIN\user): a Git for Windows "whoami" on PATH prints only the bare name.
    return subprocess.run([str(WHOAMI)], capture_output=True, text=True, check=True).stdout.strip()


def _deny_blocks_listing(folder: Path) -> bool:
    """Measured, not assumed: True when an injected list-deny ACE on ``folder`` actually stops this host's process
    from enumerating it. An elevated token with SeBackupPrivilege ENABLED lists through a deny ACE (directory
    enumeration opens with backup intent), so on such a host the real-ACL twin below cannot run (RCO1 E1)."""
    with injected_deny((folder, "RD")):
        try:
            os.listdir(folder)
        except PermissionError:
            return True
    return False


@contextlib.contextmanager
def injected_deny(*grants: tuple[Path, str]):
    user = _me()
    applied = []
    try:
        for path, rights in grants:
            subprocess.run(["icacls", str(path), "/deny", f"{user}:({rights})"], capture_output=True, check=True)
            applied.append(path)
        yield
    finally:
        for path in reversed(applied):
            subprocess.run(["icacls", str(path), "/remove:d", user], capture_output=True, check=True)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_dangling_junction_at_the_relay_record_path_is_unreadable_state(tmp_path, shell):
    fixture = Fixture(tmp_path)
    record = fixture.journal / "native-bridge-wake.json"
    record.unlink()
    gone = tmp_path / "record-target-removed"
    gone.mkdir()
    junction(record, gone)
    gone.rmdir()  # the junction now points nowhere
    try:
        code, plan = fixture.run(shell)
    finally:
        os.rmdir(record)  # removes the junction only
    assert code == 2 and plan["reasons"] == ["relay_state_unreadable:" + str(fixture.journal)], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_an_injected_read_denial_on_the_relay_record_is_unreadable_state(tmp_path, shell):
    fixture = Fixture(tmp_path)
    record = fixture.journal / "native-bridge-wake.json"
    code, plan = fixture.run(shell, around=lambda: injected_deny((record, "RD")))
    assert code == 2 and plan["reasons"] == ["relay_state_unreadable:" + str(fixture.journal)], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_an_injected_inspection_denial_at_the_record_path_is_unknown_never_a_missing_record(tmp_path, shell):
    # Read-attributes denied on the record and list denied on its journal: Get-Item cannot tell whether a record
    # exists (UnauthorizedAccess, not ItemNotFound). Before this fix it read as 'no record' and the journal walk
    # then crashed the plan (exit 1, no JSON).
    fixture = Fixture(tmp_path)
    record = fixture.journal / "native-bridge-wake.json"
    if not _deny_blocks_listing(fixture.journal):
        pytest.skip("measured: this host lists through an injected deny ACE (SeBackupPrivilege enabled in the "
                    "token); the deterministic cmdlet-shim twins below cover the same guards on every host")
    code, plan = fixture.run(shell, around=lambda: injected_deny((record, "RA,REA,RD"), (fixture.journal, "RD")))
    assert code == 2 and plan["reasons"] == ["relay_state_unreadable:" + str(fixture.journal),
                                             "lane_journal_unreadable:" + str(fixture.journal)], plan
    record.unlink()  # honest control: the same journal with no record at all, no denial, still plans
    code, plan = fixture.run(shell)
    assert code == 0 and plan["verdict"] == "plan", plan


# --- RCO1 E1 (Lead d00b48b1): deterministic inability-to-inspect twins, independent of the host token ----------
# The ACL twin above depends on the token: with SeBackupPrivilege enabled (measured on this fleet host) a deny ACE
# does not stop directory enumeration. These twins inject the failure at the cmdlet the plan calls instead: a
# test-only global Get-Item / Get-ChildItem shim fails for exactly ONE named path and passes every other call to
# the real cmdlet, so the plan's own error handling is exercised byte-for-byte on every host. No ACL, file or
# production path is touched; the no-change snapshot still holds.

_SHIM = r"""
function global:Get-Item {
    [CmdletBinding()]
    param([Parameter(ValueFromPipeline = $true)] [string[]] $Path, [string[]] $LiteralPath, [switch] $Force)
    if ($LiteralPath -and $env:WD_TEST_FORBIDDEN_DESCENDANT -and
        $LiteralPath[0].StartsWith($env:WD_TEST_FORBIDDEN_DESCENDANT, [StringComparison]::OrdinalIgnoreCase)) {
        [Console]::Error.WriteLine('WD_TEST_FORBIDDEN_DESCENDANT_INSPECTED')
    }
    if ($LiteralPath -and $env:WD_TEST_FORBIDDEN_RELAY -and
        [IO.Path]::GetFileName($LiteralPath[0]) -ieq 'start-wd-tools-consumer.ps1') {
        [Console]::Error.WriteLine('WD_TEST_FORBIDDEN_RELAY_INSPECTED')
    }
    if ($LiteralPath -and $LiteralPath[0] -eq $env:WD_TEST_INSPECT_DENIED) {
        $PSCmdlet.WriteError([Management.Automation.ErrorRecord]::new(
            [UnauthorizedAccessException]::new('injected: inspection denied'), 'ItemExistsUnauthorizedAccessError',
            [Management.Automation.ErrorCategory]::PermissionDenied, $LiteralPath[0]))
        return
    }
    Microsoft.PowerShell.Management\Get-Item @PSBoundParameters
}
function global:Get-ChildItem {
    [CmdletBinding()]
    param([string[]] $Path, [string[]] $LiteralPath, [switch] $Force, [switch] $File, [switch] $Directory,
          [switch] $Recurse)
    if ($LiteralPath -and $env:WD_TEST_FORBIDDEN_DESCENDANT -and
        $LiteralPath[0].StartsWith($env:WD_TEST_FORBIDDEN_DESCENDANT, [StringComparison]::OrdinalIgnoreCase)) {
        [Console]::Error.WriteLine('WD_TEST_FORBIDDEN_DESCENDANT_INSPECTED')
    }
    if ($LiteralPath -and $LiteralPath[0] -eq $env:WD_TEST_LIST_DENIED) {
        throw [UnauthorizedAccessException]::new('injected: list denied')
    }
    Microsoft.PowerShell.Management\Get-ChildItem @PSBoundParameters
}
"""


def _quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_shimmed(fixture: Fixture, shell: str, list_denied=None, inspect_denied=None, location=None,
                forbid_relay=None, forbid_descendant=None, **overrides):
    params = {"TargetCommit": TARGET, "ExpectedTargetManifestSha256": fixture.manifest_sha,
              "BundlesRoot": fixture.bundles, "StatePointerPath": fixture.pointer, "LaneJournals": fixture.journal,
              "IntentDirectory": fixture.intents}
    params.update(overrides)
    call = " ".join(("-" + name) if value is True else ("-" + name + " " + _quote(value))
                    for name, value in params.items() if value is not None)
    location_setup = "\nSet-Location -LiteralPath " + _quote(location) if location is not None else ""
    command = _SHIM + location_setup + "\n& " + _quote(SCRIPT) + " " + call + "\nexit $LASTEXITCODE\n"
    env = dict(os.environ, WD_TEST_LIST_DENIED=str(list_denied or ""), WD_TEST_INSPECT_DENIED=str(inspect_denied or ""),
               WD_TEST_FORBIDDEN_RELAY=str(forbid_relay or ""),
               WD_TEST_FORBIDDEN_DESCENDANT=str(forbid_descendant or ""))
    argv = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
    before = fixture.snapshot()
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120, env=env)
    assert fixture.snapshot() == before, "the dry plan must not change any file"
    if forbid_relay is not None:
        assert 'WD_TEST_FORBIDDEN_RELAY_INSPECTED' not in result.stderr, result.stderr
    if forbid_descendant is not None:
        assert 'WD_TEST_FORBIDDEN_DESCENDANT_INSPECTED' not in result.stderr, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, result.stdout + result.stderr   # one JSON line, never a crash without a plan
    return result.returncode, json.loads(lines[0])


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("kind", ["journal-root", "journal-ancestor", "intent-ancestor"])
def test_state_junction_is_rejected_before_planner_inspects_descendants(tmp_path, shell, kind):
    fixture = Fixture(tmp_path)
    link = tmp_path / "synthetic-state-alias"
    if kind == "journal-root":
        junction(link, fixture.journal)
        overrides = {"LaneJournals": link}
        forbidden = link / "native-bridge-wake.json"
    elif kind == "journal-ancestor":
        junction(link, fixture.journal.parent)
        overrides = {"LaneJournals": link / fixture.journal.name}
        forbidden = link / fixture.journal.name
    else:
        junction(link, tmp_path)
        overrides = {"IntentDirectory": link / fixture.intents.name}
        forbidden = link / fixture.intents.name
    try:
        code, plan = run_shimmed(fixture, shell, forbid_descendant=forbidden, **overrides)
    finally:
        os.rmdir(link)
    assert code == 2 and plan["verdict"] == "hold" and plan["applied"] is False, plan


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("kind", ["journal", "intent"])
def test_state_directory_inspection_denial_is_not_missing(tmp_path, shell, kind):
    fixture = Fixture(tmp_path)
    denied = fixture.journal if kind == "journal" else fixture.intents
    expected = "lane_journal_untrusted:" + str(denied) if kind == "journal" else "intent_directory_invalid"
    code, plan = run_shimmed(fixture, shell, inspect_denied=denied)
    assert code == 2 and expected in plan["reasons"], plan


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("kind", ["journal", "intent"])
def test_non_directory_state_ancestor_remains_missing(tmp_path, shell, kind):
    fixture = Fixture(tmp_path)
    parent_file = tmp_path / "synthetic-not-directory"
    parent_file.write_text("not a folder", encoding="utf-8")
    child = parent_file / "child"
    overrides = {"LaneJournals": child} if kind == "journal" else {"IntentDirectory": child}
    expected = "lane_journal_missing:" + str(child) if kind == "journal" else "intent_directory_missing"
    code, plan = run_shimmed(fixture, shell, **overrides)
    assert code == 2 and expected in plan["reasons"], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_state_directories_accept_relative_paths_at_powershell_location(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = run_shimmed(fixture, shell, location=tmp_path,
                             LaneJournals=fixture.journal.relative_to(tmp_path),
                             IntentDirectory=fixture.intents.relative_to(tmp_path))
    assert code == 0 and plan["verdict"] == "plan", plan


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("relay", [NAMED_RELAY, "# synthetic relay without contract\n"])
def test_invalid_bundle_junction_does_not_inspect_or_read_relay(tmp_path, shell, relay):
    fixture = Fixture(tmp_path, relay=relay)
    alias_root = tmp_path / "synthetic-bundles"
    alias_root.mkdir()
    alias = alias_root / TARGET
    junction(alias, fixture.bundle)
    try:
        code, plan = run_shimmed(fixture, shell, BundlesRoot=alias_root,
                                 forbid_relay=alias / "start-wd-tools-consumer.ps1")
    finally:
        os.rmdir(alias)
    assert code == 2 and plan["verdict"] == "hold", plan
    assert "target_bundle_missing" in plan["reasons"], plan
    assert "target_relay_contract_unknown" in plan["reasons"], plan
    assert plan["target_relay_statuses"] == []
    assert plan["applied"] is False and plan["authority_effect"] == "none"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_bundle_missing_or_file_does_not_inspect_relay(tmp_path, shell, kind):
    fixture = Fixture(tmp_path)
    invalid_root = tmp_path / "synthetic-invalid-bundles"
    invalid_root.mkdir()
    invalid_bundle = invalid_root / TARGET
    if kind == "file":
        invalid_bundle.write_text("not a bundle", encoding="utf-8")
    code, plan = run_shimmed(fixture, shell, BundlesRoot=invalid_root,
                             forbid_relay=invalid_bundle / "start-wd-tools-consumer.ps1")
    assert code == 2 and plan["verdict"] == "hold", plan
    assert "target_bundle_missing" in plan["reasons"], plan
    assert "target_relay_contract_unknown" in plan["reasons"], plan
    assert plan["target_relay_statuses"] == []
    assert plan["applied"] is False and plan["authority_effect"] == "none"


@pytest.mark.parametrize("shell", SHELLS)
def test_relative_bundle_root_uses_powershell_location(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = run_shimmed(fixture, shell, location=tmp_path, BundlesRoot="bundles")
    assert code == 0 and plan["verdict"] == "plan", plan
    assert plan["target_bundle"] == str(fixture.bundle) and plan["applied"] is False


@pytest.mark.parametrize("shell", SHELLS)
def test_bundle_root_inspection_denial_holds_on_every_host(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = run_shimmed(fixture, shell, inspect_denied=fixture.bundles)
    assert code == 2 and plan["verdict"] == "hold", plan
    assert plan["reasons"] == ["bundle_root_untrusted"], plan
    assert plan["target_bundle"] is None and plan["applied"] is False


@pytest.mark.parametrize("shell", SHELLS)
def test_manifest_inspection_denial_is_unreadable_before_hash(tmp_path, shell):
    fixture = Fixture(tmp_path)
    manifest = fixture.bundle / "deployment-manifest.json"
    code, plan = run_shimmed(fixture, shell, inspect_denied=manifest)
    assert code == 2 and plan["verdict"] == "hold", plan
    assert plan["reasons"] == ["target_manifest_unreadable"], plan
    assert plan["target_manifest_sha256"] is None and plan["applied"] is False


@pytest.mark.parametrize("shell", SHELLS)
def test_listed_component_inspection_denial_is_not_reported_missing(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = run_shimmed(fixture, shell, inspect_denied=fixture.bundle / "tools-bootstrap")
    assert code == 2 and plan["verdict"] == "hold", plan
    assert plan["reasons"] == ["bundle_file_uninspectable:tools-bootstrap/a.txt"], plan
    assert plan["applied"] is False


@pytest.mark.parametrize("shell", SHELLS)
def test_e1_a_journal_the_plan_cannot_list_holds_on_every_host(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = run_shimmed(fixture, shell, list_denied=fixture.journal)
    assert code == 2 and plan["verdict"] == "hold" and plan["applied"] is False, plan
    assert plan["reasons"] == ["lane_journal_unreadable:" + str(fixture.journal)], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_e1_an_uninspectable_record_and_an_unlistable_journal_both_hold_on_every_host(tmp_path, shell):
    fixture = Fixture(tmp_path)
    record = fixture.journal / "native-bridge-wake.json"
    code, plan = run_shimmed(fixture, shell, list_denied=fixture.journal, inspect_denied=record)
    assert code == 2 and plan["applied"] is False, plan
    assert plan["reasons"] == ["relay_state_unreadable:" + str(fixture.journal),
                               "lane_journal_unreadable:" + str(fixture.journal)], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_e1_an_uninspectable_record_alone_is_unknown_never_a_missing_record(tmp_path, shell):
    fixture = Fixture(tmp_path)
    record = fixture.journal / "native-bridge-wake.json"
    record.unlink()  # even when nothing is there, a lookup that fails for another reason proves nothing
    code, plan = run_shimmed(fixture, shell, inspect_denied=record)
    assert code == 2 and plan["reasons"] == ["relay_state_unreadable:" + str(fixture.journal)], plan


@pytest.mark.parametrize("shell", SHELLS)
def test_e1_controls_through_the_same_shim_readable_and_truly_missing_records_plan(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = run_shimmed(fixture, shell)          # shim present, nothing denied: the readable record plans
    assert code == 0 and plan["verdict"] == "plan", plan
    (fixture.journal / "native-bridge-wake.json").unlink()
    code, plan = run_shimmed(fixture, shell)          # a provably absent record (ItemNotFound) still plans
    assert code == 0 and plan["verdict"] == "plan", plan


@pytest.mark.parametrize("shell", SHELLS)
def test_e1_apply_is_refused_before_any_injected_failure_is_reached(tmp_path, shell):
    fixture = Fixture(tmp_path)
    code, plan = run_shimmed(fixture, shell, list_denied=fixture.journal, Apply=True)
    assert code == 3 and plan["reasons"] == ["apply_requires_signed_activation"] and plan["applied"] is False, plan
