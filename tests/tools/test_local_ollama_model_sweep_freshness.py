# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "benchmarks" / "LOCAL_OLLAMA_MODEL_SWEEP_2026.md"


def _read_doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _freshness_note(text: str) -> str:
    start = text.index("## Freshness notes")
    end = text.index("## Reproduce", start)
    return text[start:end]


def _measured_model_rows(text: str) -> list[str]:
    start = text.index("**Phase 17D host run (this session):**")
    end = text.index("**Side-by-side observations", start)
    rows: list[str] = []
    for line in text[start:end].splitlines():
        if line.startswith("| `"):
            rows.append(line.split("|", 2)[1].strip(" `"))
    return rows


def test_20260606_inventory_note_does_not_replace_phase17d_measurement() -> None:
    text = _read_doc()
    note = _freshness_note(text)

    assert "**Date:** 2026-05-05" in text
    assert "2026-06-06 bridge audit" in note
    assert "Ollama 0.24.0" in note
    for model in (
        "gemma3:4b",
        "qwen3:4b",
        "llama3.2:3b",
        "llama3.1:8b",
        "nomic-embed-text:latest",
        "all-minilm:latest",
    ):
        assert f"`{model}`" in note
    assert "inventory check only" in note
    assert "does not rerun `tools/run_phase17d_local_model_sweep.py`" in note
    assert "does not replace the 2026-05-05 artifact" in note
    assert "does not upgrade `MEASURED-LOCAL-OLLAMA-PANEL`" in note


def test_inventory_only_models_are_not_added_to_measured_panel() -> None:
    rows = _measured_model_rows(_read_doc())

    assert rows == [
        "gemma4:e4b",
        "gemma3:4b",
        "llama3.2:3b",
        "phi4-mini:latest",
    ]
    assert "qwen3:4b" not in rows
    assert "llama3.1:8b" not in rows
