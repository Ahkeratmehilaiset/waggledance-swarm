"""Offline tests for the hex-upgrade cross-consistency template index entry."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json

import pytest

import tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry as mod
from waggledance.core.bridge_event_schema import validate_event


_FIXED = datetime(2026, 6, 21, 4, 30, 0, tzinfo=timezone.utc)


def _good_digest() -> dict:
    return {
        "report_version": mod.DIGEST_REPORT_VERSION,
        "reviewer_summary_present": True,
        "shadow_only_invariant_present": True,
        "chain_final_summary_present": True,
        "all_views_present": True,
        "reviewer_clean": True,
        "shadow_only_clean": True,
        "chain_summary_clean": True,
        "cross_consistent": True,
        "path_free_verified": True,
        "claim_safe": False,
    }


def _entry(digest=None, **kw) -> dict:
    params = {
        "digest": _good_digest() if digest is None else digest,
        "agent_id": "codex-lead-1",
        "task_id": "demo-task",
        "now_utc": _FIXED,
    }
    params.update(kw)
    return mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        **params
    )


def test_good_digest_builds_index_entry() -> None:
    entry = _entry()

    assert entry["ok"] is True
    assert entry["index_entry_version"] == mod.INDEX_ENTRY_VERSION
    assert entry["artifact_count"] == 1
    tie = entry["template_index_entry"]
    assert tie["artifact_id"] == mod.TEMPLATE_ARTIFACT_ID
    assert tie["bridge_event_schema_validated"] is True
    assert tie["cross_consistent"] is True
    assert tie["cross_consistency_digest_ref"].startswith("sha256:")
    assert entry["template_only"] is True
    assert entry["manual_review_required"] is True


def test_validate_passes_for_good_entry() -> None:
    entry = _entry()

    assert (
        mod.validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            entry
        )
        == []
    )


def test_template_report_is_schema_valid() -> None:
    report = mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        digest=_good_digest(),
        agent_id="codex-lead-1",
        task_id="demo-task",
        now_utc=_FIXED,
    )

    assert report["ok"] is True
    event = report["bridge_event_template"]
    validate_event(event)
    assert event["status"] == mod.EVENT_STATUS
    assert event["payload"]["schema_version"] == mod.TEMPLATE_VERSION
    assert (
        event["payload"]["authority_boundary"][
            "runtime_subdivision_authority_granted"
        ]
        is False
    )


def test_index_entry_authority_axes_all_false() -> None:
    entry = _entry()

    for field in mod.AUTHORITY_FALSE_FIELDS:
        assert entry[field] is False, field
        assert entry["template_index_entry"][field] is False, field
    assert entry["claim_safe"] is False


def test_index_entry_content_safe_no_raw_event_or_paths() -> None:
    entry = _entry()
    blob = json.dumps(entry)

    for raw in ("template ready", "codex-lead-1", "\"ts_utc\"", "demo-task"):
        assert raw not in blob, raw
    assert "C:" + "\\" not in blob
    for field in mod._DIGEST_VERDICT_FIELDS:
        assert isinstance(entry["template_index_entry"][field], bool), field


def test_main_exit0_with_digest_json(tmp_path, capsys) -> None:
    digest_path = tmp_path / "digest.json"
    digest_path.write_text(json.dumps(_good_digest()), encoding="utf-8")

    assert (
        mod.main(
            [
                "--digest-json",
                str(digest_path),
                "--agent",
                "codex-lead-1",
                "--task-id",
                "demo-task",
                "--now",
                "2026-06-21T04:30:00Z",
                "--json",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert json.loads(out)["ok"] is True


@pytest.mark.parametrize("digest", [None, "nope", 7, [], {}])
def test_non_mapping_or_empty_digest_fails(digest) -> None:
    if digest is None:
        entry = mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            digest=None,
            now_utc=_FIXED,
        )
    else:
        entry = _entry(digest=digest)

    assert entry["ok"] is False
    assert entry["claim_safe"] is False


def test_digest_version_mismatch_fails() -> None:
    digest = _good_digest()
    digest["report_version"] = "wd.other.v1"

    assert _entry(digest=digest)["ok"] is False


def test_digest_self_claim_safe_fails() -> None:
    digest = _good_digest()
    digest["claim_safe"] = True

    assert _entry(digest=digest)["ok"] is False


@pytest.mark.parametrize("field", list(mod._DIGEST_VERDICT_FIELDS))
def test_digest_verdict_non_bool_fails(field) -> None:
    digest = _good_digest()
    digest[field] = 1

    assert _entry(digest=digest)["ok"] is False


def test_digest_extra_raw_field_fails_closed() -> None:
    digest = _good_digest()
    digest["injected"] = "C:" + "\\" + "private" + "\\" + "plan.json"

    entry = _entry(digest=digest)
    assert entry["ok"] is False
    assert "private" not in json.dumps(entry).lower()


@pytest.mark.parametrize("field", list(mod._TEMPLATE_REPORT_FALSE_FIELDS))
def test_template_report_authority_true_fails(field) -> None:
    template = mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        digest=_good_digest(),
        agent_id="codex-lead-1",
        task_id="demo-task",
        now_utc=_FIXED,
    )
    template[field] = True

    entry = mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=template,
        bridge_event_template_bytes=json.dumps(template, sort_keys=True).encode(),
        now_utc=_FIXED,
    )
    assert entry["ok"] is False, field


def test_validate_rejects_authority_true() -> None:
    entry = _entry()
    bad = copy.deepcopy(entry)
    bad["runtime_subdivision_authority_granted"] = True

    errors = (
        mod.validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            bad
        )
    )
    assert any("runtime_subdivision_authority_granted" in e for e in errors)


def test_failure_report_is_safe() -> None:
    report = mod._failure_report("some_reason")

    assert report["ok"] is False
    assert report["claim_safe"] is False
    assert "template_index_entry" not in report
