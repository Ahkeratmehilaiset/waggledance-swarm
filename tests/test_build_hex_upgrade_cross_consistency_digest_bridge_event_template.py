"""Offline tests for build_hex_upgrade_cross_consistency_digest_bridge_event_template.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template as mod
from waggledance.core.bridge_event_schema import validate_event

REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXED_NOW = "2026-06-19T06:30:00Z"


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


def _build(digest=None, **kw):
    params = dict(
        digest=_good_digest() if digest is None else digest,
        agent_id="fable-5",
        task_id="demo-task",
        now_utc=mod._parse_utc(_FIXED_NOW),
    )
    params.update(kw)
    return mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        **params
    )


def test_real_digest_renders_schema_valid_template() -> None:
    from tools.run_hex_upgrade_cross_consistency_digest import (
        build_cross_consistency_digest,
    )
    from tools.wd_image1_capability_manifest import build_manifest

    proof = next(
        capability["proof"]
        for capability in build_manifest(REPO_ROOT)["capabilities"]
        if capability["capability_id"] == "hexagonal_upgrades"
    )
    digest = build_cross_consistency_digest(proof)

    report = _build(digest=digest)

    assert report["ok"] is True
    event = report["bridge_event_template"]
    validate_event(event)
    assert event["status"] == mod.EVENT_STATUS
    assert event["payload"]["schema_version"] == mod.TEMPLATE_VERSION
    assert (
        event["payload"]["cross_consistency"]["cross_consistent"]
        is digest["cross_consistent"]
        is True
    )


def test_main_exit0_and_exit1() -> None:
    assert (
        mod.main(
            [
                "--agent",
                "fable-5",
                "--task-id",
                "demo",
                "--now",
                _FIXED_NOW,
                "--json",
            ]
        )
        == 0
    )
    assert (
        mod.main(
            [
                "--agent",
                "Bad Agent!",
                "--task-id",
                "demo",
                "--now",
                _FIXED_NOW,
                "--json",
            ]
        )
        == 1
    )


def test_template_only_authority_axes_all_false() -> None:
    report = _build()
    event = report["bridge_event_template"]
    boundary = event["payload"]["authority_boundary"]
    for axis in (
        "approval_granted",
        "release_decision_made",
        "merge_decision_made",
        "promotion_granted",
        "claim_safe",
        "literal_future_claim_safe",
        "runtime_authority_granted",
        "runtime_subdivision_authority_granted",
        "bridge_event_written",
        "gate_skip_allowed",
        "fast_track_priority",
    ):
        assert boundary[axis] is False, axis
    assert boundary["manual_review_required"] is True
    payload = event["payload"]
    for flag in (
        "direct_bridge_write_performed",
        "transport_added",
        "external_fetch_performed",
        "runtime_controls_added",
        "digest_payloads_included",
        "local_paths_recorded",
    ):
        assert payload[flag] is False, flag
    assert payload["template_only"] is True
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["claim_safe"] is False


@pytest.mark.parametrize("field", list(mod._DIGEST_VERDICT_FIELDS))
def test_verdicts_rendered_faithfully_without_upgrade(field: str) -> None:
    digest = _good_digest()
    digest[field] = False

    report = _build(digest=digest)

    assert report["ok"] is True
    cross = report["bridge_event_template"]["payload"]["cross_consistency"]
    assert cross[field] is False


@pytest.mark.parametrize("field", list(mod._DIGEST_VERDICT_FIELDS))
def test_verdict_non_bool_fails_closed(field: str) -> None:
    for bad in (1, 0, "true", None):
        digest = _good_digest()
        digest[field] = bad
        assert _build(digest=digest)["ok"] is False, (field, bad)


def test_digest_self_claim_safe_refused() -> None:
    digest = _good_digest()
    digest["claim_safe"] = True

    report = _build(digest=digest)

    assert report["ok"] is False
    assert "digest_self_claim_safe" in report["blockers"][0]


@pytest.mark.parametrize("digest", [None, "nope", 7, [], {}])
def test_non_mapping_or_empty_digest_fails(digest) -> None:
    if digest is None:
        report = mod.build_hex_upgrade_cross_consistency_digest_bridge_event_template(
            digest=None,
            agent_id="fable-5",
            task_id="demo",
            now_utc=mod._parse_utc(_FIXED_NOW),
        )
    else:
        report = _build(digest=digest)
    assert report["ok"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False


@pytest.mark.parametrize(
    "kw,bad",
    [
        ("agent_id", "Bad Agent"),
        ("agent_id", ""),
        ("task_id", "has space"),
        ("to", ""),
        ("to", "Bad,Targets!"),
        ("severity", "critical"),
        ("role", "Bad Role"),
        ("run_id", "bad id"),
        ("session_id", "bad session!!"),
    ],
)
def test_unsafe_bridge_inputs_fail(kw: str, bad: str) -> None:
    assert _build(**{kw: bad})["ok"] is False


def test_path_free_allowlist_and_digest_ref() -> None:
    report = _build()
    event = report["bridge_event_template"]
    blob = json.dumps(event)
    assert str(REPO_ROOT) not in blob
    assert mod._contains_path_marker(event) is False
    assert report["path_free_verified"] is True
    cross = event["payload"]["cross_consistency"]
    assert set(cross) <= mod._TEMPLATE_SAFE_KEYS
    for key, value in cross.items():
        if key in ("digest_schema_version", "digest_ref"):
            assert isinstance(value, str)
        else:
            assert isinstance(value, bool), (key, value)
    assert cross["digest_ref"].startswith("sha256:")
    assert len(cross["digest_ref"]) == len("sha256:") + 64


def test_path_tainted_digest_fails_closed() -> None:
    digest = _good_digest()
    digest["injected"] = "C:" + "\\" + "secret" + "\\plan.json"

    report = _build(digest=digest)

    assert report["ok"] is False
    serialized = json.dumps(report)
    assert "secret" not in serialized
    assert "plan.json" not in serialized


def test_error_report_is_safe_and_fail_closed() -> None:
    report = mod._error_report("some_reason")
    assert report["ok"] is False
    assert report["claim_safe"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert "bridge_event_template" not in report


def test_vocabulary_clean() -> None:
    report = _build()
    mod.assert_vocabulary_clean(json.dumps(report))
    mod.assert_vocabulary_clean(report["bridge_event_template"]["message"])
