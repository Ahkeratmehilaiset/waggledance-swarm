# SPDX-License-Identifier: BUSL-1.1
"""Receipted proof runner for the hex-mesh canary mirror (read-only).

Follow-up slice named in waggledance/core/hex_topology/canary_mirror.py:
the core gives pure comparison + aggregation; this tool turns a batch of
replayed production routing decisions into a digest-bound, privacy-safe
evidence artifact for storyboard panels 4->5 ("would the mesh have routed
real queries to the right cells").

Input: a JSONL file where each line is one production routing decision:
  {"query": str, "intent": str, "production_capability_id": str,
   "quality_path": str, "production_cell_id": str|null (optional)}
The input contract is CLOSED — unknown keys refuse (fail-closed) so a
forged record cannot smuggle extra fields into the evidence path. A
built-in --demo corpus (deterministic, covers all four classifications)
exists for smoke runs without captured traffic.

Read-only contract inherited from the core: nothing here routes traffic,
mutates topology, or grants authority; raw query text never appears in
the artifact (digest + length only). Deterministic via --now injection.
Optional --min-agreement-rate turns the proof into a local advisory
floor check (ok=false / exit 1 below the floor). All claim gates are
emitted false; this is local measured evidence, not consensus-grade.

Exit codes: 0 ok, 1 proof not ok (empty batch or below floor),
2 invalid arguments/input records, 3 input file missing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.hex_topology.canary_mirror import (  # noqa: E402
    CanaryMirrorError,
    build_canary_route_comparison,
    summarize_canary_mirror,
)

REPORT_VERSION = "wd.v12.hex_canary_mirror_proof.v0"
CLAIM_LABEL = "MEASURED_LOCAL_SHADOW_MIRROR"

REQUIRED_INPUT_KEYS = frozenset(
    {"query", "intent", "production_capability_id", "quality_path"}
)
OPTIONAL_INPUT_KEYS = frozenset({"production_cell_id"})
MAX_SOURCE_LABEL_CHARS = 200

# Hard rule shared with sibling tools: every artifact carries all claim
# gates as false — this is local measured evidence, never consensus-grade.
CLAIM_GATES: tuple[str, ...] = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
    "consensus_grade",
)

# Deterministic demo corpus: one record per classification.
DEMO_DECISIONS: tuple[dict[str, Any], ...] = (
    {
        "query": "calculate the heating formula",
        "intent": "math",
        "production_capability_id": "cap.math.formula",
        "quality_path": "silver",
        "production_cell_id": "math",
    },
    {
        "query": "calculate the heating formula",
        "intent": "math",
        "production_capability_id": "cap.math.formula",
        "quality_path": "silver",
        "production_cell_id": "general",
    },
    {
        "query": "hello there, how are you",
        "intent": "chat",
        "production_capability_id": "cap.chat.general",
        "quality_path": "bronze",
    },
    {
        "query": "kova pakkanen ja matala lämpötila, heating tarvitaan",
        "intent": "chat",
        "production_capability_id": "cap.chat.general",
        "quality_path": "bronze",
    },
)


def _decision(
    query: str,
    intent: str,
    capability: str,
    *,
    quality_path: str = "silver",
    production_cell_id: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "query": query,
        "intent": intent,
        "production_capability_id": capability,
        "quality_path": quality_path,
    }
    if production_cell_id is not None:
        record["production_cell_id"] = production_cell_id
    return record


# Deterministic representative corpus: 20 decisions across 3 intents
# (math / chat / code), exercising all four classifications, both mesh
# routing methods, and a realistic agreement mix (not the thin one-per-
# classification demo). Surfaced as the next coverage target by
# tools/build_hex_canary_coverage_summary.py.
REPRESENTATIVE_DECISIONS: tuple[dict[str, Any], ...] = (
    _decision("calculate the integral", "math", "cap.math.calc", production_cell_id="math"),
    _decision("solve the equation", "math", "cap.math.solve", production_cell_id="math"),
    _decision("compute the derivative", "math", "cap.math.calc", production_cell_id="general"),
    _decision("hello there how are you", "chat", "cap.chat.general"),
    _decision("what is your name", "chat", "cap.chat.general"),
    _decision("kova pakkanen ja matala lämpötila heating tarvitaan", "chat", "cap.chat.general"),
    _decision("the heating system is cold and frost", "chat", "cap.chat.general"),
    _decision("write a python function", "code", "cap.code.gen", production_cell_id="code"),
    _decision("refactor this class", "code", "cap.code.gen", production_cell_id="general"),
    _decision("debug the error", "code", "cap.code.gen"),
    _decision("translate this text", "chat", "cap.chat.general", production_cell_id="general"),
    _decision("summarize the article", "chat", "cap.chat.general", production_cell_id="general"),
    _decision("add two numbers", "math", "cap.math.calc", production_cell_id="math"),
    _decision("multiply matrices", "math", "cap.math.calc", production_cell_id="math"),
    _decision("explain the concept", "chat", "cap.chat.general"),
    _decision("generate test cases", "code", "cap.code.gen", production_cell_id="code"),
    _decision("lämpötila pakkanen heating cold", "math", "cap.math.calc", production_cell_id="math"),
    _decision("a simple greeting hello", "chat", "cap.chat.general"),
    _decision("compile the module", "code", "cap.code.gen", production_cell_id="code"),
    _decision("frost heating pakkanen lämpötila kylmä", "chat", "cap.chat.general"),
)

BUILTIN_CORPORA: dict[str, tuple[dict[str, Any], ...]] = {
    "demo": DEMO_DECISIONS,
    "representative": REPRESENTATIVE_DECISIONS,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a digest-bound, privacy-safe hex canary mirror proof "
            "artifact from replayed production routing decisions "
            "(read-only shadow comparison; local evidence only)."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input",
        type=Path,
        default=None,
        help="JSONL file of production routing decisions (closed schema).",
    )
    source.add_argument(
        "--demo",
        action="store_true",
        help="Use the built-in deterministic demo corpus instead of a file.",
    )
    source.add_argument(
        "--corpus",
        choices=sorted(BUILTIN_CORPORA),
        default=None,
        help=(
            "Use a named built-in corpus: 'demo' (4 cases, one per "
            "classification) or 'representative' (20 cases across 3 intents, "
            "both routing methods). --demo is equivalent to --corpus demo."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-8601 UTC override for generated_at_utc (deterministic runs).",
    )
    parser.add_argument(
        "--min-agreement-rate",
        type=float,
        default=None,
        help=(
            "Advisory floor in [0, 1]: ok=false / exit 1 when the mirrored "
            "agreement rate is below this value."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional path to also write the artifact JSON to.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit artifact JSON to stdout"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.min_agreement_rate is not None and not (
        args.min_agreement_rate == args.min_agreement_rate
        and 0.0 <= args.min_agreement_rate <= 1.0
    ):
        print("--min-agreement-rate must be within [0, 1]", file=sys.stderr)
        return 2

    if args.now is not None:
        now = _parse_utc(args.now)
        if now is None:
            print(f"--now is not a valid ISO-8601 instant: {args.now!r}", file=sys.stderr)
            return 2
    else:
        now = datetime.now(timezone.utc)

    if args.demo or args.corpus is not None:
        corpus_name = "demo" if args.demo else args.corpus
        decisions = [dict(d) for d in BUILTIN_CORPORA[corpus_name]]
        source_label = corpus_name
    else:
        if not args.input.exists():
            print(f"input file not found: {args.input}", file=sys.stderr)
            return 3
        try:
            decisions = _read_decisions(args.input)
        except ValueError as exc:
            print(f"invalid input: {exc}", file=sys.stderr)
            return 2
        source_label = str(args.input)[:MAX_SOURCE_LABEL_CHARS]

    try:
        artifact = build_canary_mirror_proof(
            decisions=decisions,
            source_label=source_label,
            now=now,
            min_agreement_rate=args.min_agreement_rate,
        )
    except (CanaryMirrorError, ValueError) as exc:
        print(f"canary mirror proof refused: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(artifact, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        _print_summary(artifact)
    return 0 if artifact["ok"] else 1


def build_canary_mirror_proof(
    *,
    decisions: Sequence[Mapping[str, Any]],
    source_label: str,
    now: datetime,
    min_agreement_rate: float | None = None,
) -> dict[str, Any]:
    """Mirror each decision through the hex mesh and aggregate the evidence.

    Raises CanaryMirrorError / ValueError on any malformed decision —
    a proof artifact must never silently exclude records.
    """
    comparisons = []
    for index, decision in enumerate(decisions):
        _validate_decision_keys(index, decision)
        comparisons.append(
            build_canary_route_comparison(
                query=decision["query"],
                intent=decision["intent"],
                production_capability_id=decision["production_capability_id"],
                quality_path=decision["quality_path"],
                production_cell_id=decision.get("production_cell_id"),
            )
        )

    mirror_report = summarize_canary_mirror(comparisons)
    agreement_rate = mirror_report["agreement_rate"]
    below_floor = (
        min_agreement_rate is not None and agreement_rate < min_agreement_rate
    )
    ok = mirror_report["sample_count"] > 0 and not below_floor

    artifact: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "claim_label": CLAIM_LABEL,
        "generated_at_utc": now.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "input_source": source_label[:MAX_SOURCE_LABEL_CHARS],
        "input_record_count": len(list(decisions)),
        "min_agreement_rate": min_agreement_rate,
        "below_agreement_floor": below_floor,
        "mirror_report": mirror_report,
        "ok": ok,
    }
    for gate in CLAIM_GATES:
        artifact[gate] = False
    return artifact


def _validate_decision_keys(index: int, decision: Any) -> None:
    if not isinstance(decision, Mapping):
        raise ValueError(f"decision[{index}] must be a JSON object")
    keys = set(decision.keys())
    missing = REQUIRED_INPUT_KEYS - keys
    if missing:
        raise ValueError(
            f"decision[{index}] missing required keys: {sorted(missing)}"
        )
    unknown = keys - REQUIRED_INPUT_KEYS - OPTIONAL_INPUT_KEYS
    if unknown:
        raise ValueError(
            f"decision[{index}] has unknown keys (closed contract): "
            f"{sorted(unknown)}"
        )


def _read_decisions(path: Path) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"line {line_number}: record must be a JSON object")
        decisions.append(record)
    return decisions


def _parse_utc(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _print_summary(artifact: Mapping[str, Any]) -> None:
    report = artifact["mirror_report"]
    print(
        f"hex canary mirror proof @ {artifact['generated_at_utc']} "
        f"({artifact['claim_label']}, read-only)"
    )
    print(
        f"  source: {artifact['input_source']} "
        f"({artifact['input_record_count']} decisions)"
    )
    print(
        f"  agreement: {report['agreement_count']}/{report['sample_count']} "
        f"= {report['agreement_rate']:.4f}"
    )
    for key, count in report["by_classification"].items():
        print(f"    {key}: {count}")
    if artifact["min_agreement_rate"] is not None:
        verdict = "BELOW FLOOR" if artifact["below_agreement_floor"] else "ok"
        print(f"  floor {artifact['min_agreement_rate']}: {verdict}")
    print(f"  ok: {artifact['ok']}")


if __name__ == "__main__":
    raise SystemExit(main())
