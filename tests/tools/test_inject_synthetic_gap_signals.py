# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tools.inject_synthetic_gap_signals import (
    SYNTHETIC_KIND,
    SyntheticInjectionError,
    _looks_like_live_db,
    inject_synthetic_signals,
    main,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def _kwargs(tmp_path: Path, **overrides):
    base = {
        "db_path": tmp_path / "test_control_plane.db",
        "cells": ("hub", "bee_ops", "environment", "home_comfort"),
        "rows_per_cell": 3,
        "concurrent_cells": 4,
        "window_seconds": 1,
        "run_id": "test-run-001",
        "source": "unit-test",
        "allow_live": False,
        "synthetic_confirm": False,
        "apply": False,
        "now_utc": NOW,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def test_zero_cells_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SyntheticInjectionError) as excinfo:
        inject_synthetic_signals(**_kwargs(tmp_path, cells=()))
    assert excinfo.value.report["decision"] == "invalid_arguments"


def test_zero_rows_per_cell_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SyntheticInjectionError) as excinfo:
        inject_synthetic_signals(**_kwargs(tmp_path, rows_per_cell=0))
    assert excinfo.value.report["decision"] == "invalid_arguments"


def test_zero_window_seconds_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SyntheticInjectionError) as excinfo:
        inject_synthetic_signals(**_kwargs(tmp_path, window_seconds=0))
    assert excinfo.value.report["decision"] == "invalid_arguments"


def test_concurrent_cells_above_cells_count_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SyntheticInjectionError) as excinfo:
        inject_synthetic_signals(
            **_kwargs(tmp_path, cells=("hub", "bee_ops"), concurrent_cells=5)
        )
    assert excinfo.value.report["decision"] == "invalid_arguments"


# ---------------------------------------------------------------------------
# live-DB guard (Codex RCO constraint)
# ---------------------------------------------------------------------------


def test_live_db_path_is_detected() -> None:
    assert _looks_like_live_db(Path("data/control_plane.db")) is True
    assert _looks_like_live_db(Path("data") / "control_plane.db") is True
    assert _looks_like_live_db(
        Path("/somewhere/project/data/control_plane.db")
    ) is True


def test_temp_db_path_is_not_treated_as_live(tmp_path: Path) -> None:
    assert _looks_like_live_db(tmp_path / "control_plane.db") is False
    # Different parent name -> not the live path.
    assert _looks_like_live_db(tmp_path / "synthetic" / "control_plane.db") is False


def test_live_db_target_without_flags_is_refused(tmp_path: Path) -> None:
    # Construct a live-looking relative path; the function should refuse
    # before any IO is attempted.
    report = inject_synthetic_signals(
        **_kwargs(tmp_path, db_path=Path("data/control_plane.db"))
    )
    assert report["decision"] == "refused_live_db"
    assert report["ok"] is False
    assert report["apply"] is False
    assert report["exit_code"] == 3


def test_live_db_target_with_only_allow_live_is_refused(tmp_path: Path) -> None:
    report = inject_synthetic_signals(
        **_kwargs(
            tmp_path,
            db_path=Path("data/control_plane.db"),
            allow_live=True,
            synthetic_confirm=False,
        )
    )
    assert report["decision"] == "refused_live_db"


def test_live_db_target_with_only_synthetic_confirm_is_refused(
    tmp_path: Path,
) -> None:
    report = inject_synthetic_signals(
        **_kwargs(
            tmp_path,
            db_path=Path("data/control_plane.db"),
            allow_live=False,
            synthetic_confirm=True,
        )
    )
    assert report["decision"] == "refused_live_db"


# ---------------------------------------------------------------------------
# dry-run and apply behavior
# ---------------------------------------------------------------------------


def test_dry_run_produces_plan_but_no_db_writes(tmp_path: Path) -> None:
    report = inject_synthetic_signals(**_kwargs(tmp_path))
    assert report["decision"] == "dry_run_ready"
    assert report["apply"] is False
    assert report["would_write_rows"] == 3 * 4  # rows_per_cell * cells
    # No DB file should have been created in dry-run.
    assert not (tmp_path / "test_control_plane.db").exists()


def test_apply_writes_rows_and_all_are_tagged_synthetic(tmp_path: Path) -> None:
    report = inject_synthetic_signals(**_kwargs(tmp_path, apply=True))
    assert report["decision"] == "applied"
    assert report["apply"] is True
    assert report["wrote_rows"] == 12
    assert report["kind"] == SYNTHETIC_KIND

    with ControlPlaneDB(tmp_path / "test_control_plane.db") as cp:
        rows = cp.list_runtime_gap_signals(limit=100)
    assert len(rows) == 12
    for row in rows:
        assert row.kind == SYNTHETIC_KIND
        payload = json.loads(row.signal_payload)
        assert payload["synthetic"] is True
        assert payload["source"] == "unit-test"
        assert payload["run_id"] == "test-run-001"
        assert payload["cell_coord"] in {"hub", "bee_ops", "environment", "home_comfort"}


# ---------------------------------------------------------------------------
# R25 histogram visibility (the whole point)
# ---------------------------------------------------------------------------


def test_r25_histogram_sees_synthetic_concurrency(tmp_path: Path) -> None:
    """End-to-end check: after injecting 4-cell concurrent synthetic rows,
    the R25 decision tool must see N>=4 concurrent-cell windows."""
    report = inject_synthetic_signals(
        **_kwargs(
            tmp_path,
            cells=("hub", "bee_ops", "environment", "home_comfort"),
            rows_per_cell=5,
            concurrent_cells=4,
            apply=True,
        )
    )
    assert report["decision"] == "applied"

    # Read back via the R25 histogram primitive and confirm the synthetic
    # concurrency is visible.
    from tools.runtime_gap_signal_concurrency_histogram import build_histogram

    summary = build_histogram(
        tmp_path / "test_control_plane.db", window_seconds=1
    )
    # Every active window has all 4 cells co-emitting -> N>=4 must be visible.
    assert summary["concurrency_per_window_summary"]["max"] >= 4
    assert summary["sla_thresholds"]["N_ge_4"]["windows"] > 0


def test_r25_histogram_with_one_cell_only_does_not_show_concurrency(
    tmp_path: Path,
) -> None:
    """Sanity inverse: 1-cell injection must not produce N>=2 concurrency."""
    report = inject_synthetic_signals(
        **_kwargs(
            tmp_path,
            cells=("hub",),
            rows_per_cell=5,
            concurrent_cells=1,
            apply=True,
        )
    )
    assert report["decision"] == "applied"

    from tools.runtime_gap_signal_concurrency_histogram import build_histogram

    summary = build_histogram(
        tmp_path / "test_control_plane.db", window_seconds=1
    )
    assert summary["concurrency_per_window_summary"]["max"] == 1
    assert summary["sla_thresholds"]["N_ge_2"]["windows"] == 0


# ---------------------------------------------------------------------------
# substrate evidence guard
# ---------------------------------------------------------------------------


def test_applied_report_carries_substrate_evidence_warning(tmp_path: Path) -> None:
    """The 'applied' decision must document that synthetic data is test
    substrate only, not justification for R25 production rollout."""
    report = inject_synthetic_signals(**_kwargs(tmp_path, apply=True))
    assert any("test-substrate evidence only" in r for r in report["reasons"])


def test_dry_run_report_carries_substrate_evidence_warning(tmp_path: Path) -> None:
    report = inject_synthetic_signals(**_kwargs(tmp_path))
    assert any("cannot justify R25" in r for r in report["reasons"])


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_main_dry_run_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    target = tmp_path / "smoke.db"
    exit_code = main(
        [
            "--db",
            str(target),
            "--cells",
            "hub,bee_ops",
            "--rows-per-cell",
            "2",
            "--concurrent-cells",
            "2",
            "--window-seconds",
            "1",
            "--run-id",
            "smoke-test",
            "--source",
            "cli-smoke",
            "--now",
            "2026-05-20T12:00:00Z",
            "--json",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["decision"] == "dry_run_ready"
    assert parsed["would_write_rows"] == 4
    assert parsed["kind"] == SYNTHETIC_KIND


def test_cli_main_refuses_live_db_without_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """End-to-end CLI refusal of canonical production path."""
    exit_code = main(
        [
            "--db",
            "data/control_plane.db",
            "--apply",
            "--json",
        ]
    )
    assert exit_code == 3
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["decision"] == "refused_live_db"
