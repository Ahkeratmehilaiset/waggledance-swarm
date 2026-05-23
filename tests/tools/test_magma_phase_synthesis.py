# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tools/magma_phase_synthesis.py.

The committed baseline.json is used as a real fixture. Counter-read
reports are synthesized in memory. No file outside the worktree is
read; no GitHub or network call is made.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from tools.magma_phase_synthesis import (
    DEFAULT_BASELINE,
    build_synthesis,
    main,
    render_markdown,
)

ROOT = Path(__file__).resolve().parents[2]
FIXED_NOW = dt.datetime(2026, 5, 23, 4, 30, tzinfo=dt.UTC)


def _anchor() -> dict:
    return json.loads((ROOT / DEFAULT_BASELINE).read_text(encoding="utf-8"))


def _counter_read_pass(baseline_path: str = "baseline.json") -> dict:
    return {
        "decision": "pass",
        "baseline_path": baseline_path,
        "anchor_path": None,
        "findings_count": 0,
        "static_findings": [],
        "delta_findings": [],
    }


def _counter_read_block(baseline_path: str = "baseline.json") -> dict:
    return {
        "decision": "block",
        "baseline_path": baseline_path,
        "anchor_path": "anchor.json",
        "findings_count": 2,
        "static_findings": [
            {
                "code": "release_boundary_flag_must_be_false",
                "path": "release_boundary.docker_latest_move",
                "severity": "block",
            }
        ],
        "delta_findings": [
            {
                "code": "forbidden_claim_removed_in_candidate",
                "path": "forbidden_claims",
                "severity": "block",
            }
        ],
    }


# --- build_synthesis structure --------------------------------------------

def test_synthesis_carries_baseline_metadata():
    synthesis = build_synthesis(_anchor(), generated_at_utc=FIXED_NOW)

    assert synthesis["sprint_id"] == "magma-100h-2026-05-23"
    assert synthesis["schema_version"].startswith(
        "waggledance.magma_100h_sprint_baseline"
    )
    assert synthesis["generated_at_utc"] == "2026-05-23T04:30:00Z"


def test_synthesis_preserves_release_boundary_block():
    synthesis = build_synthesis(_anchor(), generated_at_utc=FIXED_NOW)

    rb = synthesis["release_boundary"]
    assert rb["docker_latest_move"] is False
    assert rb["external_effect_authority_change"] is False
    assert rb["stable_release_claim"] is False
    assert rb["tag_creation"] is False


def test_synthesis_preserves_must_win_axis_labels_qualified():
    synthesis = build_synthesis(_anchor(), generated_at_utc=FIXED_NOW)

    a3 = synthesis["must_win_axes"]["a3_counterfactual"]
    a4 = synthesis["must_win_axes"]["a4_solver_growth"]
    # The committed baseline keeps both axis labels qualified; the
    # synthesis must not silently upgrade them.
    assert "MEASURED_LOCAL" in str(a3["claim_label"])
    assert "MEASURED_LOCAL" in str(a4["claim_label"])
    assert a3["delta_proven"] is True
    assert a4["growth_proven"] is True


def test_synthesis_carries_competitor_pilot_non_consensus():
    synthesis = build_synthesis(_anchor(), generated_at_utc=FIXED_NOW)

    cp = synthesis["competitor_pilot"]
    assert cp["consensus_grade"] is False
    assert cp["rival_local_check_consensus_grade"] is False
    assert "JamJet" in cp["rivals"]


def test_synthesis_counter_read_absent_by_default():
    synthesis = build_synthesis(_anchor(), generated_at_utc=FIXED_NOW)
    assert synthesis["counter_read"] == {"present": False}


def test_synthesis_counter_read_present_when_supplied():
    synthesis = build_synthesis(
        _anchor(),
        counter_read=_counter_read_pass(),
        generated_at_utc=FIXED_NOW,
    )
    cr = synthesis["counter_read"]
    assert cr["present"] is True
    assert cr["decision"] == "pass"
    assert cr["findings_count"] == 0


def test_synthesis_counter_read_block_surfaces_codes():
    synthesis = build_synthesis(
        _anchor(),
        counter_read=_counter_read_block(),
        generated_at_utc=FIXED_NOW,
    )
    cr = synthesis["counter_read"]
    assert cr["decision"] == "block"
    assert cr["findings_count"] == 2
    assert "release_boundary_flag_must_be_false" in cr["static_finding_codes"]
    assert (
        "forbidden_claim_removed_in_candidate" in cr["delta_finding_codes"]
    )


# --- markdown renderer ----------------------------------------------------

def test_markdown_renders_committed_baseline_without_error():
    synthesis = build_synthesis(_anchor(), generated_at_utc=FIXED_NOW)
    md = render_markdown(synthesis)
    assert md.startswith("# MAGMA 100h Sprint Phase Synthesis")
    # Each top-level section is present:
    for section in (
        "## Blockers",
        "## Release-boundary status",
        "## A3 counterfactual axis",
        "## A4 solver-growth axis",
        "## Ceded axes",
        "## Competitor pilot",
        "## Adversarial corpus",
        "## Receipt adoption",
        "## Governance throughput",
        "## Next work packages",
        "## Counter-read audit",
    ):
        assert section in md, section


def test_markdown_marks_counter_read_present_when_supplied():
    synthesis = build_synthesis(
        _anchor(),
        counter_read=_counter_read_pass(),
        generated_at_utc=FIXED_NOW,
    )
    md = render_markdown(synthesis)
    assert "**Decision:** pass" in md
    assert "**Findings count:** 0" in md


def test_markdown_marks_counter_read_block_when_supplied():
    synthesis = build_synthesis(
        _anchor(),
        counter_read=_counter_read_block(),
        generated_at_utc=FIXED_NOW,
    )
    md = render_markdown(synthesis)
    assert "**Decision:** block" in md
    assert "release_boundary_flag_must_be_false" in md
    assert "forbidden_claim_removed_in_candidate" in md


def test_markdown_calls_out_no_counter_read_when_absent():
    synthesis = build_synthesis(_anchor(), generated_at_utc=FIXED_NOW)
    md = render_markdown(synthesis)
    assert "counter-read report not provided" in md


# --- main() end-to-end ---------------------------------------------------

def test_main_exits_zero_and_emits_markdown(capsys):
    rc = main(["--baseline", str(ROOT / DEFAULT_BASELINE)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("# MAGMA 100h Sprint Phase Synthesis")


def test_main_emits_json_when_requested(capsys):
    rc = main(
        [
            "--baseline",
            str(ROOT / DEFAULT_BASELINE),
            "--format",
            "json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sprint_id"] == "magma-100h-2026-05-23"
    assert payload["release_boundary"]["stable_release_claim"] is False


def test_main_includes_counter_read_when_supplied(tmp_path, capsys):
    cr_path = tmp_path / "cr.json"
    cr_path.write_text(json.dumps(_counter_read_pass()), encoding="utf-8")
    rc = main(
        [
            "--baseline",
            str(ROOT / DEFAULT_BASELINE),
            "--counter-read",
            str(cr_path),
            "--format",
            "json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counter_read"]["present"] is True
    assert payload["counter_read"]["decision"] == "pass"


def test_main_exits_two_on_missing_baseline(tmp_path, capsys):
    rc = main(["--baseline", str(tmp_path / "missing.json")])
    assert rc == 2
    assert "baseline not found" in capsys.readouterr().err


def test_main_writes_to_output_path(tmp_path):
    out_path = tmp_path / "synth.md"
    rc = main(
        [
            "--baseline",
            str(ROOT / DEFAULT_BASELINE),
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    body = out_path.read_text(encoding="utf-8")
    assert body.startswith("# MAGMA 100h Sprint Phase Synthesis")
