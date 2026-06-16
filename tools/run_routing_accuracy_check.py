#!/usr/bin/env python3
"""Routing accuracy + expensive-path-rate check — offline, deterministic.

Drives ``core.smart_router_v2.SmartRouterV2.route()`` over the canonical
``configs/benchmarks.yaml`` corpus and compares each predicted layer against the
query's labelled ``expected_route``. Reports routing accuracy (overall + per
expected route) and the **expensive-path rate**: the fraction of queries that
end at the ``llm_reasoning`` layer, i.e. the costly inference path. A high
expensive-path rate is an efficiency signal — those queries do not resolve on a
cheaper rule/model/statistical/retrieval layer.

This is an engineering record, fully offline (no model or cloud calls), with a
forbidden-vocabulary guard. Mismatches are capsule-tuning candidates, not
asserted defects; absolute accuracy reflects how well the chosen profile's
capsule aligns with the corpus labels.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from core.domain_capsule import DomainCapsule  # noqa: E402
from core.smart_router_v2 import SmartRouterV2  # noqa: E402

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

EXPENSIVE_LAYER = "llm_reasoning"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_corpus(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data.get("queries", []))


def evaluate(profile: str, corpus: list[dict[str, Any]]) -> dict[str, Any]:
    capsule = DomainCapsule.load(profile)
    router = SmartRouterV2(capsule)

    total = 0
    correct = 0
    expensive = 0
    per_expected: dict[str, dict[str, int]] = {}
    mismatches: list[dict[str, str]] = []

    for item in corpus:
        query = str(item.get("query", ""))
        expected = str(item.get("expected_route", ""))
        if not query or not expected:
            continue
        total += 1
        predicted = router.route(query).layer
        bucket = per_expected.setdefault(expected, {"total": 0, "correct": 0})
        bucket["total"] += 1
        if predicted == expected:
            correct += 1
            bucket["correct"] += 1
        else:
            mismatches.append({
                "id": str(item.get("id", "")),
                "expected": expected,
                "predicted": predicted,
            })
        if predicted == EXPENSIVE_LAYER:
            expensive += 1

    per_expected_acc = {
        route: {
            "total": b["total"],
            "correct": b["correct"],
            "accuracy": round(b["correct"] / b["total"], 4) if b["total"] else 0.0,
        }
        for route, b in sorted(per_expected.items())
    }
    return {
        "profile": profile,
        "total": total,
        "overall_accuracy": round(correct / total, 4) if total else 0.0,
        "expensive_path_rate": round(expensive / total, 4) if total else 0.0,
        "per_expected_route": per_expected_acc,
        "mismatches": mismatches,
    }


def build_envelope(result: dict[str, Any], corpus_size: int) -> dict[str, Any]:
    return {
        "benchmark": "routing_accuracy_check",
        "schema_version": 1,
        "generated_utc": _utc_iso(),
        "inputs": {
            "profile": result["profile"],
            "corpus": "configs/benchmarks.yaml",
            "corpus_size": corpus_size,
            "expensive_layer": EXPENSIVE_LAYER,
        },
        "result": result,
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": True,
            "no_superiority_claim": True,
            "forbidden_vocabulary_excluded": list(FORBIDDEN_VOCABULARY),
        },
    }


def render_summary(env: dict[str, Any]) -> str:
    r = env["result"]
    lines = [
        "Routing accuracy + expensive-path-rate check",
        f"  profile={r['profile']} corpus={r['total']} queries",
        f"  overall_accuracy={r['overall_accuracy']}  expensive_path_rate={r['expensive_path_rate']}",
        "  per expected route:",
    ]
    for route, b in r["per_expected_route"].items():
        lines.append(f"    {route:<14} {b['correct']}/{b['total']}  acc={b['accuracy']}")
    if r["mismatches"]:
        lines.append(f"  mismatches ({len(r['mismatches'])}):")
        for m in r["mismatches"]:
            lines.append(f"    {m['id']:<24} expected={m['expected']} predicted={m['predicted']}")
    return "\n".join(lines)


def assert_vocabulary_clean(text: str) -> None:
    low = text.lower()
    hit = [p for p in FORBIDDEN_VOCABULARY if p.lower() in low]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered summary: {hit}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="apiary",
                    help="capsule profile to load")
    ap.add_argument("--corpus", default="configs/benchmarks.yaml")
    ap.add_argument("--out-dir", default=None,
                    help="if set, write the JSON envelope to <out-dir>/routing_accuracy_check.json")
    args = ap.parse_args(argv)

    corpus = load_corpus(REPO_ROOT / args.corpus)
    result = evaluate(args.profile, corpus)
    env = build_envelope(result, corpus_size=len(corpus))

    summary = render_summary(env)
    assert_vocabulary_clean(summary)
    print(summary)

    if args.out_dir:
        out_dir = REPO_ROOT / args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "routing_accuracy_check.json"
        out_path.write_text(json.dumps(env, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
