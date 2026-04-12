# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""CLI for the Mama Event Observatory.

Runs the canonical event sequence through the baseline observer
and every ablation configuration, then writes:

* ``reports/observatory/MAMA_EVENT_FRAMEWORK.md``
* ``reports/observatory/MAMA_EVENT_BASELINE.md``
* ``reports/observatory/MAMA_EVENT_ABLATIONS.md``
* ``reports/observatory/MAMA_EVENT_CANDIDATES.md``
* ``reports/observatory/MAMA_EVENT_GATE.md``
* ``logs/observatory/mama_events.ndjson``
* ``logs/observatory/caregiver_binding.ndjson``
* ``logs/observatory/self_state_timeline.ndjson``
* ``logs/observatory/ablation_matrix.json``

Usage::

    python tools/mama_event_report.py

Every string written to a report file is checked for hype
language via :func:`assert_no_hype` before it hits disk.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Make sure we can import waggledance from a plain `python tools/...`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.observatory.mama_events.ablations import (
    AblationMatrix,
    canonical_event_sequence,
    run_ablation_matrix,
)
from waggledance.observatory.mama_events.observer import (
    FileNdjsonSink,
    MamaEventObserver,
)
from waggledance.observatory.mama_events.reports import (
    CandidateRow,
    assert_no_hype,
    collect_candidate_rows,
    render_ablations_report,
    render_baseline_report,
    render_candidates_report,
    render_framework_report,
    render_gate_report,
)


@dataclass(frozen=True, slots=True)
class ReportArtifacts:
    """Absolute paths to every file the CLI wrote."""

    framework: Path
    baseline: Path
    ablations: Path
    candidates: Path
    gate: Path
    events_ndjson: Path
    binding_ndjson: Path
    self_state_ndjson: Path
    ablation_json: Path


def write_reports(
    *,
    reports_dir: Path,
    logs_dir: Path,
) -> ReportArtifacts:
    """Run the observer + ablation matrix and write every artifact.

    Returns the list of files written for the caller (CLI or test).
    """

    reports_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 1. run the ablation matrix (baseline + 5 ablations)
    matrix = run_ablation_matrix()

    # 2. run a separate baseline pass that writes NDJSON to disk for
    # replay / auditing. We cannot reuse the matrix observers because
    # those used in-memory sinks.
    events_ndjson = logs_dir / "mama_events.ndjson"
    binding_ndjson = logs_dir / "caregiver_binding.ndjson"
    self_state_ndjson = logs_dir / "self_state_timeline.ndjson"
    # clean so we don't keep appending on re-runs
    for p in (events_ndjson, binding_ndjson, self_state_ndjson):
        if p.exists():
            p.unlink()

    events = canonical_event_sequence()
    event_sink = FileNdjsonSink(events_ndjson)
    binding_sink = FileNdjsonSink(binding_ndjson)
    self_state_sink = FileNdjsonSink(self_state_ndjson)
    obs = MamaEventObserver(
        sink=event_sink,
        binding_sink=binding_sink,
        self_state_sink=self_state_sink,
    )
    obs.note_stress(magnitude=1.0)
    audit_rows: list[CandidateRow] = []
    totals: list[int] = []
    bands: list[str] = []
    flags: list[tuple[str, ...]] = []
    for evt in events:
        result = obs.observe(evt)
        totals.append(result.breakdown.total)
        bands.append(result.breakdown.band.value)
        flags.append(tuple(sorted(f.value for f in result.contamination.flags)))
    obs.close()

    rows = collect_candidate_rows(
        events=events,
        totals=totals,
        bands=bands,
        contamination_flags=flags,
    )

    # 3. render reports
    framework_text = render_framework_report()
    baseline_text = render_baseline_report(matrix)
    ablations_text = render_ablations_report(matrix)
    candidates_text = render_candidates_report(rows)
    gate_text = render_gate_report(matrix)

    # 4. enforce honesty invariant before writing anything
    for text in (
        framework_text,
        baseline_text,
        ablations_text,
        candidates_text,
        gate_text,
    ):
        assert_no_hype(text)

    # 5. persist reports
    framework_path = reports_dir / "MAMA_EVENT_FRAMEWORK.md"
    baseline_path = reports_dir / "MAMA_EVENT_BASELINE.md"
    ablations_path = reports_dir / "MAMA_EVENT_ABLATIONS.md"
    candidates_path = reports_dir / "MAMA_EVENT_CANDIDATES.md"
    gate_path = reports_dir / "MAMA_EVENT_GATE.md"

    framework_path.write_text(framework_text, encoding="utf-8")
    baseline_path.write_text(baseline_text, encoding="utf-8")
    ablations_path.write_text(ablations_text, encoding="utf-8")
    candidates_path.write_text(candidates_text, encoding="utf-8")
    gate_path.write_text(gate_text, encoding="utf-8")

    # 6. serialise the ablation matrix as JSON for downstream tools
    ablation_json_path = logs_dir / "ablation_matrix.json"
    ablation_json_path.write_text(
        json.dumps(matrix.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return ReportArtifacts(
        framework=framework_path,
        baseline=baseline_path,
        ablations=ablations_path,
        candidates=candidates_path,
        gate=gate_path,
        events_ndjson=events_ndjson,
        binding_ndjson=binding_ndjson,
        self_state_ndjson=self_state_ndjson,
        ablation_json=ablation_json_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate Mama Event Observatory reports.",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=ROOT / "reports" / "observatory",
        help="Where markdown reports go.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=ROOT / "logs" / "observatory",
        help="Where NDJSON audit logs go.",
    )
    args = parser.parse_args(argv)

    arts = write_reports(
        reports_dir=args.reports_dir,
        logs_dir=args.logs_dir,
    )

    print("Wrote reports:")
    print(f"  framework : {arts.framework}")
    print(f"  baseline  : {arts.baseline}")
    print(f"  ablations : {arts.ablations}")
    print(f"  candidates: {arts.candidates}")
    print(f"  gate      : {arts.gate}")
    print("Wrote logs:")
    print(f"  events     : {arts.events_ndjson}")
    print(f"  binding    : {arts.binding_ndjson}")
    print(f"  self_state : {arts.self_state_ndjson}")
    print(f"  ablations  : {arts.ablation_json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
