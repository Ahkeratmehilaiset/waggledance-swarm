"""Exercise snapshot and restore only in the helper's synthetic sandbox."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/windows/mind-operator/Restore-WdMindOperatorSnapshot.ps1"
SHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
AUDIT = Path("C:/Python/project2/.codex-audit")
POINTER = "WD_REBOOT_STATE_CURRENT.json"
LEAF = "start-wd-all.ps1"
OLD = "1" * 40
NEW = "2" * 40
NEW_MANIFEST = "a" * 64


def digest(data):
    return hashlib.sha256(data).hexdigest().upper()


def pointer(root, commit, manifest):
    return json.dumps(dict(
        source_commit=commit, final_commit=commit, manifest_sha256=manifest,
        final_manifest_sha256=manifest, active_bundle=str(root / "bundles" / commit),
    )).encode()


def invoke(root, snapshot, manifest, phase, *args):
    return subprocess.run(
        [SHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", SCRIPT,
         "-Mode", "Synthetic", "-SyntheticRoot", root, "-SyntheticCommit", OLD,
         "-SyntheticManifestSha256", manifest, "-Phase", phase, "-SnapshotRoot", snapshot, *args],
        cwd=root, capture_output=True, text=True, timeout=30,
    )


@pytest.fixture
def saved():
    AUDIT.mkdir(parents=True, exist_ok=True)
    assert AUDIT.resolve(strict=True) == AUDIT
    root = AUDIT / ("pr2179-operator-synthetic-" + uuid.uuid4().hex)
    root.mkdir()
    try:
        bundle = root / "bundles" / OLD
        bundle.mkdir(parents=True)
        (bundle / "fixture.txt").write_bytes(b"fixture")
        manifest_bytes = json.dumps(dict(schema_version=1, source_commit=OLD,
                                         files={"fixture.txt": digest(b"fixture")})).encode()
        (bundle / "deployment-manifest.json").write_bytes(manifest_bytes)
        manifest = digest(manifest_bytes)
        machine = root / "machine"
        machine.mkdir()
        (machine / POINTER).write_bytes(pointer(root, OLD, manifest))
        (machine / LEAF).write_bytes(b"original")
        (root / "backups").mkdir()
        snapshot = root / "backups" / ("mind-pr2179-" + uuid.uuid4().hex)
        result = invoke(root, snapshot, manifest, "Snapshot")
        assert result.returncode == 0, result.stderr
        raw = (snapshot / "snapshot.json").read_bytes()
        anchor = digest(raw)
        assert result.stdout.splitlines() == ["SNAPSHOT_SHA256=" + anchor]
        inventory = json.loads(raw)["files"]
        assert len(inventory) == len({entry["name"] for entry in inventory}) == 25
        assert {entry["name"] for entry in inventory if entry["present"]} == {POINTER, LEAF}
        for entry in inventory:
            if entry["present"]:
                data = (machine / entry["name"]).read_bytes()
                assert (snapshot / entry["name"]).read_bytes() == data
                assert (entry["length"], entry["sha256"]) == (len(data), digest(data))
            else:
                assert entry["length"] is None and entry["sha256"] is None
                assert not (snapshot / entry["name"]).exists()
        yield root, snapshot, manifest, anchor, [entry["name"] for entry in inventory]
    finally:
        shutil.rmtree(root)


@pytest.mark.parametrize("old_commit,old_manifest", [(True, True), (True, False), (False, True)])
def test_ordinary_restore_refuses_each_old_anchor_before_writes(saved, old_commit, old_manifest):
    root, snapshot, manifest, anchor, _ = saved
    machine = root / "machine"
    current_commit = OLD if old_commit else NEW
    current_manifest = manifest.lower() if old_manifest else NEW_MANIFEST
    (machine / POINTER).write_bytes(pointer(root, current_commit, current_manifest))
    (machine / LEAF).write_bytes(b"current")
    def state():
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in machine.iterdir()}

    before = state()
    result = invoke(root, snapshot, manifest, "Restore", "-ExpectedSnapshotSha256", anchor,
                    "-ExpectedCurrentCommit", current_commit, "-ExpectedCurrentManifestSha256", current_manifest)
    assert result.returncode != 0 and "HOLD ordinary restore requires a distinct current pair" in result.stderr
    assert result.stdout == ""
    assert state() == before


@pytest.mark.parametrize("recovery", [False, True])
def test_verified_restore_reports_inventory_with_pointer_last(saved, recovery):
    root, snapshot, manifest, anchor, names = saved
    machine = root / "machine"
    (machine / LEAF).write_bytes(b"current")
    if recovery:
        args = ["-RecoverOldPointer", "-ExpectedOldPointerSha256", digest((snapshot / POINTER).read_bytes())]
    else:
        (machine / POINTER).write_bytes(pointer(root, NEW, NEW_MANIFEST))
        args = ["-ExpectedCurrentCommit", NEW, "-ExpectedCurrentManifestSha256", NEW_MANIFEST]
    result = invoke(root, snapshot, manifest, "Restore", "-ExpectedSnapshotSha256", anchor, *args)
    assert result.returncode == 0, result.stderr
    ordered = [name for name in names if name != POINTER] + [POINTER]
    assert result.stdout.splitlines() == ["PROCESSED " + name for name in ordered] + ["RESTORE_CANDIDATE_COMPLETE"]
    assert {p.name: p.read_bytes() for p in machine.iterdir()} == {
        POINTER: (snapshot / POINTER).read_bytes(), LEAF: b"original",
    }
