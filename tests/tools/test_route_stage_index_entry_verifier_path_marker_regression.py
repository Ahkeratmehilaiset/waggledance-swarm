# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "tests" / "tools"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

import test_verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry as handoff_bundle_index_helpers  # noqa: E402
import test_verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry as handoff_bundle_verifier_index_helpers  # noqa: E402
import test_verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry as feed_index_helpers  # noqa: E402
import test_verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry as feed_verifier_index_helpers  # noqa: E402
import test_verify_route_stage_handoff_verifier_summary_bridge_template_index_entry as handoff_verifier_index_helpers  # noqa: E402
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    TemplateIndexEntryVerificationError as HandoffBundleIndexVerificationError,
    verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    TemplateIndexEntryVerificationError as HandoffBundleVerifierIndexVerificationError,
    verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    TemplateIndexEntryVerificationError as FeedIndexVerificationError,
    verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    TemplateIndexEntryVerificationError as FeedVerifierIndexVerificationError,
    verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_route_stage_handoff_verifier_summary_bridge_template_index_entry import (  # noqa: E402
    TemplateIndexEntryVerificationError as HandoffVerifierIndexVerificationError,
    verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)


CREATED_AT_PATH_MARKERS = (
    "D:/wd/path-leak/index-entry.json",
    "/tmp/wd/path-leak/index-entry.json",
)


def test_route_stage_index_entry_verifiers_reject_path_markers_in_ignored_created_at() -> None:
    for case_name, verifier_call, error_type in _verification_cases():
        for marker in CREATED_AT_PATH_MARKERS:
            _assert_rejects_created_at_path_marker(
                case_name=case_name,
                verifier_call=verifier_call,
                error_type=error_type,
                marker=marker,
            )


def _assert_rejects_created_at_path_marker(
    *,
    case_name: str,
    verifier_call: Callable[[dict[str, Any]], Any],
    error_type: type[Exception],
    marker: str,
) -> None:
    try:
        verifier_call({"created_at_utc": marker})
    except error_type as exc:
        assert str(exc).endswith("_forbidden_marker"), case_name
        assert marker not in str(exc)
    else:
        raise AssertionError(f"{case_name} accepted path marker in created_at_utc")


def _verification_cases() -> list[
    tuple[str, Callable[[dict[str, Any]], Any], type[Exception]]
]:
    cases: list[tuple[str, Callable[[dict[str, Any]], Any], type[Exception]]] = []

    feed_artifacts = feed_index_helpers._artifact_set()
    feed_raw = feed_index_helpers._artifact_bytes(feed_artifacts)
    feed_index = feed_index_helpers._index_entry(feed_artifacts)
    cases.append(
        (
            "feed_index",
            lambda update: verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry(
                index_entry=_mutated(feed_index, update),
                verification_summary=feed_artifacts["summary"],
                bridge_event_template_report=feed_artifacts["template"],
                verification_summary_bytes=feed_raw["summary"],
                bridge_event_template_bytes=feed_raw["template"],
            ),
            FeedIndexVerificationError,
        )
    )

    handoff_bundle_artifacts = handoff_bundle_index_helpers._artifact_set()
    handoff_bundle_raw = handoff_bundle_index_helpers._artifact_bytes(
        handoff_bundle_artifacts
    )
    handoff_bundle_index = handoff_bundle_index_helpers._index_entry(
        handoff_bundle_artifacts
    )
    cases.append(
        (
            "handoff_bundle_index",
            lambda update: verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
                index_entry=_mutated(handoff_bundle_index, update),
                bundle_verification_summary=handoff_bundle_artifacts["summary"],
                summary_bridge_event_template_report=handoff_bundle_artifacts[
                    "template"
                ],
                bundle_verification_summary_bytes=handoff_bundle_raw["summary"],
                summary_bridge_event_template_bytes=handoff_bundle_raw["template"],
            ),
            HandoffBundleIndexVerificationError,
        )
    )

    handoff_bundle_verifier_artifacts = (
        handoff_bundle_verifier_index_helpers._artifact_set()
    )
    handoff_bundle_verifier_raw = handoff_bundle_verifier_index_helpers._artifact_bytes(
        handoff_bundle_verifier_artifacts
    )
    handoff_bundle_verifier_index = handoff_bundle_verifier_index_helpers._index_entry(
        handoff_bundle_verifier_artifacts
    )
    cases.append(
        (
            "handoff_bundle_verifier_index",
            lambda update: verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
                index_entry=_mutated(handoff_bundle_verifier_index, update),
                index_entry_verification_summary=handoff_bundle_verifier_artifacts[
                    "summary"
                ],
                summary_bridge_event_template_report=handoff_bundle_verifier_artifacts[
                    "template"
                ],
                index_entry_verification_summary_bytes=handoff_bundle_verifier_raw[
                    "summary"
                ],
                summary_bridge_event_template_bytes=handoff_bundle_verifier_raw[
                    "template"
                ],
            ),
            HandoffBundleVerifierIndexVerificationError,
        )
    )

    feed_verifier_artifacts = feed_verifier_index_helpers._artifact_set()
    feed_verifier_raw = feed_verifier_index_helpers._artifact_bytes(
        feed_verifier_artifacts
    )
    feed_verifier_index = feed_verifier_index_helpers._index_entry(
        feed_verifier_artifacts
    )
    cases.append(
        (
            "feed_verifier_index",
            lambda update: verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
                index_entry=_mutated(feed_verifier_index, update),
                index_entry_verification_summary=feed_verifier_artifacts[
                    "summary"
                ],
                summary_bridge_event_template_report=feed_verifier_artifacts[
                    "template"
                ],
                index_entry_verification_summary_bytes=feed_verifier_raw["summary"],
                summary_bridge_event_template_bytes=feed_verifier_raw["template"],
            ),
            FeedVerifierIndexVerificationError,
        )
    )

    handoff_verifier_artifacts = handoff_verifier_index_helpers._artifact_set()
    handoff_verifier_raw = handoff_verifier_index_helpers._artifact_bytes(
        handoff_verifier_artifacts
    )
    handoff_verifier_index = handoff_verifier_index_helpers._index_entry(
        handoff_verifier_artifacts
    )
    cases.append(
        (
            "handoff_verifier_index",
            lambda update: verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
                index_entry=_mutated(handoff_verifier_index, update),
                index_entry_verification_summary=handoff_verifier_artifacts[
                    "summary"
                ],
                summary_bridge_event_template_report=handoff_verifier_artifacts[
                    "template"
                ],
                index_entry_verification_summary_bytes=handoff_verifier_raw[
                    "summary"
                ],
                summary_bridge_event_template_bytes=handoff_verifier_raw["template"],
            ),
            HandoffVerifierIndexVerificationError,
        )
    )

    return cases


def _mutated(index_entry: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    mutated = deepcopy(index_entry)
    mutated.update(update)
    return mutated
