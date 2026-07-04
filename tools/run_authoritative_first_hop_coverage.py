# SPDX-License-Identifier: BUSL-1.1
"""Authoritative first-hop route-order coverage — offline, deterministic.

Measures, over the canonical corpus, the fraction of routing decisions whose
FIRST hop is resolved by a capsule-AUTHORITATIVE source (the capsule's declared
``key_decision`` match or its ``LayerConfig.priority`` order) rather than by the
heuristic keyword classifier. This is a capsule<->corpus authority-alignment
signal (a tuning lead), NOT a correctness gate.

A first hop is AUTHORITATIVE iff ``result.reason`` is one of::

    capsule_decision_match, capsule_decision_fallback, capsule_priority_fallback

It is NON-authoritative (heuristic) iff the reason is ``keyword_classifier:*``.
``hot_cache_hit`` is EXCLUDED from both numerator and denominator (cache
provenance is not the capsule order). Thus::

    coverage = authoritative_first_hop_count / (corpus_size - hot_cache_count)

The denominator is always the full set of non-cached first hops. It must not
narrow itself to only solver-shaped or already-authoritative rows;
``authoritative_first_hop_gap_count`` is the complement that must be driven to
zero before this evidence can support a 1.0 route-order claim.

Provenance, not destination: a query the keyword classifier resolves counts as
NON-authoritative even if it happens to land on the priority-0 layer.

Privacy: the raw query text is NEVER emitted. Only the corpus id, route labels,
the fixed ``reason`` enum, the first-hop class, and capsule-declared
``decision_id`` values are allowed. The ``raw_query_not_emitted`` invariant is
DERIVED by allowlisting emitted fields and re-scanning the serialized report for
raw query text or high-signal query markers (fail-closed), never hardcoded True.

Measurement-vs-claim: this tool only MEASURES. The WD Image1 counter's
``satisfied``/``current_value`` stay gated on the existing
``authoritative_first_hop_safe`` flag; a measured 100% must never flip that
claim. Counter wiring (measurement-only, default-off) is a separate follow-up.

Exact validation commands::

    python tools/run_authoritative_first_hop_coverage.py --json
    python -m pytest tests/test_authoritative_first_hop_coverage.py -q

Engineering record; offline (no model/cloud calls); forbidden-vocabulary
guarded. No claim of superiority over any external system.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from core.domain_capsule import DomainCapsule, VALID_LAYERS  # noqa: E402
from core.smart_router_v2 import SmartRouterV2  # noqa: E402

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.authoritative_first_hop_coverage.v1"
SAMPLE_PROFILE = "apiary"
HOT_CACHE_REASON = "hot_cache_hit"
DENOMINATOR_SCOPE = "all_non_cached_first_hops"
AUTHORITATIVE_REASONS: frozenset[str] = frozenset({
    "capsule_decision_match",
    "capsule_decision_fallback",
    "capsule_priority_fallback",
})
# Safe-to-emit record keys (allowlist). No key carries raw query text.
_RECORD_KEYS: frozenset[str] = frozenset({
    "id", "expected", "predicted", "reason", "first_hop_class", "decision_id",
})
_FIRST_HOP_CLASSES: frozenset[str] = frozenset({
    "authoritative", "heuristic", "cached",
})
_PER_ROUTE_SUMMARY_KEYS: frozenset[str] = frozenset({
    "total", "routable", "authoritative", "heuristic", "cached",
    "authoritative_coverage",
})
_KEYWORD_CLASSIFIER_REASONS: frozenset[str] = frozenset({
    "keyword_classifier:math",
    "keyword_classifier:seasonal",
    "keyword_classifier:rule",
    "keyword_classifier:stat",
    "keyword_classifier:retrieval",
})
_SAFE_EMITTED_TOKENS: frozenset[str] = frozenset({
    "authoritative",
    "cached",
    "capsule_decision_fallback",
    "capsule_decision_match",
    "capsule_priority_fallback",
    "heuristic",
    "hot_cache_hit",
    "keyword_classifier",
    "llm_reasoning",
    "model_based",
    "retrieval",
    "rule_constraints",
    "statistical",
})


def _raw_query_leak_markers(raw: str) -> set[str]:
    """Return raw-query markers that must not appear in emitted structures.

    Whole queries catch verbatim leaks. Token markers catch sentinel/raw-derived
    leaks without treating ordinary short domain words as violations when they
    overlap capsule-side route labels or decision ids.
    """
    q = re.sub(r"\s+", " ", str(raw)).strip().lower()
    markers: set[str] = set()
    if len(q) >= 8:
        markers.add(q)

    for token in re.findall(r"[a-z0-9_]+", q):
        if token in _SAFE_EMITTED_TOKENS:
            continue
        has_digit = any(ch.isdigit() for ch in token)
        if (has_digit and len(token) >= 6) or len(token) >= 12:
            markers.add(token)

    for token in re.findall(r"[a-z0-9_+*.-]{5,}", q):
        if (
            token not in _SAFE_EMITTED_TOKENS
            and any(ch.isdigit() for ch in token)
            and any(ch in "+*.-" for ch in token)
        ):
            markers.add(token)
    return markers


def _capsule_decision_ids(capsule: Any) -> set[str]:
    """Capsule-declared decision ids are the only non-null ids we emit."""
    return {
        str(dec.get("id", ""))
        for dec in (getattr(capsule, "key_decisions", None) or [])
        if str(dec.get("id", ""))
    }


def _corpus_record_ids(corpus: Sequence[dict[str, Any]]) -> set[str]:
    """Corpus ids are the only non-empty record ids we emit."""
    return {
        str(item.get("id", ""))
        for item in corpus
        if str(item.get("id", "")) and item.get("query") and item.get("expected_route")
    }


def _is_safe_reason(reason: str) -> bool:
    return (
        reason == HOT_CACHE_REASON
        or reason in AUTHORITATIVE_REASONS
        or reason in _KEYWORD_CLASSIFIER_REASONS
    )


def _is_safe_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value
        and value not in (float("inf"), float("-inf"))
    )


def _emitted_values_are_allowlisted(
    records: list[dict[str, Any]],
    per_route_summary: dict[str, Any],
    allowed_decision_ids: set[str],
    allowed_record_ids: set[str],
) -> bool:
    """Fail closed if an emitted field carries a non-capsule/free-form value."""
    for record in records:
        if set(record) - _RECORD_KEYS:
            return False
        record_id = record.get("id")
        if record_id not in (None, "") and str(record_id) not in allowed_record_ids:
            return False
        if str(record.get("expected", "")) not in VALID_LAYERS:
            return False
        if str(record.get("predicted", "")) not in VALID_LAYERS:
            return False
        if str(record.get("first_hop_class", "")) not in _FIRST_HOP_CLASSES:
            return False
        if not _is_safe_reason(str(record.get("reason", ""))):
            return False
        decision_id = record.get("decision_id")
        if decision_id not in (None, "") and str(decision_id) not in allowed_decision_ids:
            return False

    for route, summary in per_route_summary.items():
        if str(route) not in VALID_LAYERS or not isinstance(summary, dict):
            return False
        if set(summary) != _PER_ROUTE_SUMMARY_KEYS:
            return False
        for key in ("total", "routable", "authoritative", "heuristic", "cached"):
            value = summary.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return False
        coverage = summary.get("authoritative_coverage")
        if coverage is not None and (
            not _is_safe_number(coverage) or not 0.0 <= float(coverage) <= 1.0
        ):
            return False
    return True


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_corpus(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data.get("queries", []))


def _classify_first_hop(reason: str) -> str:
    """Classify a first hop by PROVENANCE (reason), not destination layer."""
    if reason == HOT_CACHE_REASON:
        return "cached"
    if reason in AUTHORITATIVE_REASONS:
        return "authoritative"
    return "heuristic"


def _capsule_declares_authoritative_order(capsule: Any) -> bool:
    """True iff the capsule declares an authoritative order to measure against.

    Without declared key_decisions OR a priority layer order there is nothing
    authoritative to compare a first hop to, so coverage is not measurable
    (fail-closed -> measurement unavailable, NOT trivially 100%).
    """
    decisions = getattr(capsule, "key_decisions", None) or []
    try:
        ordered = capsule.get_layers_by_priority()
    except Exception:
        ordered = []
    return bool(decisions) or bool(ordered)


def _derive_raw_query_not_emitted(
    records: list[dict[str, Any]],
    per_route_summary: dict[str, Any],
    queries: Sequence[str],
    allowed_decision_ids: set[str] | None = None,
    allowed_record_ids: set[str] | None = None,
) -> bool:
    """Re-scan the emitted data; fail-closed privacy invariant (never hardcoded).

    Returns True iff emitted fields match their allowlists and no raw corpus
    query text or sentinel-like raw-query token survives anywhere in the
    serialized emitted structures. Whole-query strings and high-signal token
    markers are scanned so verbatim or raw-derived query survival flips it
    False.
    """
    scan_body = json.dumps(
        {"first_hop_records": records, "per_route_summary": per_route_summary},
        ensure_ascii=False,
    ).lower()
    if not _emitted_values_are_allowlisted(
        records, per_route_summary, allowed_decision_ids or set(),
        allowed_record_ids or set()
    ):
        return False
    for raw in queries:
        for marker in _raw_query_leak_markers(raw):
            if marker in scan_body:
                return False
    return True


def diagnose(profile: str, corpus: list[dict[str, Any]]) -> dict[str, Any]:
    capsule = DomainCapsule.load(profile)
    router = SmartRouterV2(capsule)
    declares_order = _capsule_declares_authoritative_order(capsule)

    total = 0
    cached = 0
    authoritative = 0
    heuristic = 0
    records: list[dict[str, Any]] = []
    per_route_total: Counter = Counter()
    per_route_auth: Counter = Counter()
    per_route_heur: Counter = Counter()
    per_route_cached: Counter = Counter()
    raw_queries: list[str] = []

    for item in corpus:
        query = str(item.get("query", ""))
        expected = str(item.get("expected_route", ""))
        if not query or not expected:
            continue
        total += 1
        raw_queries.append(query)
        result = router.route(query)
        first_hop_class = _classify_first_hop(result.reason)
        per_route_total[expected] += 1
        if first_hop_class == "cached":
            cached += 1
            per_route_cached[expected] += 1
        elif first_hop_class == "authoritative":
            authoritative += 1
            per_route_auth[expected] += 1
        else:
            heuristic += 1
            per_route_heur[expected] += 1
        # Emit ONLY safe fields: id + route labels + fixed reason enum +
        # provenance class + capsule-side decision_id. Never the raw query.
        records.append({
            "id": str(item.get("id", "")),
            "expected": expected,
            "predicted": result.layer,
            "reason": result.reason,
            "first_hop_class": first_hop_class,
            "decision_id": result.decision_id,
        })

    routable = total - cached
    denominator_integrity_ok = (
        authoritative + heuristic == routable
        and routable == total - cached
    )
    measurement_available = bool(routable > 0 and declares_order)
    coverage = round(authoritative / routable, 4) if measurement_available else None

    per_route_summary: dict[str, Any] = {}
    for route in sorted(per_route_total):
        r_routable = per_route_total[route] - per_route_cached[route]
        per_route_summary[route] = {
            "total": per_route_total[route],
            "routable": r_routable,
            "authoritative": per_route_auth[route],
            "heuristic": per_route_heur[route],
            "cached": per_route_cached[route],
            "authoritative_coverage": round(per_route_auth[route] / r_routable, 4)
            if r_routable else None,
        }

    raw_query_not_emitted = _derive_raw_query_not_emitted(
        records, per_route_summary, raw_queries, _capsule_decision_ids(capsule),
        _corpus_record_ids(corpus)
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_iso(),
        "ok": measurement_available,
        "profile": profile,
        "corpus_size": total,
        "hot_cache_count": cached,
        "routable_size": routable,
        "first_hop_denominator_scope": DENOMINATOR_SCOPE,
        "first_hop_denominator_count": routable,
        "authoritative_first_hop_count": authoritative,
        "heuristic_first_hop_count": heuristic,
        "authoritative_first_hop_gap_count": heuristic,
        "coverage_measurement_available": measurement_available,
        "authoritative_first_hop_coverage": coverage,
        "measurement_basis": "v1_first_hop_authoritative_order",
        "per_route_summary": per_route_summary,
        "first_hop_records": records,
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": True,
            "raw_query_not_emitted": raw_query_not_emitted,
            "capsule_declares_authoritative_order": declares_order,
            "measurement_not_a_correctness_gate": True,
            "no_superiority_claim": True,
            "denominator_is_all_non_cached_first_hops": denominator_integrity_ok,
            "forbidden_vocabulary_excluded": list(FORBIDDEN_VOCABULARY),
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    cov = report["authoritative_first_hop_coverage"]
    lines = [
        "Authoritative first-hop route-order coverage",
        f"  profile={report['profile']} corpus={report['corpus_size']} "
        f"routable={report['routable_size']} cached={report['hot_cache_count']} "
        f"authoritative={report['authoritative_first_hop_count']} "
        f"heuristic={report['heuristic_first_hop_count']} "
        f"coverage={cov} (available={report['coverage_measurement_available']})",
        "  per expected route (authoritative/routable):",
    ]
    for route, s in report["per_route_summary"].items():
        lines.append(
            f"    {route:<16} auth={s['authoritative']}/{s['routable']} "
            f"coverage={s['authoritative_coverage']} "
            f"(heuristic={s['heuristic']} cached={s['cached']})"
        )
    return "\n".join(lines)


def assert_vocabulary_clean(text: str) -> None:
    hit = [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered summary: {hit}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=SAMPLE_PROFILE)
    parser.add_argument("--corpus", default="configs/benchmarks.yaml")
    parser.add_argument("--out-dir", default=None,
                        help="if set, write the JSON report to "
                             "<out-dir>/authoritative_first_hop_coverage.json")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    corpus = load_corpus(ROOT / args.corpus)
    report = diagnose(args.profile, corpus)

    summary = render_summary(report)
    assert_vocabulary_clean(summary)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(summary)

    if args.out_dir:
        out_dir = ROOT / args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "authoritative_first_hop_coverage.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
