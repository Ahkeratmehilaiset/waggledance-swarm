from __future__ import annotations

from pathlib import Path

import pytest

import tools.backfill_axioms_to_hex as backfill


def test_write_ledger_entries_rejects_path_like_cell_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_dir = tmp_path / "ledger"
    monkeypatch.setattr(backfill, "LEDGER_DIR", ledger_dir)

    with pytest.raises(ValueError, match="cell_id"):
        backfill.write_ledger_entries(
            "thermal/../../escape",
            [{"seq": 1, "ok": True}],
            "axiom_backfill",
            "2026-05-24T12:00:00+00:00",
        )

    assert not (tmp_path / "escape").exists()
    assert not ledger_dir.exists()


def test_write_ledger_entries_rejects_path_like_source_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_dir = tmp_path / "ledger"
    monkeypatch.setattr(backfill, "LEDGER_DIR", ledger_dir)

    with pytest.raises(ValueError, match="source"):
        backfill.write_ledger_entries(
            "thermal",
            [{"seq": 1, "ok": True}],
            "../escape",
            "2026-05-24T12:00:00+00:00",
        )

    assert not (tmp_path / "escape").exists()
    assert not ledger_dir.exists()


def test_write_ledger_entries_accepts_known_cell_under_ledger_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger_dir = tmp_path / "ledger"
    monkeypatch.setattr(backfill, "LEDGER_DIR", ledger_dir)

    path = backfill.write_ledger_entries(
        "thermal",
        [{"seq": 1, "ok": True}],
        "axiom_backfill",
        "2026-05-24T12:00:00+00:00",
    )

    assert path == (
        ledger_dir / "thermal" / "000001_axiom_backfill_20260524T120000.jsonl"
    )
    assert path.exists()
    assert path.resolve().is_relative_to(ledger_dir.resolve())


def test_audit_placement_rejects_unknown_cell_id_before_backfill_write() -> None:
    ok, audit = backfill.audit_placement(
        {
            "cell_id": "thermal/../../escape",
            "model_id": "malicious_cell",
            "model_name": "malicious cell",
            "description": "thermal model",
        },
        first_view_vec=None,
        centroids={},
    )

    assert ok is False
    assert audit["error"] == "cell_id must be a known filename-safe cell"
