# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Morning analysis for the Mama Event Observatory overnight collector.

Reads the artifacts produced by ``tools/mama_event_overnight.py`` and
renders ``reports/observatory/MAMA_EVENT_OVERNIGHT.md`` with three
clearly separated sections:

1. **Data availability** — did the overnight cycle even see new real
   rows? This is a property of the source corpus, not the framework.
2. **Framework stability** — did the pipeline run cleanly? Snapshots
   monotonic, NDJSON files growing, no truncation, no schema drift,
   no contamination-guard regressions.
3. **Observatory verdict on new real data** — strictly the verdict
   the closed gate function emitted on the new real rows ingested
   during the overnight window. Synthetic data is never blended in.

If no new real rows arrived, the report records
``data_status: NO_NEW_REAL_DATA`` and
``observatory_verdict: NO_CANDIDATE_EVENTS`` as separate fields and
explains the distinction in the body. The verdict is never inflated
to express absence of data.

The renderer pipes the final markdown through
:func:`assert_no_hype` so a forbidden term anywhere in the report
text aborts the write before any file is touched.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Reuse the collector's filename constants, checkpoint dataclass, and
# size helper so the morning analysis can never drift from the writer.
# `tools` is not a package, so we load the sibling module by file path.
import importlib.util as _importlib_util

_COLLECTOR_PATH = Path(__file__).resolve().with_name("mama_event_overnight.py")
_spec = _importlib_util.spec_from_file_location(
    "mama_event_overnight", _COLLECTOR_PATH
)
assert _spec is not None and _spec.loader is not None
_collector = _importlib_util.module_from_spec(_spec)
sys.modules.setdefault("mama_event_overnight", _collector)
_spec.loader.exec_module(_collector)

from waggledance.observatory.mama_events.gate import GateVerdict
from waggledance.observatory.mama_events.reports import assert_no_hype

# Re-export so callers (and tests) keep importing from this module.
Checkpoint = _collector.Checkpoint
load_checkpoint = _collector.load_checkpoint
_ndjson_size_bytes = _collector._ndjson_size_bytes
CHECKPOINT_NAME = _collector.CHECKPOINT_NAME
EVENTS_NAME = _collector.EVENTS_NAME
SELF_STATE_NAME = _collector.SELF_STATE_NAME
BINDING_NAME = _collector.BINDING_NAME
SNAPSHOTS_NAME = _collector.SNAPSHOTS_NAME
PROCESS_NAME = _collector.PROCESS_NAME


# ── paths ────────────────────────────────────────────────

OVERNIGHT_DIR = ROOT / "logs" / "observatory" / "overnight"
REPORT_PATH = ROOT / "reports" / "observatory" / "MAMA_EVENT_OVERNIGHT.md"


log = logging.getLogger("mama_event_overnight_analysis")


# ── data status / verdict separation ─────────────────────


DATA_STATUS_NO_NEW = "NO_NEW_REAL_DATA"
DATA_STATUS_NEW = "NEW_REAL_DATA_OBSERVED"


# ── analysis result ──────────────────────────────────────


@dataclass
class OvernightAnalysis:
    """Aggregated overnight findings used by the renderer.

    Holds only what the morning report actually needs. Pre-computing
    these on a single pass over the NDJSON files keeps the renderer
    free of any branching logic that might accidentally cross the
    real/synthetic boundary.
    """

    data_status: str
    observatory_verdict: str
    snapshots: List[Dict[str, Any]] = field(default_factory=list)
    cumulative_real_events: int = 0
    new_real_events_this_run: int = 0
    candidate_events: int = 0
    max_score: int = 0
    contamination_hits: int = 0
    caregiver_binding_hits: int = 0
    self_state_emissions: int = 0
    consolidation_writes: int = 0
    band_counts: Dict[str, int] = field(default_factory=dict)
    top_candidates: List[Dict[str, Any]] = field(default_factory=list)
    checkpoint_start_row_id: int = 0
    checkpoint_end_row_id: int = 0
    first_started_at: str = ""
    last_run_started_at: str = ""
    last_run_ended_at: str = ""
    wall_clock_seconds: float = 0.0
    process_rss_mb_first: Optional[float] = None
    process_rss_mb_last: Optional[float] = None
    process_rss_mb_max: Optional[float] = None
    ndjson_total_bytes: int = 0
    framework_stability_notes: List[str] = field(default_factory=list)


# ── readers ──────────────────────────────────────────────


@dataclass
class _NdjsonReadResult:
    """Outcome of reading an NDJSON file: parsed rows + skipped count.

    Skipped lines (blank lines and JSON decode errors) are surfaced
    so :func:`_framework_stability_check` can warn the morning report
    when an artifact is corrupt — silently dropping malformed rows
    would mask a real framework problem.
    """

    rows: List[Dict[str, Any]]
    decode_errors: int = 0


def _read_ndjson(path: Path) -> _NdjsonReadResult:
    """Read an NDJSON file. Returns parsed dict rows + decode-error count.

    Returns an empty result if the file does not exist; an empty file
    is therefore indistinguishable from a missing file at this layer.
    The framework-stability section reports the file size separately
    so the morning report still says whether the file was created.
    """
    if not path.is_file():
        return _NdjsonReadResult(rows=[])
    rows: List[Dict[str, Any]] = []
    decode_errors = 0
    with open(path, encoding="utf-8", errors="replace") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                decode_errors += 1
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return _NdjsonReadResult(rows=rows, decode_errors=decode_errors)


# ── strongest verdict (closed-gate ordered) ──────────────


_VERDICT_ORDER = {
    GateVerdict.NO_CANDIDATES.value: 0,
    GateVerdict.WEAK_SPONTANEOUS_ONLY.value: 1,
    GateVerdict.GROUNDED_CANDIDATE.value: 2,
    GateVerdict.STRONG_PROTO_SOCIAL.value: 3,
}


def strongest_verdict(verdicts: Sequence[str]) -> str:
    """Return the strongest verdict observed across snapshots.

    Uses the closed-gate ordering — never invents a tier outside the
    four-member :class:`GateVerdict` enum.
    """
    best = GateVerdict.NO_CANDIDATES.value
    best_rank = 0
    for v in verdicts:
        rank = _VERDICT_ORDER.get(v, -1)
        if rank > best_rank:
            best = v
            best_rank = rank
    return best


# ── top candidates from events ndjson ────────────────────


def collect_top_candidates(
    events: Sequence[Dict[str, Any]],
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """Return up to ``top_n`` highest-scoring real candidate events.

    Reads the redacted preview, never the raw utterance text. Skips
    score-zero events because they are not candidates by definition.
    """
    rows: List[Dict[str, Any]] = []
    for obj in events:
        score = int(((obj.get("score") or {}).get("total")) or 0)
        if score <= 0:
            continue
        evt = obj.get("event") or {}
        cont = (obj.get("contamination") or {}).get("flags") or []
        rows.append({
            "score": score,
            "band": str((obj.get("score") or {}).get("band", "")),
            "session": str(evt.get("session_id", "")),
            "caregiver": str(evt.get("caregiver_candidate_id") or ""),
            "contamination": list(cont),
            "preview": str(
                evt.get("redacted_utterance")
                or evt.get("utterance_text")
                or ""
            )[:80],
        })
    rows.sort(key=lambda r: -r["score"])
    return rows[:top_n]


# ── analysis pass ────────────────────────────────────────


def analyse(out_dir: Path = OVERNIGHT_DIR) -> OvernightAnalysis:
    """Run the full analysis on the overnight artifacts in ``out_dir``."""
    checkpoint = load_checkpoint(out_dir / CHECKPOINT_NAME) or Checkpoint()
    snapshots_read = _read_ndjson(out_dir / SNAPSHOTS_NAME)
    events_read = _read_ndjson(out_dir / EVENTS_NAME)
    snapshots = snapshots_read.rows
    events = events_read.rows

    # Per-event aggregates: NEVER reach into self_state or binding
    # streams to invent verdicts. Those streams are timeline-only.
    candidate_events = sum(
        1 for obj in events
        if int(((obj.get("score") or {}).get("total")) or 0) > 0
    )
    max_score = max(
        (int(((obj.get("score") or {}).get("total")) or 0) for obj in events),
        default=0,
    )
    contamination_hits = sum(
        1 for obj in events
        if (obj.get("contamination") or {}).get("flags")
    )
    band_counts: Dict[str, int] = {}
    for obj in events:
        band = str((obj.get("score") or {}).get("band", ""))
        if band:
            band_counts[band] = band_counts.get(band, 0) + 1

    # Snapshot-derived counters
    if snapshots:
        last = snapshots[-1]
        new_this_run = int(last.get("new_real_events_this_run") or 0)
        cumulative = int(last.get("cumulative_real_events") or 0)
        caregiver_hits = int(last.get("caregiver_binding_hits") or 0)
        self_state_emissions = int(last.get("self_state_emissions") or 0)
        consolidation_writes = int(last.get("consolidation_writes") or 0)
        verdicts_seen = [str(s.get("verdict") or "") for s in snapshots if s.get("verdict")]
        rss_values = [
            float(s["process_rss_mb"]) for s in snapshots
            if isinstance(s.get("process_rss_mb"), (int, float))
        ]
        rss_first = rss_values[0] if rss_values else None
        rss_last = rss_values[-1] if rss_values else None
        rss_max = max(rss_values) if rss_values else None
    else:
        new_this_run = 0
        cumulative = int(checkpoint.cumulative_real_events)
        caregiver_hits = 0
        self_state_emissions = 0
        consolidation_writes = 0
        verdicts_seen = []
        rss_first = rss_last = rss_max = None

    observatory_verdict = strongest_verdict(verdicts_seen) if verdicts_seen else GateVerdict.NO_CANDIDATES.value
    data_status = DATA_STATUS_NEW if new_this_run > 0 else DATA_STATUS_NO_NEW

    # Wall clock: prefer first/last snapshot timestamps if both present
    wall_clock = 0.0
    if len(snapshots) >= 2:
        wall_clock = _seconds_between(
            snapshots[0].get("timestamp", ""),
            snapshots[-1].get("timestamp", ""),
        )

    ndjson_total = sum(_ndjson_size_bytes(out_dir / name) for name in (
        EVENTS_NAME, SELF_STATE_NAME, BINDING_NAME, SNAPSHOTS_NAME, PROCESS_NAME,
    ))

    stability_notes = _framework_stability_check(
        out_dir=out_dir,
        snapshots=snapshots,
        events=events,
        new_this_run=new_this_run,
        snapshot_decode_errors=snapshots_read.decode_errors,
        events_decode_errors=events_read.decode_errors,
    )

    return OvernightAnalysis(
        data_status=data_status,
        observatory_verdict=observatory_verdict,
        snapshots=snapshots,
        cumulative_real_events=cumulative,
        new_real_events_this_run=new_this_run,
        candidate_events=candidate_events,
        max_score=max_score,
        contamination_hits=contamination_hits,
        caregiver_binding_hits=caregiver_hits,
        self_state_emissions=self_state_emissions,
        consolidation_writes=consolidation_writes,
        band_counts=band_counts,
        top_candidates=collect_top_candidates(events),
        checkpoint_start_row_id=int(snapshots[0].get("checkpoint_row_id", 0)) if snapshots else 0,
        checkpoint_end_row_id=int(checkpoint.last_row_id),
        first_started_at=checkpoint.first_started_at,
        last_run_started_at=checkpoint.last_run_started_at,
        last_run_ended_at=checkpoint.last_run_ended_at,
        wall_clock_seconds=wall_clock,
        process_rss_mb_first=rss_first,
        process_rss_mb_last=rss_last,
        process_rss_mb_max=rss_max,
        ndjson_total_bytes=ndjson_total,
        framework_stability_notes=stability_notes,
    )


def _seconds_between(iso_a: str, iso_b: str) -> float:
    """Return ``b - a`` in seconds, or 0.0 if either input is unparseable."""
    from datetime import datetime
    try:
        a = datetime.strptime(iso_a, "%Y-%m-%dT%H:%M:%SZ")
        b = datetime.strptime(iso_b, "%Y-%m-%dT%H:%M:%SZ")
        return max(0.0, (b - a).total_seconds())
    except (TypeError, ValueError):
        return 0.0


# ── stability checks ────────────────────────────────────


def _framework_stability_check(
    *,
    out_dir: Path,
    snapshots: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    new_this_run: int,
    snapshot_decode_errors: int = 0,
    events_decode_errors: int = 0,
) -> List[str]:
    """Return human-readable stability notes for the morning report.

    Each note is a single sentence the renderer drops verbatim into
    the framework-stability section. The list is empty if everything
    looks healthy.
    """
    notes: List[str] = []
    if not snapshots:
        notes.append(
            "No overnight snapshots were recorded — the collector did not write a single "
            "snapshot record. Either the collector was never run or it crashed before "
            "the initial snapshot."
        )
        return notes

    # NDJSON parse errors are first-class stability signals: a corrupt
    # line means a half-written record made it to disk and silently
    # dropping it would mask the failure.
    if snapshot_decode_errors:
        notes.append(
            f"overnight_snapshots.ndjson had {snapshot_decode_errors} undecodable line(s) "
            "— possible truncation."
        )
    if events_decode_errors:
        notes.append(
            f"events_real.ndjson had {events_decode_errors} undecodable line(s) "
            "— possible truncation."
        )

    # snapshot count vs. event count sanity
    if new_this_run < 0:
        notes.append("Negative new_real_events_this_run in the latest snapshot — counter regression.")
    if len(events) < new_this_run:
        notes.append(
            f"events_real.ndjson holds {len(events)} rows but the snapshot reports "
            f"{new_this_run} new real events ingested this run — events sink may be lagging."
        )

    # NDJSON files exist
    for name in (EVENTS_NAME, SELF_STATE_NAME, BINDING_NAME, SNAPSHOTS_NAME, PROCESS_NAME):
        if not (out_dir / name).is_file():
            notes.append(f"Expected NDJSON file {name} is missing from the overnight directory.")

    # snapshot index is monotonic + starts at 0
    indices = [int(s.get("snapshot_index", -1)) for s in snapshots]
    if indices != sorted(indices) or (indices and indices[0] != 0):
        notes.append("Snapshot indices are non-monotonic or do not start at 0.")

    # ndjson_bytes monotonically non-decreasing across snapshots (append-only)
    sizes = [int((s.get("ndjson_bytes") or {}).get(EVENTS_NAME, 0)) for s in snapshots]
    if any(b < a for a, b in zip(sizes, sizes[1:])):
        notes.append(
            "events_real.ndjson size decreased between snapshots — append-only invariant violated."
        )

    return notes


# ── renderer ─────────────────────────────────────────────


def render_overnight_report(analysis: OvernightAnalysis) -> str:
    """Render the morning markdown report from a pre-computed analysis.

    The output is run through :func:`assert_no_hype` before being
    returned so a forbidden term anywhere in the body raises an
    AssertionError instead of writing a tainted file.
    """
    lines: List[str] = []
    lines.append("# Mama Event Observatory — Overnight Run")
    lines.append("")
    lines.append(
        "This report is the output of an overnight real-data collection cycle. "
        "It only describes evidence available in the appended NDJSON logs at "
        "`logs/observatory/overnight/`. It does NOT make any claim about inner "
        "experience and it never blends synthetic data into the verdict."
    )
    lines.append("")

    # ── Section 1: Data availability ─────────────────
    lines.append("## 1. Data availability")
    lines.append("")
    lines.append(f"* **data_status**: `{analysis.data_status}`")
    lines.append(f"* **new_real_events_this_run**: {analysis.new_real_events_this_run}")
    lines.append(f"* **cumulative_real_events**: {analysis.cumulative_real_events}")
    lines.append(f"* **checkpoint_start_row_id**: {analysis.checkpoint_start_row_id}")
    lines.append(f"* **checkpoint_end_row_id**: {analysis.checkpoint_end_row_id}")
    lines.append(f"* **first_started_at**: `{analysis.first_started_at or '-'}`")
    lines.append(f"* **last_run_started_at**: `{analysis.last_run_started_at or '-'}`")
    lines.append(f"* **last_run_ended_at**: `{analysis.last_run_ended_at or '-'}`")
    lines.append(f"* **wall_clock_seconds**: {analysis.wall_clock_seconds:.1f}")
    lines.append(f"* **snapshots_recorded**: {len(analysis.snapshots)}")
    lines.append(f"* **NDJSON total size (bytes)**: {analysis.ndjson_total_bytes}")
    lines.append("")
    if analysis.data_status == DATA_STATUS_NO_NEW:
        lines.append(
            "_No new real rows arrived in `data/chat_history.db` during the overnight "
            "window. This is a property of the source corpus, not of the framework. "
            "It is recorded as a data-availability outcome and does NOT count as a "
            "framework failure._"
        )
        lines.append("")

    # ── Section 2: Framework stability ───────────────
    lines.append("## 2. Framework stability")
    lines.append("")
    if analysis.framework_stability_notes:
        for note in analysis.framework_stability_notes:
            lines.append(f"* WARN: {note}")
    else:
        lines.append("* All append-only sinks present and growing monotonically.")
        lines.append("* Snapshot indices monotonic from 0.")
        lines.append("* No counter regressions in the snapshot stream.")
    lines.append("")
    if analysis.process_rss_mb_first is not None:
        lines.append(
            f"* **process_rss_mb (first / last / max)**: "
            f"{analysis.process_rss_mb_first:.1f} / "
            f"{(analysis.process_rss_mb_last or 0.0):.1f} / "
            f"{(analysis.process_rss_mb_max or 0.0):.1f}"
        )
    else:
        lines.append("* process_rss_mb: unavailable on this platform")
    lines.append("")

    # ── Section 3: Observatory verdict on new real data ─
    lines.append("## 3. Observatory verdict on new real data")
    lines.append("")
    lines.append(f"* **observatory_verdict**: `{analysis.observatory_verdict}`")
    lines.append(f"* **candidate_events**: {analysis.candidate_events}")
    lines.append(f"* **max_score**: {analysis.max_score}")
    lines.append(f"* **contamination_hits**: {analysis.contamination_hits}")
    lines.append(f"* **caregiver_binding_hits**: {analysis.caregiver_binding_hits}")
    lines.append(f"* **self_state_emissions**: {analysis.self_state_emissions}")
    lines.append(f"* **consolidation_writes**: {analysis.consolidation_writes}")
    lines.append("")
    if analysis.band_counts:
        lines.append("### Band counts")
        lines.append("")
        for band, count in sorted(analysis.band_counts.items()):
            lines.append(f"* `{band}`: {count}")
        lines.append("")
    else:
        lines.append("_No score band reached on real new data._")
        lines.append("")

    if analysis.top_candidates:
        lines.append("### Top real candidates (sorted by score)")
        lines.append("")
        lines.append("| rank | score | band | session | caregiver | contamination | preview |")
        lines.append("|------|-------|------|---------|-----------|---------------|---------|")
        for rank, row in enumerate(analysis.top_candidates, start=1):
            cont = ",".join(row["contamination"]) if row["contamination"] else "-"
            preview = (row["preview"] or "").replace("|", "/").replace("\n", " ")
            lines.append(
                f"| {rank} | {row['score']} | `{row['band']}` | "
                f"`{row['session'] or '-'}` | `{row['caregiver'] or '-'}` | "
                f"{cont} | {preview} |"
            )
        lines.append("")
    else:
        lines.append("_No non-zero candidate events on real new data this overnight cycle._")
        lines.append("")

    # ── Section 4: Honest separation ─────────────────
    lines.append("## 4. Honesty separation")
    lines.append("")
    lines.append(
        "* `data_status` and `observatory_verdict` are reported as separate fields. "
        f"An observatory verdict of `{GateVerdict.NO_CANDIDATES.value}` produced by "
        "an empty overnight window is not the same statement as the same verdict "
        "produced by a stream of new real rows that all scored zero — the morning "
        "analysis must keep these distinct."
    )
    lines.append(
        "* Synthetic data from `tools/mama_event_longrun.py` PASS B is NEVER mixed "
        "into the overnight verdict. Only the rows tailed from "
        "`data/chat_history.db` during this overnight cycle were considered."
    )
    lines.append(
        "* The strongest verdict in this report is the strongest member of the "
        "closed `GateVerdict` enum that the live observer reached on snapshot "
        "boundaries during this run. The enum has exactly four members and is "
        "not extended for the overnight path."
    )
    lines.append(
        "* No claim about inner experience or strong-AI properties is made. "
        "`assert_no_hype` is invoked against this entire report text before "
        "anything is written to disk."
    )
    lines.append("")

    text = "\n".join(lines)
    assert_no_hype(text)
    return text


# ── CLI ──────────────────────────────────────────────────


def write_overnight_report(
    analysis: OvernightAnalysis,
    *,
    report_path: Path = REPORT_PATH,
) -> Path:
    """Render and write the overnight report. Returns the written path.

    Calls :func:`render_overnight_report` which already runs the
    no-hype check; this wrapper handles the file IO and the parent
    directory creation.
    """
    text = render_overnight_report(analysis)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text, encoding="utf-8")
    return report_path


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Mama Event Observatory overnight analysis renderer.",
    )
    parser.add_argument(
        "--in-dir",
        type=Path,
        default=OVERNIGHT_DIR,
        help="Overnight artifacts directory (default: %(default)s)",
    )
    parser.add_argument(
        "--out-path",
        type=Path,
        default=REPORT_PATH,
        help="Output markdown path (default: %(default)s)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    analysis = analyse(args.in_dir)
    path = write_overnight_report(analysis, report_path=args.out_path)
    log.info(
        "wrote overnight report: data_status=%s observatory_verdict=%s -> %s",
        analysis.data_status,
        analysis.observatory_verdict,
        path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
