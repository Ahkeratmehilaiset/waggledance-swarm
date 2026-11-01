"""Synthetic one-shot status publication, no live bridge or runtime."""
from datetime import datetime, timezone
import hashlib
import importlib
import json

import pytest

from sandbox.mind.step2.test_status_public_filter import ROSTER, enc, event


def module():
    return importlib.import_module("tools.wd_mind_status_publish_once")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def spec(tmp_path):
    private_parent = tmp_path / "mind-status-private"
    private_parent.mkdir()
    audit = private_parent / ".codex-audit"
    audit.mkdir()
    snapshot_root = audit / "synthetic-snapshots"
    snapshot_root.mkdir()
    secret = "synthetic-private-cursor-payload"
    source = (enc(event(message=secret, payload={"public_status": True, "path": "C:\\private\\synthetic"}))
              + b"\n" + enc(event()) + b"\n")
    policy = enc({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": ["public-task"]})
    roster = enc({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
                  "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}})
    public = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                  "public_types": ["message"]})
    approval = enc({"schema": "wd.mind-public-approval.v1", "descriptor_sha256": sha(public),
                    "approved_tasks": ["public-task"], "approved_types": ["message"]})
    return {"private_audit_root": audit, "private_storage_assumed": True, "snapshot_root": snapshot_root, "snapshot_bytes": source,
            "expected_snapshot_sha256": sha(source), "policy_bytes": policy, "policy_sha256": sha(policy),
            "roster_bytes": roster, "roster_sha256": sha(roster), "public_policy_bytes": public,
             "public_policy_sha256": sha(public), "approval_bytes": approval,
             "expected_approval_sha256": sha(approval), "as_of": datetime(2026, 11, 1, tzinfo=timezone.utc),
            "expected_previous_sha256": None}


def test_default_off_does_not_inspect_request():
    class Poison:
        def __getattribute__(self, name):
            raise AssertionError("disabled must not inspect")
    assert module().publish_once(request=Poison()) == {"status": "OFF"}


@pytest.mark.parametrize("enabled", [None, 0, 1, "true"])
def test_only_literal_true_enables(enabled):
    assert module().publish_once(enabled=enabled, request=object()) == {"status": "REFUSED"}


def test_success_reports_retained_private_artifacts_separately(spec):
    reply = module().publish_once(enabled=True, request=spec)
    assert set(reply) == {"projection", "private_artifacts"}
    assert reply["private_artifacts"] == "RETAINED"
    assert reply["projection"]["schema"] == "wd.mind-status-projection.v3"
    assert set(reply["projection"]) == {"schema", "authority", "coverage", "records", "trust"}
    assert "private_artifacts" not in reply["projection"]
    assert len(list(spec["snapshot_root"].iterdir())) == 2
    visible = json.dumps(reply)
    assert str(spec["snapshot_root"]) not in visible
    assert spec["expected_snapshot_sha256"] not in visible
    assert "synthetic-private" not in visible


def test_pinned_synthetic_snapshot_publishes_only_metadata(spec, capsys):
    mod = module()
    reply = mod.publish_once(enabled=True, request=spec)
    assert reply["private_artifacts"] == "RETAINED"
    assert reply["projection"]["schema"] == "wd.mind-status-projection.v3"
    assert reply["projection"]["coverage"] == "UNKNOWN"
    assert reply["projection"]["trust"]["source_freshness"] == "UNKNOWN"
    raw = (spec["private_audit_root"] / "public-status.json").read_bytes()
    private = json.loads(raw)
    assert "anchor_sha256" in private and "roster_sha256" in private
    public = json.dumps(reply)
    for field in ("anchor_sha256", "roster_sha256", "policy_sha256", "public_policy_sha256", "body_sha256"):
        assert private[field] not in public and field not in public
    assert spec["expected_snapshot_sha256"] not in public
    assert "publication_sha256" not in public and "last_delta_only" not in public
    assert json.loads(raw)["body"]["records"][0]["task_id"] == "public-task"
    for forbidden in (b"synthetic-private", b"C:\\\\private", b'"cursor"', b'"source_path"', b'"payload"'):
        assert forbidden not in raw and forbidden.decode() not in repr(reply)
    assert capsys.readouterr().out == "" and capsys.readouterr().err == ""


@pytest.mark.parametrize("kind", ["wrong_snapshot_hash", "wrong_policy_hash", "bad_previous", "incomplete_row"])
def test_bad_pins_and_stale_cas_never_publish(spec, kind):
    mod = module()
    altered = dict(spec)
    if kind == "wrong_snapshot_hash":
        altered["expected_snapshot_sha256"] = "0" * 64
    elif kind == "wrong_policy_hash":
        altered["policy_sha256"] = "0" * 64
    elif kind == "bad_previous":
        altered["expected_previous_sha256"] = "0" * 64
    else:
        altered["snapshot_bytes"] = altered["snapshot_bytes"][:-1]
        altered["expected_snapshot_sha256"] = sha(altered["snapshot_bytes"])
    reply = mod.publish_once(enabled=True, request=altered)
    assert reply == {"status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert not (spec["private_audit_root"] / "public-status.json").exists()


def test_stale_cas_preserves_the_existing_publication(spec):
    mod = module()
    assert mod.publish_once(enabled=True, request=spec)["projection"]["schema"] == "wd.mind-status-projection.v3"
    target = spec["private_audit_root"] / "public-status.json"
    before = target.read_bytes()
    stale = dict(spec, expected_previous_sha256="0" * 64)
    assert mod.publish_once(enabled=True, request=stale) == {"status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert target.read_bytes() == before


def test_snapshot_directory_outside_audit_is_refused(spec, tmp_path):
    mod = module()
    outside = tmp_path / "outside"
    outside.mkdir()
    assert mod.publish_once(enabled=True, request=dict(spec, snapshot_root=outside)) == {"status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert list(outside.iterdir()) == []


def test_private_storage_assumption_must_be_explicit_and_dedicated(spec, tmp_path):
    mod = module()
    assert mod.publish_once(enabled=True, request=dict(spec, private_storage_assumed=False)) == {"status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    wrong_parent = tmp_path / "public-status-root"
    wrong_parent.mkdir()
    wrong_root = wrong_parent / ".codex-audit"
    wrong_root.mkdir()
    assert mod.publish_once(enabled=True, request=dict(spec, private_audit_root=wrong_root)) == {"status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert not (wrong_root / "public-status.json").exists()


@pytest.mark.parametrize("when", ["before_read", "after_prepare"])
def test_changed_snapshot_before_publication_is_refused(spec, monkeypatch, when):
    mod = module()
    if when == "before_read":
        original = mod.snapshot.read_anchored_delta
        def altered(path, *args, **kwargs):
            path.write_bytes(b'{}\n')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(mod.snapshot, "read_anchored_delta", altered)
    else:
        original = mod.session.prepare_status_attempt
        def altered(*args, **kwargs):
            result = original(*args, **kwargs)
            source = next(spec["snapshot_root"].glob("*.jsonl"))
            source.write_bytes(b'{}\n')
            return result
        monkeypatch.setattr(mod.session, "prepare_status_attempt", altered)
    assert mod.publish_once(enabled=True, request=spec) == {"status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert not (spec["private_audit_root"] / "public-status.json").exists()


@pytest.mark.parametrize("landed", [False, True])
def test_ambiguous_publish_is_unknown_and_never_acks_cursor(spec, monkeypatch, landed):
    mod = module()
    original = mod.session.publish_status
    calls = []
    def ambiguous(*args, **kwargs):
        calls.append(1)
        if landed:
            original(*args, **kwargs)
        raise OSError("synthetic-private-cursor-payload")
    monkeypatch.setattr(mod.session, "publish_status", ambiguous)
    reply = mod.publish_once(enabled=True, request=spec)
    assert calls == [1] and reply == {"status": "UNKNOWN", "private_artifacts": "MAY_REMAIN"}
    assert (spec["private_audit_root"] / "public-status.json").exists() is landed
    assert "cursor" not in repr(reply)


def test_change_after_final_readback_documents_immutability_assumption(spec, monkeypatch):
    """The accepted result is from the earlier read: this does not close TOCTOU or prove immutability."""
    mod = module()
    original = mod.session.publish_status
    def change_after_readback(*args, **kwargs):
        source = next(spec["snapshot_root"].glob("*.jsonl"))
        source.write_bytes(b'{}\n')
        return original(*args, **kwargs)
    monkeypatch.setattr(mod.session, "publish_status", change_after_readback)
    result = mod.publish_once(enabled=True, request=spec)
    assert result["private_artifacts"] == "RETAINED"
    assert result["projection"]["schema"] == "wd.mind-status-projection.v3"
    assert result["projection"]["coverage"] == "UNKNOWN"
    assert result["projection"]["records"][0]["task_id"] == "public-task"
    assert next(spec["snapshot_root"].glob("*.jsonl")).read_bytes() == b'{}\n'


def test_stale_prior_refuses_before_raw_snapshot_creation(spec):
    mod = module()
    stale = dict(spec, expected_previous_sha256="0" * 64)
    assert mod.publish_once(enabled=True, request=stale) == {
        "status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert list(spec["snapshot_root"].iterdir()) == []
    assert (spec["private_audit_root"] / ".public-status.lock").is_file()


@pytest.mark.parametrize("bad", ["missing", "wrong_pin", "descriptor_replaced"])
def test_unapproved_descriptor_is_rejected_before_any_raw_write(spec, bad):
    changed = dict(spec)
    if bad == "missing":
        changed["approval_bytes"] = None
    elif bad == "wrong_pin":
        changed["expected_approval_sha256"] = "0" * 64
    else:
        descriptor = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                          "public_types": ["message", "milestone"]})
        changed["public_policy_bytes"], changed["public_policy_sha256"] = descriptor, sha(descriptor)
    assert module().publish_once(enabled=True, request=changed) == {
        "status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert list(spec["snapshot_root"].iterdir()) == []
    assert not (spec["private_audit_root"] / "public-status.json").exists()
    assert not (spec["private_audit_root"] / ".public-status.lock").exists()


@pytest.mark.parametrize("bad", ["approval_hash", "descriptor_binding"])
def test_bad_approval_pin_stops_before_other_input_parsing_and_writes(spec, monkeypatch, bad):
    mod = module()
    def must_not_parse(*args, **kwargs):
        pytest.fail("other input was parsed before approval pin validation")
    monkeypatch.setattr(mod.adapter, "strict_pins", must_not_parse)
    monkeypatch.setattr(mod.adapter, "public_status_policy", must_not_parse)
    changed = dict(spec)
    if bad == "approval_hash":
        changed["expected_approval_sha256"] = "0" * 64
    else:
        descriptor = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                          "public_types": ["message", "milestone"]})
        changed["public_policy_bytes"], changed["public_policy_sha256"] = descriptor, sha(descriptor)
    assert mod.publish_once(enabled=True, request=changed) == {
        "status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert list(spec["snapshot_root"].iterdir()) == []
    assert not (spec["private_audit_root"] / "public-status.json").exists()
    assert not (spec["private_audit_root"] / ".public-status.lock").exists()


def test_failed_prepare_reports_possible_retention_and_keeps_exact_raw_snapshot(spec, monkeypatch):
    mod = module()
    def fail_prepare(*args, **kwargs):
        raise RuntimeError("synthetic-private-cursor-payload")
    monkeypatch.setattr(mod.session, "prepare_status_attempt", fail_prepare)
    reply = mod.publish_once(enabled=True, request=spec)
    assert reply == {"status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert "synthetic-private-cursor-payload" not in repr(reply)
    snapshots = list(spec["snapshot_root"].glob("*.jsonl"))
    assert len(snapshots) == 1 and snapshots[0].read_bytes() == spec["snapshot_bytes"]
    assert not (spec["private_audit_root"] / "public-status.json").exists()


def test_race_after_preflight_cannot_overwrite_and_reports_retention(spec, monkeypatch):
    mod = module()
    original = mod.session.prepare_status_attempt
    inside = False
    competing_bytes = []
    def competing(*args, **kwargs):
        nonlocal inside
        if not inside:
            inside = True
            try:
                other = mod.publish_once(enabled=True, request=spec)
                assert other["projection"]["schema"] == "wd.mind-status-projection.v3"
                competing_bytes.append((spec["private_audit_root"] / "public-status.json").read_bytes())
            finally:
                inside = False
        return original(*args, **kwargs)
    monkeypatch.setattr(mod.session, "prepare_status_attempt", competing)
    reply = mod.publish_once(enabled=True, request=spec)
    assert reply == {"status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    assert (spec["private_audit_root"] / "public-status.json").read_bytes() == competing_bytes[0]
    assert len(list(spec["snapshot_root"].glob("*.jsonl"))) == 2
