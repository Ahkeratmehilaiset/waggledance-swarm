import hashlib
import json

import pytest

from tools import wd_q4_guard_state_provision_cli as anchored


def digest(data):
    return hashlib.sha256(data).hexdigest()


def bundle(tmp_path, monkeypatch):
    fleet = b'{"runtime_root":"C:\\\\synthetic"}'
    manifest = json.dumps({"schema_version": 1, "files": {"wd-fleet.json": digest(fleet)}}).encode()
    (tmp_path / "wd-fleet.json").write_bytes(fleet)
    (tmp_path / "deployment-manifest.json").write_bytes(manifest)
    pointer = tmp_path / "pointer.json"
    pointer.write_text(json.dumps({"active_bundle": str(tmp_path), "manifest_sha256": digest(manifest),
                                   "final_manifest_sha256": digest(manifest)}), encoding="utf-8")
    monkeypatch.setattr(anchored, "_STATE_POINTER", pointer, raising=False)
    return fleet, manifest


def test_off_does_not_read_or_write(monkeypatch):
    monkeypatch.setattr(anchored.caller, "_read", lambda *_: pytest.fail("read while off"))
    assert anchored.provision_anchored() == {"status": "OFF"}


def test_only_manifest_anchored_fleet_reaches_provisioner(tmp_path, monkeypatch):
    fleet, manifest = bundle(tmp_path, monkeypatch)
    received = []

    def provision(**kwargs):
        received.append(kwargs)
        return {"status": "READY"}

    monkeypatch.setattr(anchored.provisioner, "provision_guard_state", provision)
    assert anchored.provision_anchored(bundle_root=str(tmp_path), manifest_sha256=digest(manifest), enabled=True) == {
        "status": "READY"
    }
    assert received == [{"fleet_bytes": fleet, "expected_fleet_sha256": digest(fleet), "enabled": True}]


@pytest.mark.parametrize("target", ["manifest", "fleet"])
def test_hash_mismatch_refuses_before_provisioning(tmp_path, monkeypatch, target):
    fleet, manifest = bundle(tmp_path, monkeypatch)
    path = tmp_path / ("deployment-manifest.json" if target == "manifest" else "wd-fleet.json")
    path.write_bytes((manifest if target == "manifest" else fleet) + b" ")
    monkeypatch.setattr(anchored.provisioner, "provision_guard_state", lambda **_: pytest.fail("unsafe provisioning"))
    with pytest.raises(anchored.AnchoredProvisionUnavailable):
        anchored.provision_anchored(bundle_root=str(tmp_path), manifest_sha256=digest(manifest), enabled=True)


def test_bad_schema_refuses_before_provisioning(tmp_path, monkeypatch):
    _, manifest = bundle(tmp_path, monkeypatch)
    bad = manifest.replace(b'"schema_version": 1', b'"schema_version": 2')
    (tmp_path / "deployment-manifest.json").write_bytes(bad)
    (tmp_path / "pointer.json").write_text(json.dumps({"active_bundle": str(tmp_path), "manifest_sha256": digest(bad),
                                                        "final_manifest_sha256": digest(bad)}), encoding="utf-8")
    monkeypatch.setattr(anchored.provisioner, "provision_guard_state", lambda **_: pytest.fail("unsafe provisioning"))
    with pytest.raises(anchored.AnchoredProvisionUnavailable):
        anchored.provision_anchored(bundle_root=str(tmp_path), manifest_sha256=digest(bad), enabled=True)


@pytest.mark.parametrize("change", ["foreign_bundle", "wrong_hash", "wrong_final_hash", "missing_pointer"])
def test_pointer_mismatch_refuses_self_consistent_bundle(tmp_path, monkeypatch, change):
    _, manifest = bundle(tmp_path, monkeypatch)
    pointer_path = tmp_path / "pointer.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if change == "foreign_bundle":
        pointer["active_bundle"] = str(tmp_path / "other")
    elif change == "wrong_hash":
        pointer["manifest_sha256"] = "0" * 64
    elif change == "wrong_final_hash":
        pointer["final_manifest_sha256"] = "0" * 64
    if change == "missing_pointer":
        pointer_path.unlink()
    else:
        pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    monkeypatch.setattr(anchored.provisioner, "provision_guard_state", lambda **_: pytest.fail("unsafe provisioning"))
    with pytest.raises(anchored.AnchoredProvisionUnavailable):
        anchored.provision_anchored(bundle_root=str(tmp_path), manifest_sha256=digest(manifest), enabled=True)


def test_cli_is_off_without_arguments(capsys):
    assert anchored.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "OFF"}


def test_cli_rejects_partial_arguments_without_exposing_them(capsys):
    assert anchored.main(["--enable", "private-path"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "REFUSED"}
    assert captured.err == ""


def test_anchored_real_provisioner_creates_only_synthetic_lane_dirs(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    (runtime / "shared" / ".codex-audit").mkdir(parents=True)
    fleet = json.dumps({"runtime_root": str(runtime)}).encode()
    manifest = json.dumps({"schema_version": 1, "files": {"wd-fleet.json": digest(fleet)}}).encode()
    (tmp_path / "wd-fleet.json").write_bytes(fleet)
    (tmp_path / "deployment-manifest.json").write_bytes(manifest)
    pointer = tmp_path / "pointer.json"
    pointer.write_text(json.dumps({"active_bundle": str(tmp_path), "manifest_sha256": digest(manifest).upper(),
                                   "final_manifest_sha256": digest(manifest)}), encoding="utf-8")
    monkeypatch.setattr(anchored, "_STATE_POINTER", pointer)
    assert anchored.provision_anchored(bundle_root=str(tmp_path), manifest_sha256=digest(manifest).upper(),
                                       enabled=True) == {"status": "READY"}
    audit = runtime / "shared" / ".codex-audit"
    for lane in anchored.provisioner._LANES:
        assert (audit / lane / "q4-caller-state").is_dir()
