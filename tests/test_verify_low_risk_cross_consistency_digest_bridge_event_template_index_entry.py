"""Offline verifier tests for the low-risk cross-consistency index entry."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json

import pytest

from tools.build_low_risk_cross_consistency_digest_bridge_event_template import (
    build_low_risk_cross_consistency_digest_bridge_event_template,
)
from tools.build_low_risk_cross_consistency_digest_bridge_event_template_index_entry import (
    build_low_risk_cross_consistency_digest_bridge_event_template_index_entry,
)
import tools.verify_low_risk_cross_consistency_digest_bridge_event_template_index_entry as mod


_FIXED = datetime(2026, 6, 21, 10, 30, 0, tzinfo=timezone.utc)


def _good_digest() -> dict:
    return {
        "report_version": "wd.low_risk_cross_consistency_digest.v1",
        "real_loop_present": True,
        "repeat_window_trend_present": True,
        "reviewer_summary_present": True,
        "all_views_present": True,
        "real_loop_clean": True,
        "trend_clean": True,
        "reviewer_clean": True,
        "reviewer_matches_trend": True,
        "cross_consistent": True,
        "path_free_verified": True,
        "claim_safe": False,
    }


def _template_report() -> dict:
    return build_low_risk_cross_consistency_digest_bridge_event_template(
        digest=_good_digest(),
        agent_id="fable-5",
        task_id="demo-task",
        now_utc=_FIXED,
    )


def _template_bytes(report: dict) -> bytes:
    return json.dumps(report, sort_keys=True).encode()


def _index_entry(report: dict) -> dict:
    return build_low_risk_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=report,
        bridge_event_template_bytes=_template_bytes(report),
        now_utc=_FIXED,
    )


def _verify(entry: dict, report: dict) -> dict:
    return mod.verify_low_risk_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=entry,
        bridge_event_template_report=report,
        index_entry_bytes=json.dumps(entry, sort_keys=True).encode(),
        bridge_event_template_bytes=_template_bytes(report),
    )


def test_verifier_accepts_matching_index_entry() -> None:
    report = _template_report()
    entry = _index_entry(report)

    verification = _verify(entry, report)

    assert verification["ok"] is True
    assert verification["verification_version"] == mod.VERIFICATION_VERSION
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert verification["runtime_authority_granted"] is False
    assert verification["scheduler_enqueue_allowed"] is False
    assert verification["claim_safe"] is False
    assert verification["network_access_performed"] is False


def test_cli_accepts_matching_artifacts(tmp_path, capsys) -> None:
    report = _template_report()
    entry = _index_entry(report)
    template_path = tmp_path / "template.json"
    entry_path = tmp_path / "entry.json"
    template_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    entry_path.write_text(json.dumps(entry, sort_keys=True), encoding="utf-8")

    rc = mod.main(
        [
            "--index-entry-json",
            str(entry_path),
            "--bridge-event-template-json",
            str(template_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["ok"] is True


def test_verifier_rejects_digest_tamper() -> None:
    report = _template_report()
    entry = _index_entry(report)
    tampered = copy.deepcopy(entry)
    tampered["template_index_entry"]["payload_digest"] = "sha256:" + ("0" * 64)

    verification = _verify(tampered, report)

    assert verification["ok"] is False
    assert verification["digest_checks"]["payload_digest"] == "mismatch"
    assert "payload_digest_mismatch" in verification["blockers"]
    assert "rebuilt_index_entry_mismatch" in verification["blockers"]


def test_verifier_rejects_authority_tamper() -> None:
    report = _template_report()
    entry = _index_entry(report)
    tampered = copy.deepcopy(entry)
    tampered["runtime_authority_granted"] = True

    verification = _verify(tampered, report)

    assert verification["ok"] is False
    assert any("runtime_authority_granted" in item for item in verification["blockers"])
    assert verification["runtime_authority_granted"] is False


def test_verifier_rejects_source_template_contract_tamper() -> None:
    report = _template_report()
    entry = _index_entry(report)
    tampered_report = copy.deepcopy(report)
    tampered_report["runtime_authority_granted"] = True

    verification = mod.verify_low_risk_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=entry,
        bridge_event_template_report=tampered_report,
        index_entry_bytes=json.dumps(entry, sort_keys=True).encode(),
        bridge_event_template_bytes=_template_bytes(tampered_report),
    )

    assert verification["ok"] is False
    assert verification["source_contract_check"] == "failed"
    assert "source_contract_rebuild_failed" in verification["blockers"]


def test_verifier_rejects_template_bytes_mismatch() -> None:
    report = _template_report()
    entry = _index_entry(report)
    changed_report = _template_report()
    changed_report["bridge_event_template"]["task_id"] = "different-task"

    verification = mod.verify_low_risk_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=entry,
        bridge_event_template_report=changed_report,
        index_entry_bytes=json.dumps(entry, sort_keys=True).encode(),
        bridge_event_template_bytes=_template_bytes(changed_report),
    )

    assert verification["ok"] is False
    assert verification["rebuilt_index_entry_check"] == "mismatch"
    assert "template_report_sha256_mismatch" in verification["blockers"]


def test_verifier_rejects_empty_index_entry_bytes() -> None:
    report = _template_report()
    entry = _index_entry(report)

    verification = mod.verify_low_risk_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=entry,
        bridge_event_template_report=report,
        index_entry_bytes=b"",
        bridge_event_template_bytes=_template_bytes(report),
    )

    assert verification["ok"] is False
    assert verification["size_checks"]["index_entry_bytes_present"] == "failed"
    assert "index_entry_bytes_missing" in verification["blockers"]


@pytest.mark.parametrize("bad_value", [None, [], "not an entry"])
def test_verifier_rejects_non_mapping_index_entry(bad_value) -> None:
    report = _template_report()

    with pytest.raises(mod.LowRiskIndexEntryVerificationError):
        mod.verify_low_risk_cross_consistency_digest_bridge_event_template_index_entry(
            index_entry=bad_value,
            bridge_event_template_report=report,
            index_entry_bytes=b"{}",
            bridge_event_template_bytes=_template_bytes(report),
        )


def test_cli_rejects_duplicate_json_key(tmp_path, capsys) -> None:
    report = _template_report()
    template_path = tmp_path / "template.json"
    entry_path = tmp_path / "entry.json"
    template_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")
    entry_path.write_text('{"ok": true, "ok": false}', encoding="utf-8")

    rc = mod.main(
        [
            "--index-entry-json",
            str(entry_path),
            "--bridge-event-template-json",
            str(template_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["ok"] is False
    assert payload["claim_safe"] is False


def test_failure_output_does_not_leak_raw_event_content() -> None:
    report = _template_report()
    entry = _index_entry(report)
    tampered = copy.deepcopy(entry)
    tampered["template_index_entry"]["event_digest"] = "sha256:" + ("1" * 64)

    verification = _verify(tampered, report)
    blob = json.dumps(verification)

    assert verification["ok"] is False
    assert "template ready" not in blob
    assert "fable-5" not in blob
    assert "ts_utc" not in blob
    assert "C:" not in blob
    assert verification["local_paths_recorded"] is False
    assert verification["artifact_payloads_included"] is False
