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

Provenance, not destination: a query the keyword classifier resolves counts as
NON-authoritative even if it happens to land on the priority-0 layer.

Privacy: the raw query text is NEVER emitted. Only the corpus id, route labels,
the fixed ``reason`` enum, the first-hop class, and the capsule-side
``decision_id`` are recorded. The ``raw_query_not_emitted`` invariant is DERIVED
by re-scanning the serialized report for any corpus query text or non-trivial
query-derived token (fail-closed), never hardcoded True.

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

from core.domain_capsule import DomainCapsule  # noqa: E402
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
AUTHORITATIVE_REASONS: frozenset[str] = frozenset({
    "capsule_decision_match",
    "capsule_decision_fallback",
    "capsule_priority_fallback",
})
# Safe-to-emit record keys (allowlist). No key carries raw query text.
_RECORD_KEYS: frozenset[str] = frozenset({
    "id", "expected", "predicted", "reason", "first_hop_class", "decision_id",
})


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
) -> bool:
    """Re-scan the emitted data; fail-closed privacy invariant (never hardcoded).

    Returns True iff no raw corpus query text survives anywhere in the serialized
    emitted structures. Whole-query strings and non-trivial raw-query tokens are
    scanned (length-guarded to avoid matching incidental route-label words), so
    any meaningful query survival flips it False.
    """
    scan_body = json.dumps(
        {"first_hop_records": records, "per_route_summary": per_route_summary},
        ensure_ascii=False,
    ).lower()
    for raw in queries:
        q = re.sub(r"\s+", " ", str(raw)).strip().lower()
        if len(q) >= 8 and q in scan_body:
            return False
        for token in _raw_query_privacy_needles(q):
            if token in scan_body:
                return False
    return True


def _raw_query_privacy_needles(query: str) -> frozenset[str]:
    """Return raw-query fragments specific enough to treat as privacy leaks."""
    needles: set[str] = set()
    for match in re.finditer(r"[a-z0-9][a-z0-9_+\-*/.:]*", query):
        token = match.group(0).strip("._:+-*/")
        if not token:
            continue
        digit_runs = re.findall(r"\d{4,}", token)
        needles.update(digit_runs)
        has_digit = any(ch.isdigit() for ch in token)
        has_operator = any(ch in "+-*/_:." for ch in token)
        if len(token) >= 12 or (has_digit and len(token) >= 4) or (
            has_operator and len(token) >= 4
        ):
            needles.add(token)
    return frozenset(needles)


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
        records, per_route_summary, raw_queries
    )
    ok = bool(measurement_available and raw_query_not_emitted)

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_iso(),
        "ok": ok,
        "profile": profile,
        "corpus_size": total,
        "hot_cache_count": cached,
        "routable_size": routable,
        "authoritative_first_hop_count": authoritative,
        "heuristic_first_hop_count": heuristic,
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
