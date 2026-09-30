# SPDX-License-Identifier: BUSL-1.1
"""The external trusted-caller provenance adapter for F0 (tools/bridge_v2_trusted_inputs.py).

Every fixture writes its inputs under tmp_path and runs the REAL F0 evaluator and the REAL
F15 switch adapter, so the assembled evidence is proven against both consumers. No runtime
root, lock or file outside tmp_path is touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

import tools.bridge_v2_activation as activation
import tools.bridge_v2_trusted_inputs as trusted
from tools.bridge_v2_switch_evidence import Refusal, switch_activation
from tools.bridge_v2_trusted_inputs import HighWaterStore, TrustRefusal, load_trusted_inputs

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)
HEAD, TREE = "1" * 40, "2" * 40
PARAMETERS = {"tick_seconds": 300, "hysteresis_percent": 5, "evidence_max_age_seconds": 600, "budget_mode": "steady"}


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_json(path: Path, value) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return raw


class World:
    """One consistent set of inputs: an F15-enabled signed policy, its revocation state,
    the deployed manifest with its anchor, and the operator packet that pins them."""

    def __init__(self, root: Path, *, enabled: bool = True, revocation_version: int = 3, floor: int = 2) -> None:
        config = json.loads((ROOT / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))
        policy = config["policy"]
        policy["features"]["F15"].update(enabled=enabled, stage=1 if enabled else None)
        policy.update(expires_utc=iso(NOW + timedelta(days=10)), revocation_max_age_seconds=3600,
                      parameters={"F15": dict(PARAMETERS)})
        self.digest = activation.canonical_sha256(policy)
        self.config = root / "configs" / "bridge_v2_activation.json"
        write_json(self.config, {"policy": policy, "signature": {
            "schema": activation.SIGNATURE_SCHEMA, "policy_sha256": self.digest, "head": HEAD, "tree": TREE,
            "signed_utc": iso(NOW - timedelta(hours=1)), "expires_utc": iso(NOW + timedelta(days=5))}})
        self.runtime = root / "runtime"
        self.revocation = self.runtime / activation.REVOCATION_RELATIVE
        write_json(self.revocation, {"schema": activation.REVOCATION_SCHEMA, "version": revocation_version,
                                     "policy_sha256": self.digest, "frozen": False, "revoked": [],
                                     "updated_utc": iso(NOW - timedelta(minutes=5))})
        self.manifest = root / "bundle" / "deployment-manifest.json"
        # The installer writes an upper-case digest; the adapter compares digests case-insensitively.
        self.anchor = sha(write_json(self.manifest, {"schema_version": 1, "source_commit": HEAD})).upper()
        self.packet = root / "operator" / "activation-packet.json"
        self.packet_fields = {"schema": trusted.PACKET_SCHEMA, "trusted_policy_sha256": self.digest,
                              "expected_head": HEAD, "expected_tree": TREE,
                              "deployment_manifest_sha256": self.anchor, "min_revocation_version": floor,
                              "features": ["F15"], "issued_utc": iso(NOW - timedelta(hours=1)),
                              "expires_utc": iso(NOW + timedelta(days=1))}
        self.write_packet()

    def write_packet(self, **changes) -> None:
        self.packet_sha = sha(write_json(self.packet, {**self.packet_fields, **changes}))

    def load(self, **overrides):
        arguments = {"packet_path": self.packet, "packet_sha256": self.packet_sha, "manifest_path": self.manifest,
                     "manifest_anchor_sha256": self.anchor, "config_path": self.config,
                     "runtime_root": self.runtime, "now": NOW, "environ": {}}
        arguments.update(overrides)
        return load_trusted_inputs("F15", **arguments)

    def refusal(self, **overrides) -> str:
        with pytest.raises(TrustRefusal) as caught:
            self.load(**overrides)
        return caught.value.code


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def test_success_twin_feeds_the_real_switch_adapter_and_ratchets_the_high_water(world):
    evidence = world.load()
    assert set(evidence) == {"decision", "pins", "document", "revocation"}
    assert evidence["decision"] == {"feature": "F15", "enabled": True, "reason": "enabled by the signed policy",
                                    "policy_sha256": world.digest, "revocation_version": 3}
    # The decision ran with the persisted floor 2; the mark then advanced to the Decision's version.
    assert evidence["pins"] == {"trusted_policy_sha256": world.digest, "expected_head": HEAD,
                                "expected_tree": TREE, "min_revocation_version": 2}
    assert switch_activation(evidence, NOW) == {"feature": "F15", "policy_sha256": world.digest,
                                                "revocation_version": 3, "head": HEAD, "tree": TREE,
                                                "parameters": PARAMETERS}
    assert HighWaterStore(world.runtime).read() == 3
    # A second run pins the ratcheted mark, never the lower packet floor.
    assert world.load()["pins"]["min_revocation_version"] == 3


def test_the_shipped_default_off_config_never_enables(world):
    world.config.write_bytes((ROOT / "configs" / "bridge_v2_activation.json").read_bytes())
    evidence = world.load()
    assert evidence["decision"]["enabled"] is False
    with pytest.raises(Refusal) as caught:
        switch_activation(evidence, NOW)
    assert caught.value.code == "f0_decision_disabled"


def test_the_pin_comes_from_the_packet_never_from_the_config_signature(world):
    # An attacker edits the config and re-signs it with its own digest; the packet still pins the old one.
    document = json.loads(world.config.read_text(encoding="utf-8"))
    document["policy"]["parameters"]["F15"]["tick_seconds"] = 1
    forged = activation.canonical_sha256(document["policy"])
    document["signature"]["policy_sha256"] = forged
    write_json(world.config, document)
    evidence = world.load()
    assert evidence["pins"]["trusted_policy_sha256"] == world.digest != forged
    assert evidence["decision"]["enabled"] is False
    assert "trusted signed digest" in evidence["decision"]["reason"]


@pytest.mark.parametrize("change, code", [
    (lambda w: setattr(w, "packet_sha", "0" * 64), "packet_digest_mismatch"),
    (lambda w: setattr(w, "packet_sha", "not-a-digest"), "packet_digest_invalid"),
    (lambda w: w.write_packet(extra=1), "packet_malformed"),
    (lambda w: w.write_packet(schema="wd.other.v1"), "packet_malformed"),
    (lambda w: w.write_packet(trusted_policy_sha256=w.digest.upper()), "packet_malformed"),
    (lambda w: w.write_packet(min_revocation_version=True), "packet_malformed"),
    (lambda w: w.write_packet(features=[]), "packet_malformed"),
    (lambda w: w.write_packet(features=["F15", "F15"]), "packet_malformed"),
    (lambda w: w.write_packet(features=["F20"]), "packet_feature_not_authorized"),
    (lambda w: w.write_packet(expires_utc=iso(NOW)), "packet_expired"),
    (lambda w: w.write_packet(issued_utc=iso(NOW + timedelta(hours=1)),
                              expires_utc=iso(NOW + timedelta(days=1))), "packet_from_the_future"),
    (lambda w: w.write_packet(expires_utc=iso(NOW + timedelta(days=40))), "packet_window_invalid"),
    (lambda w: w.write_packet(issued_utc="2026-09-30T16:00:00+00:00"), "packet_issued_utc_malformed"),
])
def test_the_packet_is_exact_scoped_and_time_bounded(world, change, code):
    change(world)
    assert world.refusal() == code


def test_a_packet_with_a_duplicate_key_is_malformed(world):
    raw = world.packet.read_bytes().replace(b'"features": [', b'"features": ["F15"], "features": [', 1)
    world.packet.write_bytes(raw)  # bytes: text mode would turn LF into CRLF on Windows
    world.packet_sha = sha(raw)
    assert world.refusal() == "packet_malformed"


def test_the_config_can_never_serve_as_its_own_packet(world):
    assert world.refusal(packet_path=world.config, packet_sha256=sha(world.config.read_bytes())) == "packet_is_the_config"


@pytest.mark.parametrize("change, code", [
    (lambda w: {"manifest_anchor_sha256": "0" * 64}, "deployment_anchor_mismatch"),
    (lambda w: {"manifest_anchor_sha256": None}, "deployment_anchor_mismatch"),
    (lambda w: (w.manifest.write_bytes(w.manifest.read_bytes() + b" "), {})[1], "deployment_manifest_digest_mismatch"),
    (lambda w: {"manifest_path": w.manifest.with_name("absent.json")}, "deployment_manifest_missing"),
])
def test_the_deployed_manifest_must_be_the_anchored_one(world, change, code):
    assert world.refusal(**change(world)) == code


def test_a_deployed_manifest_for_another_head_is_refused(tmp_path):
    world = World(tmp_path)
    world.anchor = sha(write_json(world.manifest, {"schema_version": 1, "source_commit": "3" * 40})).upper()
    world.write_packet(deployment_manifest_sha256=world.anchor)
    assert world.refusal() == "deployment_head_mismatch"


def test_a_revocation_rollback_below_the_persisted_mark_is_disabled_and_the_mark_stays(world):
    HighWaterStore(world.runtime).advance(None, 5, NOW)
    evidence = world.load()
    assert evidence["pins"]["min_revocation_version"] == 5
    assert evidence["decision"]["enabled"] is False and "rollback" in evidence["decision"]["reason"]
    assert HighWaterStore(world.runtime).read() == 5
    with pytest.raises(Refusal) as caught:
        switch_activation(evidence, NOW)
    assert caught.value.code == "f0_decision_disabled"


def test_a_corrupt_high_water_mark_refuses_before_any_decision(world):
    store = HighWaterStore(world.runtime)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"schema": "wd.bridge-v2-revocation-high-water.v1", "version": 0, '
                          '"updated_utc": "2026-09-30T16:00:00Z"}', encoding="utf-8")
    calls = []
    assert world.refusal(evaluate=lambda *a, **k: calls.append(a)) == "high_water_corrupt"
    assert calls == []


def test_the_high_water_store_is_a_monotonic_compare_and_swap(tmp_path):
    store = HighWaterStore(tmp_path)
    assert store.read() is None
    assert store.advance(None, 2, NOW) == 2
    with pytest.raises(TrustRefusal) as conflict:
        store.advance(None, 3, NOW)
    assert conflict.value.code == "high_water_cas_conflict"
    with pytest.raises(TrustRefusal) as rollback:
        store.advance(2, 1, NOW)
    assert rollback.value.code == "high_water_rollback"
    assert store.advance(2, 2, NOW) == 2 and store.read() == 2
    assert not list(store.path.parent.glob("*.tmp"))


def test_a_held_lock_refuses_instead_of_waiting(tmp_path):
    store = HighWaterStore(tmp_path)
    with trusted._exclusive_lock(store.lock_path):
        with pytest.raises(TrustRefusal) as caught:
            store.advance(None, 2, NOW)
    assert caught.value.code == "high_water_locked"
    assert store.read() is None


def test_inputs_changed_during_the_decision_are_refused(world):
    def evaluate_then_bump(feature, **kwargs):
        decision = activation.evaluate(feature, **kwargs)
        state = json.loads(world.revocation.read_text(encoding="utf-8"))
        write_json(world.revocation, {**state, "version": state["version"] + 1})
        return decision

    assert world.refusal(evaluate=evaluate_then_bump) == "inputs_changed_during_decision"


def test_a_decision_that_is_not_an_f0_decision_is_refused(world):
    assert world.refusal(evaluate=lambda feature, **kwargs: {"feature": feature, "enabled": True}) == "decision_invalid"
    assert world.refusal(evaluate=lambda feature, **kwargs: activation.Decision("F16", True, "x")) == "decision_invalid"


@pytest.mark.parametrize("now", [datetime(2026, 9, 30, 17, 0), "2026-09-30T17:00:00Z", None])
def test_an_unknown_time_refuses(world, now):
    assert world.refusal(now=now) == "time_unknown"


def test_missing_activation_inputs_refuse_with_their_own_code(world):
    world.revocation.unlink()
    assert world.refusal() == "revocation_missing"


def test_the_deployment_anchor_is_read_from_a_consistent_installer_pointer(tmp_path):
    pointer = tmp_path / "WD_REBOOT_STATE_CURRENT.json"
    record = {"source_commit": HEAD, "final_commit": HEAD, "manifest_sha256": "AB" * 32,
              "final_manifest_sha256": "ab" * 32, "active_bundle": str(tmp_path / "bundle")}
    pointer.write_bytes(b"\xef\xbb\xbf" + json.dumps(record).encode("utf-8"))
    assert trusted.read_deployment_anchor(pointer) == {"commit": HEAD, "manifest_sha256": "ab" * 32,
                                                       "manifest_path": tmp_path / "bundle" / "deployment-manifest.json"}
    pointer.write_text(json.dumps({**record, "final_commit": "4" * 40}), encoding="utf-8")
    with pytest.raises(TrustRefusal) as caught:
        trusted.read_deployment_anchor(pointer)
    assert caught.value.code == "pointer_inconsistent"


def test_no_runtime_path_imports_the_adapter():
    """Dormant: only this test module names it outside its own file."""
    needle = "bridge_v2_trusted_inputs"
    hits = []
    for base in ("ops", ".agent-bridge", "tools", "configs", "waggledance/core"):
        for path in (ROOT / base).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ps1", ".psm1", ".json", ".cmd"} \
                    and path.name != "bridge_v2_trusted_inputs.py":
                if needle in path.read_text(encoding="utf-8", errors="replace"):
                    hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def test_n8_a_floor_raised_during_the_decision_refuses_a_decision_below_it(tmp_path):
    world = World(tmp_path, floor=3)

    def evaluate_while_another_caller_advances(feature, **kwargs):
        decision = activation.evaluate(feature, **kwargs)
        HighWaterStore(world.runtime).advance(3, 5, NOW)
        return decision

    assert world.refusal(evaluate=evaluate_while_another_caller_advances) == "high_water_advanced_during_decision"
    assert HighWaterStore(world.runtime).read() == 5


def test_n8_success_twin_a_concurrent_raise_to_the_decision_version_is_accepted(tmp_path):
    world = World(tmp_path, floor=2)

    def evaluate_while_another_caller_advances(feature, **kwargs):
        decision = activation.evaluate(feature, **kwargs)
        HighWaterStore(world.runtime).advance(2, 3, NOW)
        return decision

    evidence = world.load(evaluate=evaluate_while_another_caller_advances)
    assert evidence["decision"]["enabled"] is True and evidence["decision"]["revocation_version"] == 3
    assert HighWaterStore(world.runtime).read() == 3


def test_n8_a_mark_removed_during_the_decision_is_a_rollback(tmp_path):
    world = World(tmp_path)

    def evaluate_then_remove_the_mark(feature, **kwargs):
        decision = activation.evaluate(feature, **kwargs)
        HighWaterStore(world.runtime).path.unlink()
        return decision

    assert world.refusal(evaluate=evaluate_then_remove_the_mark) == "high_water_rollback"


def test_n10_a_zero_progress_write_refuses_instead_of_spinning(tmp_path, monkeypatch):
    store = HighWaterStore(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(trusted.os, "write", lambda descriptor, data: 0)
        with pytest.raises(TrustRefusal) as caught:
            store.advance(None, 2, NOW)
    assert caught.value.code == "high_water_unwritable"
    assert store.read() is None and not list(store.path.parent.glob("*.tmp"))


def test_the_residual_limits_are_stated_in_the_module():
    text = (ROOT / "tools" / "bridge_v2_trusted_inputs.py").read_text(encoding="utf-8")
    assert "A-B-A" in text and "power failure" in text and "resets the floor" in text