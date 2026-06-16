# SPDX-License-Identifier: BUSL-1.1
"""Routing misroute diagnostic — offline, deterministic capsule-tuning leads.

Follow-up to the routing-accuracy check (PR #1249), which found `apiary` routes
the canonical corpus to its labelled `expected_route` only ~47% of the time
(`retrieval`-labelled weakest at ~17%). This tool explains WHY each misroute
happens: for every query whose predicted layer != its `expected_route`, it
reports which router step resolved it (`reason`), the matched capsule decision
(`decision_id`) and the matched keywords — concrete, query-level capsule-tuning
leads. It also aggregates, per expected route, the most common wrong layer and
reason.

These are TUNING LEADS, not defects, and routing accuracy is a capsule↔corpus
alignment signal, not a correctness gate. Lower misroute = fewer queries falling
to the expensive `llm_reasoning` layer. The raw query text is never emitted
(only the corpus id + capsule-side matched keywords), so no query payload leaks.

Exact validation commands::

    python tools/run_routing_misroute_diagnostic.py --json
    python -m pytest tests/test_routing_misroute_diagnostic.py -q

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

REPORT_VERSION = "wd.routing_misroute_diagnostic.v1"
SAMPLE_PROFILE = "apiary"
EXPENSIVE_LAYER = "llm_reasoning"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_corpus(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data.get("queries", []))


def diagnose(profile: str, corpus: list[dict[str, Any]]) -> dict[str, Any]:
    capsule = DomainCapsule.load(profile)
    router = SmartRouterV2(capsule)

    total = 0
    correct = 0
    misroutes: list[dict[str, Any]] = []
    per_route_total: Counter = Counter()
    per_route_miss: Counter = Counter()
    misroute_to_expensive = 0

    for item in corpus:
        query = str(item.get("query", ""))
        expected = str(item.get("expected_route", ""))
        if not query or not expected:
            continue
        total += 1
        per_route_total[expected] += 1
        result = router.route(query)
        predicted = result.layer
        if predicted == expected:
            correct += 1
            continue
        per_route_miss[expected] += 1
        if predicted == EXPENSIVE_LAYER:
            misroute_to_expensive += 1
        misroutes.append({
            "id": str(item.get("id", "")),
            "expected": expected,
            "predicted": predicted,
            "reason": result.reason,
            "decision_id": result.decision_id,
            # capsule-side matched keywords only - never the raw query text
            "matched_keywords": list(result.matched_keywords or []),
        })

    # Per-expected-route tuning summary: how often each route is missed and the
    # most common wrong layer / resolving reason for its misroutes.
    per_route_summary: dict[str, Any] = {}
    for route in sorted(per_route_total):
        route_misroutes = [m for m in misroutes if m["expected"] == route]
        wrong_layers = Counter(m["predicted"] for m in route_misroutes)
        reasons = Counter(m["reason"] for m in route_misroutes)
        per_route_summary[route] = {
            "total": per_route_total[route],
            "misrouted": per_route_miss[route],
            "miss_rate": round(per_route_miss[route] / per_route_total[route], 4)
            if per_route_total[route] else 0.0,
            "top_wrong_layer": wrong_layers.most_common(1)[0][0] if wrong_layers else None,
            "top_reason": reasons.most_common(1)[0][0] if reasons else None,
        }

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_iso(),
        "ok": total > 0,
        "profile": profile,
        "corpus_size": total,
        "overall_accuracy": round(correct / total, 4) if total else 0.0,
        "misroute_count": len(misroutes),
        "misroute_to_expensive_layer_count": misroute_to_expensive,
        "per_route_summary": per_route_summary,
        "misroute_leads": misroutes,
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": True,
            "raw_query_not_emitted": True,
            "tuning_leads_not_defects": True,
            "no_superiority_claim": True,
            "forbidden_vocabulary_excluded": list(FORBIDDEN_VOCABULARY),
        },
    }


def render_summary(report: dict[str, Any]) -> str:
    lines = [
        "Routing misroute diagnostic",
        f"  profile={report['profile']} corpus={report['corpus_size']} "
        f"accuracy={report['overall_accuracy']} misroutes={report['misroute_count']} "
        f"to_expensive={report['misroute_to_expensive_layer_count']}",
        "  per expected route (misrouted/total, top wrong layer, top reason):",
    ]
    for route, s in report["per_route_summary"].items():
        lines.append(
            f"    {route:<14} {s['misrouted']}/{s['total']} miss_rate={s['miss_rate']} "
            f"-> {s['top_wrong_layer']} (reason {s['top_reason']})"
        )
    if report["misroute_leads"]:
        lines.append("  leads:")
        for m in report["misroute_leads"]:
            lines.append(
                f"    {m['id']:<24} expected={m['expected']} predicted={m['predicted']} "
                f"reason={m['reason']} matched={m['matched_keywords']}"
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
                        help="if set, write the JSON report to <out-dir>/routing_misroute_diagnostic.json")
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
        (out_dir / "routing_misroute_diagnostic.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
        )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
