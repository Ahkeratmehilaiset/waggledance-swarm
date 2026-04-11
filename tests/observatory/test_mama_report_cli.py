# SPDX-License-Identifier: Apache-2.0
"""End-to-end test for ``tools/mama_event_report.py``.

Spawns the report writer against a tmp directory and verifies that
every expected file is produced and is JSON-safe / markdown-safe.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from waggledance.observatory.mama_events.reports import assert_no_hype

_TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "mama_event_report.py"


def _load_tool_module():
    if "mama_event_report" in sys.modules:
        return sys.modules["mama_event_report"]
    spec = importlib.util.spec_from_file_location("mama_event_report", _TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register in sys.modules BEFORE exec_module so @dataclass annotations
    # can resolve cls.__module__ during class construction.
    sys.modules["mama_event_report"] = module
    spec.loader.exec_module(module)
    return module


def test_write_reports_produces_every_expected_artifact(tmp_path: Path):
    mod = _load_tool_module()
    arts = mod.write_reports(
        reports_dir=tmp_path / "reports",
        logs_dir=tmp_path / "logs",
    )

    # every file must exist
    for p in (
        arts.framework,
        arts.baseline,
        arts.ablations,
        arts.candidates,
        arts.gate,
        arts.events_ndjson,
        arts.binding_ndjson,
        arts.self_state_ndjson,
        arts.ablation_json,
    ):
        assert p.exists(), f"missing artifact: {p}"
        assert p.stat().st_size > 0, f"empty artifact: {p}"


def test_write_reports_all_markdown_is_hype_free(tmp_path: Path):
    mod = _load_tool_module()
    arts = mod.write_reports(
        reports_dir=tmp_path / "reports",
        logs_dir=tmp_path / "logs",
    )

    for path in (arts.framework, arts.baseline, arts.ablations, arts.candidates, arts.gate):
        text = path.read_text(encoding="utf-8")
        assert_no_hype(text)


def test_write_reports_ndjson_is_valid(tmp_path: Path):
    mod = _load_tool_module()
    arts = mod.write_reports(
        reports_dir=tmp_path / "reports",
        logs_dir=tmp_path / "logs",
    )

    for path in (arts.events_ndjson, arts.binding_ndjson, arts.self_state_ndjson):
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert lines, f"no lines in {path}"
        for line in lines:
            json.loads(line)  # must be valid JSON


def test_write_reports_ablation_json_roundtrips(tmp_path: Path):
    mod = _load_tool_module()
    arts = mod.write_reports(
        reports_dir=tmp_path / "reports",
        logs_dir=tmp_path / "logs",
    )

    parsed = json.loads(arts.ablation_json.read_text(encoding="utf-8"))
    assert "baseline" in parsed
    assert "ablations" in parsed
    assert len(parsed["ablations"]) == 5


def test_write_reports_is_idempotent(tmp_path: Path):
    """Running twice must not corrupt any file (ndjson gets truncated)."""
    mod = _load_tool_module()
    first = mod.write_reports(
        reports_dir=tmp_path / "reports",
        logs_dir=tmp_path / "logs",
    )
    second = mod.write_reports(
        reports_dir=tmp_path / "reports",
        logs_dir=tmp_path / "logs",
    )
    # ndjson file lines must be identical between runs (no double-append)
    a = first.events_ndjson.read_text(encoding="utf-8")
    b = second.events_ndjson.read_text(encoding="utf-8")
    assert a == b
