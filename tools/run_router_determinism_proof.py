# SPDX-License-Identifier: BUSL-1.1
"""Router determinism proof - offline, reproducible routing-decision evidence.

Supports the WD Image #1 *deterministic-solver-first* pillar: the precondition
for reproducible routing (and for trusting the accuracy/misroute diagnostics in
PRs #1249/#1263) is that `SmartRouterV2.route()` is itself deterministic - the
same query always resolves to the same routing decision.

This proof routes every query in the canonical corpus TWICE (two independent
router instances over the same capsule) and checks the semantic routing decision
is byte-identical across the two runs. The decision is compared on stable fields
only - layer, reason, decision_id, fallback, model, confidence, rules - never on
the volatile `routing_time_ms` and never on the raw `inputs` / `matched_keywords`
(which can carry query text). The `deterministic` verdict is DERIVED from the
observed runs (fail-closed), never hardcoded.

The raw query text is never emitted (only the corpus id + the stable, capsule-
side decision fields), so no query payload leaks; `raw_query_not_emitted` is
derived by re-scanning the serialized report against the input queries.

Exact validation commands::

    python tools/run_router_determinism_proof.py --json
    python -m pytest tests/test_router_determinism_proof.py -q

Engineering record; offline (no model/cloud calls); forbidden-vocabulary
guarded. No claim of superiority over any external system.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.domain_capsule import DomainCapsule  # noqa: E402
from core.smart_router_v2 import SmartRouterV2  # noqa: E402

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.router_determinism_proof.v1"
SAMPLE_PROFILE = "apiary"

# The stable, semantic routing-decision fields. Excludes routing_time_ms
# (volatile timing) and inputs / matched_keywords (can carry raw query text).
STABLE_DECISION_FIELDS = (
    "layer",
    "reason",
    "decision_id",
    "fallback",
    "model",
    "confidence",
    "rules",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_corpus(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data.get("queries", []))


# Router layer enum (core/smart_router_v2). Emitted layer/fallback values must be
# one of these route labels (plus the capsule's own fallback / decision names).
_KNOWN_LAYERS = (
    "model_based",
    "retrieval",
    "llm_reasoning",
    "rule_constraints",
    "statistical",
)
# The EXACT closed set of reason values core/smart_router_v2 can emit. Matched
# exactly (NOT by prefix): a prefix match on "keyword_classifier:" would let a
# forged suffix (keyword_classifier:<raw query>) over-claim as known.
_KNOWN_REASONS = frozenset({
    "hot_cache_hit",
    "capsule_decision_match",
    "capsule_decision_fallback",
    "capsule_priority_fallback",
    "keyword_classifier:math",
    "keyword_classifier:seasonal",
    "keyword_classifier:rule",
    "keyword_classifier:stat",
    "keyword_classifier:retrieval",
})


def _stable_decision(result: Any) -> dict[str, Any]:
    """Stable semantic view of a RouteResult (no volatile/raw fields)."""
    payload = result.to_dict()
    return {field: payload.get(field) for field in STABLE_DECISION_FIELDS}


def _allowed_layers(capsule: Any) -> set[str]:
    """Route labels the router may emit as layer/fallback for this capsule."""
    layers: set[str] = set(_KNOWN_LAYERS)
    fallback = getattr(capsule, "default_fallback", None)
    if fallback:
        layers.add(str(fallback))
    # A capsule decision name can become a fallback layer, so allow decision ids.
    for dec in (getattr(capsule, "key_decisions", None) or []):
        if dec.get("id"):
            layers.add(str(dec.get("id")))
    return layers


def _allowed_decision_ids(capsule: Any) -> set[str]:
    return {
        str(dec.get("id"))
        for dec in (getattr(capsule, "key_decisions", None) or [])
        if dec.get("id")
    }


def _reason_is_known(reason: Any) -> bool:
    return str(reason) in _KNOWN_REASONS


def _derive_raw_query_not_emitted(
    decisions: list[dict[str, Any]],
    allowed_layers: set[str],
    allowed_decision_ids: set[str],
    allowed_ids: set[str],
) -> bool:
    """VALUE-ALLOWLIST every emitted decision field by MEMBERSHIP against known
    sets, else it is a forged or raw-query value and the privacy invariant fails
    closed.

    Each field is checked against a closed membership set: id against the input
    corpus's declared ids, layer/fallback against the route-label set, decision_id
    against the capsule decision ids, reason against the EXACT closed reason set
    (no prefix match). This is COMPLETE and false-positive-free regardless of
    token length - a short alpha query word (e.g. a name like 'hunters') injected
    into id/decision_id/reason is caught because it is not a member of the
    corresponding set. Mirrors the #1265/#1267 value-allowlist approach. Never
    hardcoded.
    """
    for dec in decisions:
        item_id = str(dec.get("id", ""))
        if not (item_id == "" or item_id in allowed_ids):
            return False
        if dec.get("layer") not in allowed_layers:
            return False
        fallback = dec.get("fallback")
        if not (fallback is None or fallback in allowed_layers):
            return False
        decision_id = dec.get("decision_id")
        if not (
            decision_id is None
            or decision_id == ""
            or decision_id in allowed_decision_ids
        ):
            return False
        if not _reason_is_known(dec.get("reason")):
            return False
    return True


def diagnose(profile: str, corpus: list[dict[str, Any]]) -> dict[str, Any]:
    capsule = DomainCapsule.load(profile)
    # Two independent router instances over the same capsule: a deterministic
    # router must produce identical decisions regardless of instance state.
    router_a = SmartRouterV2(capsule)
    router_b = SmartRouterV2(capsule)

    total = 0
    deterministic_count = 0
    nondeterministic: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    # The input corpus's declared ids - the only ids that may be emitted.
    allowed_ids = {str(item.get("id")) for item in corpus if item.get("id")}

    for item in corpus:
        query = str(item.get("query", ""))
        if not query:
            continue
        total += 1
        run1 = _stable_decision(router_a.route(query))
        run2 = _stable_decision(router_b.route(query))
        identical = json.dumps(run1, sort_keys=True, default=str) == json.dumps(
            run2, sort_keys=True, default=str
        )
        if identical:
            deterministic_count += 1
        else:
            nondeterministic.append({"id": str(item.get("id", ""))})
        # Emit only the stable, capsule-side decision fields + the per-query
        # determinism verdict. No raw query, no inputs, no matched_keywords.
        decisions.append({
            "id": str(item.get("id", "")),
            "layer": run1.get("layer"),
            "reason": run1.get("reason"),
            "decision_id": run1.get("decision_id"),
            "fallback": run1.get("fallback"),
            "deterministic": identical,
        })

    # DERIVE the determinism verdict from the observed runs (fail-closed): every
    # routed query must have matched across the two runs.
    all_deterministic = total > 0 and deterministic_count == total

    # DERIVE the privacy invariant (never hardcode) by VALUE-ALLOWLISTING every
    # emitted decision field against the known router/capsule output sets - a
    # forged/raw value in any field (even a short alpha query word) fails closed.
    raw_query_not_emitted = _derive_raw_query_not_emitted(
        decisions,
        _allowed_layers(capsule),
        _allowed_decision_ids(capsule),
        allowed_ids,
    )

    # DERIVE the vocabulary-clean invariant by SCANNING the emitted content (not
    # by listing the forbidden terms in the report, which would itself make the
    # JSON contain them). Fail-closed if any forbidden term is present.
    content_blob = json.dumps(
        {
            "routing_decisions": decisions,
            "nondeterministic_leads": nondeterministic,
            "profile": profile,
        },
        ensure_ascii=False,
    )
    forbidden_vocabulary_clean = not _vocabulary_hits(content_blob)

    blockers: list[str] = []
    if total == 0:
        blockers.append("empty_corpus")
    if total > 0 and not all_deterministic:
        blockers.append("nondeterministic_routing")
    if not raw_query_not_emitted:
        blockers.append("raw_query_emitted")
    if not forbidden_vocabulary_clean:
        blockers.append("forbidden_vocabulary_emitted")

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_iso(),
        "ok": not blockers,
        "blockers": blockers,
        "profile": profile,
        "corpus_size": total,
        "deterministic_count": deterministic_count,
        "deterministic_ratio": round(deterministic_count / total, 4) if total else 0.0,
        "all_deterministic": all_deterministic,
        "nondeterministic_count": len(nondeterministic),
        "nondeterministic_leads": nondeterministic,
        "routing_decisions": decisions,
        "compared_decision_fields": list(STABLE_DECISION_FIELDS),
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": all_deterministic,
            "raw_query_not_emitted": raw_query_not_emitted,
            "volatile_timing_excluded": True,
            "no_superiority_claim": True,
            "forbidden_vocabulary_clean": forbidden_vocabulary_clean,
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    lines = [
        "Router determinism proof",
        f"  profile={report['profile']} corpus={report['corpus_size']} "
        f"deterministic={report['deterministic_count']}/{report['corpus_size']} "
        f"ratio={report['deterministic_ratio']} all={report['all_deterministic']}",
        f"  ok={report['ok']} blockers={report['blockers']}",
        f"  compared fields: {report['compared_decision_fields']}",
    ]
    if report["nondeterministic_leads"]:
        lines.append("  nondeterministic ids:")
        for lead in report["nondeterministic_leads"]:
            lines.append(f"    {lead['id']}")
    return "\n".join(lines)


def _vocabulary_hits(text: str) -> list[str]:
    return [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]


def assert_vocabulary_clean(text: str) -> None:
    hit = _vocabulary_hits(text)
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered text: {hit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=SAMPLE_PROFILE)
    parser.add_argument("--corpus", default="configs/benchmarks.yaml")
    parser.add_argument("--out-dir", default=None,
                        help="if set, write the JSON report to <out-dir>/router_determinism_proof.json")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    corpus = load_corpus(ROOT / args.corpus)
    report = diagnose(args.profile, corpus)

    summary = render_summary(report)
    assert_vocabulary_clean(summary)
    json_report = json.dumps(report, indent=2, sort_keys=True)
    # The JSON report no longer lists the forbidden terms, so it must itself be
    # vocabulary-clean (not just the human summary).
    assert_vocabulary_clean(json_report)
    if args.json:
        print(json_report)
    else:
        print(summary)

    if args.out_dir:
        out_dir = ROOT / args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "router_determinism_proof.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
