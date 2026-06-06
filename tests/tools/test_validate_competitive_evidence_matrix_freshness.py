# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import importlib
import json
from datetime import date


def _matrix(
    *,
    snapshot_date: str = "2026-05-06",
    audit_date: str = "2026-06-06",
    metadata: str | None = (
        "`snapshot_date=2026-05-06`; `freshness_audit_date=2026-06-06`; "
        "`prior_freshness_audit_date=2026-05-27`; "
        "`max_age_days=14`; `status=historical_stale`; "
        "`fresh_for_planning=false`; `priority_rows=G,J,L`; "
        "`historical_labels_until_refreshed=true`."
    ),
    labels: str | None = None,
) -> str:
    if labels is None:
        labels = """
### G. 10,000-solver capability scale

* **Label:** **MEASURED this session.** **PROVEN** that the data path works.

### J. LLM / MoE fallback as a hybrid

* **Label:** **INFERRED** for the architecture; **MEASURED-LOCAL-OLLAMA-PANEL** for the local panel.

### L. Edge resource use

* **Label:** **MEASURED** image size; **INFERRED** edge fitness.
"""
    meta_line = f"**Freshness metadata:** {metadata}\n" if metadata is not None else ""
    return f"""# Competitive Evidence Matrix - fixture

**Evidence snapshot date:** {snapshot_date}
**Freshness audit:** {audit_date} read-only audit found stale evidence.
{meta_line}
## Axes
{labels}
"""


def test_current_repo_matrix_is_valid_but_not_fresh_for_planning() -> None:
    mod = importlib.import_module(
        "tools.validate_competitive_evidence_matrix_freshness"
    )
    text = mod.DEFAULT_MATRIX.read_text(encoding="utf-8")

    report = mod.validate_matrix_freshness(text, now=date(2026, 6, 6))

    assert report["ok"] is True
    assert report["fresh_for_planning"] is False
    assert report["historical_stale_allowed"] is True
    assert report["freshness_audit_date"] == "2026-06-06"
    assert report["snapshot_age_days"] == 31
    assert report["priority_rows"] == ["G", "J", "L"]


def test_stale_matrix_without_historical_metadata_fails() -> None:
    mod = importlib.import_module(
        "tools.validate_competitive_evidence_matrix_freshness"
    )
    text = _matrix(metadata=None)

    report = mod.validate_matrix_freshness(text, now=date(2026, 6, 4))

    assert report["ok"] is False
    assert "stale_evidence_not_marked_historical" in report["blockers"]
    assert "max_age_days_missing" in report["blockers"]


def test_fresh_matrix_can_be_fresh_for_planning() -> None:
    mod = importlib.import_module(
        "tools.validate_competitive_evidence_matrix_freshness"
    )
    text = _matrix(
        snapshot_date="2026-06-01",
        audit_date="2026-06-01",
        metadata=(
            "`snapshot_date=2026-06-01`; `freshness_audit_date=2026-06-01`; "
            "`max_age_days=14`; `status=current`; "
            "`fresh_for_planning=true`; `priority_rows=G,J,L`; "
            "`historical_labels_until_refreshed=false`."
        ),
    )

    report = mod.validate_matrix_freshness(text, now=date(2026, 6, 4))

    assert report["ok"] is True
    assert report["fresh_for_planning"] is True
    assert report["historical_stale_allowed"] is False


def test_age_equal_to_max_age_is_still_fresh() -> None:
    mod = importlib.import_module(
        "tools.validate_competitive_evidence_matrix_freshness"
    )
    text = _matrix(
        snapshot_date="2026-05-21",
        audit_date="2026-05-21",
        metadata=(
            "`snapshot_date=2026-05-21`; `freshness_audit_date=2026-05-21`; "
            "`max_age_days=14`; `status=current`; "
            "`fresh_for_planning=true`; `priority_rows=G,J,L`; "
            "`historical_labels_until_refreshed=false`."
        ),
    )

    report = mod.validate_matrix_freshness(text, now=date(2026, 6, 4))

    assert report["ok"] is True
    assert report["snapshot_age_days"] == 14
    assert report["fresh_for_planning"] is True


def test_require_fresh_fails_historical_stale_matrix(capsys) -> None:
    mod = importlib.import_module(
        "tools.validate_competitive_evidence_matrix_freshness"
    )

    rc = mod.main(["--now", "2026-06-04", "--require-fresh", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["historical_stale_allowed"] is True
    assert "snapshot_age_exceeds_max_age" in payload["blockers"]


def test_metadata_fresh_for_planning_must_match_age() -> None:
    mod = importlib.import_module(
        "tools.validate_competitive_evidence_matrix_freshness"
    )
    text = _matrix(
        metadata=(
            "`snapshot_date=2026-05-06`; `freshness_audit_date=2026-05-27`; "
            "`max_age_days=14`; `status=historical_stale`; "
            "`fresh_for_planning=true`; `priority_rows=G,J,L`; "
            "`historical_labels_until_refreshed=true`."
        ),
    )

    report = mod.validate_matrix_freshness(text, now=date(2026, 6, 4))

    assert report["ok"] is False
    assert "fresh_for_planning_mismatch" in report["blockers"]


def test_invalid_metadata_values_fail_closed() -> None:
    mod = importlib.import_module(
        "tools.validate_competitive_evidence_matrix_freshness"
    )
    text = _matrix(
        snapshot_date="2026-05-06",
        audit_date="2026-05-27",
        metadata=(
            "`snapshot_date=2026-05-06`; `freshness_audit_date=not-a-date`; "
            "`max_age_days=nan`; `status=historical_stale`; "
            "`fresh_for_planning=maybe`; `priority_rows=G,too-long`; "
            "`historical_labels_until_refreshed=maybe`."
        ),
    )

    report = mod.validate_matrix_freshness(text, now=date(2026, 6, 4))

    assert report["ok"] is False
    assert "freshness_audit_date_invalid" in report["blockers"]
    assert "max_age_days_invalid" in report["blockers"]
    assert "fresh_for_planning_invalid" in report["blockers"]
    assert "priority_rows_invalid" in report["blockers"]
    assert "historical_labels_until_refreshed_invalid" in report["blockers"]
    assert "stale_evidence_not_marked_historical" in report["blockers"]


def test_priority_rows_must_point_to_evidence_bearing_axes() -> None:
    mod = importlib.import_module(
        "tools.validate_competitive_evidence_matrix_freshness"
    )
    text = _matrix(
        metadata=(
            "`snapshot_date=2026-05-06`; `freshness_audit_date=2026-05-27`; "
            "`max_age_days=14`; `status=historical_stale`; "
            "`fresh_for_planning=false`; `priority_rows=G,Z`; "
            "`historical_labels_until_refreshed=true`."
        ),
    )

    report = mod.validate_matrix_freshness(text, now=date(2026, 6, 4))

    assert report["ok"] is False
    assert "priority_rows_not_evidence_bearing:Z" in report["blockers"]
