"""Tests for backfill_provenance tool."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.backfill_provenance import grade_to_source_type, load_trajectories


class TestGradeToSourceType:
    def test_gold(self):
        assert grade_to_source_type("gold") == "confirmed_by_verifier"

    def test_silver(self):
        assert grade_to_source_type("silver") == "inferred_by_solver"

    def test_bronze(self):
        assert grade_to_source_type("bronze") == "proposed_by_llm"

    def test_quarantine(self):
        assert grade_to_source_type("quarantine") == "proposed_by_llm"

    def test_unknown_defaults(self):
        assert grade_to_source_type("unknown") == "proposed_by_llm"


class TestLoadTrajectories:
    def test_missing_file(self, tmp_path):
        result = load_trajectories(str(tmp_path / "nonexistent.jsonl"))
        assert result == []

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.jsonl"
        f.write_text("")
        result = load_trajectories(str(f))
        assert result == []

    def test_valid_jsonl(self, tmp_path):
        f = tmp_path / "data.jsonl"
        f.write_text('{"trajectory_id": "t1", "quality_grade": "gold"}\n'
                      '{"trajectory_id": "t2", "quality_grade": "silver"}\n')
        result = load_trajectories(str(f))
        assert len(result) == 2
        assert result[0]["trajectory_id"] == "t1"

    def test_invalid_json_skipped(self, tmp_path):
        f = tmp_path / "mixed.jsonl"
        f.write_text('{"ok": true}\nnot json\n{"ok": false}\n')
        result = load_trajectories(str(f))
        assert len(result) == 2
