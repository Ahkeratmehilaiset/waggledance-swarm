import hashlib
import json

import pytest

from tools import wd_q4_guard_state_provision_cli as anchored


def digest(data):
    return hashlib.sha256(data).hexdigest()


def bundle(tmp_path):
    fleet = b'{"runtime_root":"C:\\\\synthetic"}'
    manifest = json.dumps({"schema_version": 1, "files": {"wd-fleet.json": digest(fleet)}}).encode()
    (tmp_path / "wd-fleet.json").write_bytes(fleet)
    (tmp_path / "deployment-manifest.json").write_bytes(manifest)
    return fleet, manifest


def test_off_does_not_read_or_write(monkeypatch):
    monkeypatch.setattr(anchored.caller, "_read", lambda *_: pytest.fail("read while off"))
    assert anchored.provision_anchored() == {"status": "OFF"}


def test_only_manifest_anchored_fleet_reaches_provisioner(tmp_path, monkeypatch):
    fleet, manifest = bundle(tmp_path)
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
    fleet, manifest = bundle(tmp_path)
    path = tmp_path / ("deployment-manifest.json" if target == "manifest" else "wd-fleet.json")
    path.write_bytes((manifest if target == "manifest" else fleet) + b" ")
    monkeypatch.setattr(anchored.provisioner, "provision_guard_state", lambda **_: pytest.fail("unsafe provisioning"))
    with pytest.raises(anchored.AnchoredProvisionUnavailable):
        anchored.provision_anchored(bundle_root=str(tmp_path), manifest_sha256=digest(manifest), enabled=True)


def test_bad_schema_refuses_before_provisioning(tmp_path, monkeypatch):
    _, manifest = bundle(tmp_path)
    bad = manifest.replace(b'"schema_version": 1', b'"schema_version": 2')
    (tmp_path / "deployment-manifest.json").write_bytes(bad)
    monkeypatch.setattr(anchored.provisioner, "provision_guard_state", lambda **_: pytest.fail("unsafe provisioning"))
    with pytest.raises(anchored.AnchoredProvisionUnavailable):
        anchored.provision_anchored(bundle_root=str(tmp_path), manifest_sha256=digest(bad), enabled=True)


def test_cli_is_off_without_arguments(capsys):
    assert anchored.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "OFF"}


def test_cli_rejects_partial_arguments_without_exposing_them(capsys):
    assert anchored.main(["--enable", "private-path"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "REFUSED"}
    assert captured.err == ""
