"""Contract tests for digest/index-entry warning fail-closed boundaries.

Derived local index entries are allowed to expose only digest-bound safe
scalars. Source-report warnings are caller-controlled text, so they must never
be copied into an ok=true digest-only output.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template import (
    build_hex_upgrade_cross_consistency_digest_bridge_event_template,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)

_FIXED = datetime(2026, 6, 21, 4, 30, 0, tzinfo=timezone.utc)


def _good_digest() -> dict:
    return {
        "report_version": "wd.hex_upgrade_cross_consistency_digest.v1",
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


def _template_report() -> dict:
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        digest=_good_digest(),
        agent_id="fable-5",
        task_id="contract-task",
        now_utc=_FIXED,
    )


def _index_entry(report: dict) -> dict:
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=report,
        bridge_event_template_bytes=json.dumps(report, sort_keys=True).encode(),
        now_utc=_FIXED,
    )


def test_index_entry_rejects_source_warnings_without_copying_warning_text() -> None:
    report = _template_report()
    report["warnings"] = [
        "raw bridge event message=template ready agent=fable-5 ts_utc=2026-06-21T04:30:00Z"
    ]

    entry = _index_entry(report)
    encoded = json.dumps(entry, sort_keys=True)

    assert entry["ok"] is False
    assert "bridge_event_template_warnings_present" in entry["blockers"][0]
    assert entry["warnings"] == []
    for raw_text in (
        "template ready",
        "fable-5",
        "2026-06-21T04:30:00Z",
        "ts_utc",
    ):
        assert raw_text not in encoded


def test_index_entry_success_output_keeps_warnings_empty() -> None:
    entry = _index_entry(_template_report())

    assert entry["ok"] is True
    assert entry["warnings"] == []
