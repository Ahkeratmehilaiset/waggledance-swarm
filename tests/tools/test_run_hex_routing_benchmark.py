# SPDX-License-Identifier: Apache-2.0
"""Tests for the hex routing enable benchmark harness (sprint seed #11)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from run_hex_routing_benchmark import (  # noqa: E402
    WORKLOAD,
    render_markdown,
    run_benchmark,
)


def test_benchmark_is_deterministic_between_runs() -> None:
    first = run_benchmark(queries_per_category=1)
    second = run_benchmark(queries_per_category=1)
    assert first["deterministic_views"] == second["deterministic_views"]


def test_disabled_mode_always_falls_back() -> None:
    result = run_benchmark(queries_per_category=1)
    disabled = result["modes"]["disabled"]
    assert disabled["outcomes"]["resolved"] == 0
    assert disabled["outcomes"]["fallback_none"] == len(WORKLOAD)
    assert disabled["llm_calls"] == 0


def test_enabled_mode_actually_routes() -> None:
    result = run_benchmark(queries_per_category=1)
    enabled = result["modes"]["enabled"]
    # The point of seed #11: enabling the mesh must DO something -
    # local resolutions happen and the LLM is exercised.
    assert enabled["outcomes"]["resolved"] > 0
    assert enabled["llm_calls"] > 0
    assert enabled["metrics"]["cells_loaded"] == 7
    total = enabled["outcomes"]["resolved"] + enabled["outcomes"]["fallback_none"]
    assert total == len(WORKLOAD)


def test_result_schema_and_markdown_render() -> None:
    result = run_benchmark(queries_per_category=1)
    assert result["benchmark"] == "hex_routing_enable_benchmark.v1"
    for mode in ("disabled", "enabled"):
        view = result["deterministic_views"][mode]
        assert "_runtime_seconds" not in view  # wall-clock excluded
        assert json.dumps(view, sort_keys=True)  # JSON-serializable
    md = render_markdown(result)
    assert "| metric | hex_mesh disabled | hex_mesh enabled |" in md
    assert "neighbor-assist" in md


def test_cli_entrypoint_writes_reports(tmp_path: Path) -> None:
    out_json = tmp_path / "bench.json"
    out_md = tmp_path / "bench.md"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "run_hex_routing_benchmark.py"),
         "--queries-per-category", "1",
         "--output-json", str(out_json), "--output-md", str(out_md)],
        capture_output=True, text=True, timeout=300, cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["benchmark"] == "hex_routing_enable_benchmark.v1"
    assert out_md.read_text(encoding="utf-8").startswith("# Hex routing benchmark")
