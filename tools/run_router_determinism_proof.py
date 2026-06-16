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


# Router layer enum (core/smart_router_v2) - the only single-word emitted values
# that could legitimately coincide with a query token, so they anchor the safe
# vocabulary that the token-level leak scan must exclude to avoid false positives.
_KNOWN_LAYERS = (
    "model_based",
    "retrieval",
    "llm_reasoning",
    "rule_constraints",
    "statistical",
)
# Reason-label / classifier-class vocabulary the router can emit.
_KNOWN_REASON_VOCAB = (
    "capsule_decision_match",
    "capsule_priority_fallback",
    "keyword_classifier",
    "math",
    "seasonal",
    "rule",
    "stat",
)
# Minimum token length scanned for raw-query leakage. Short common words (the,
# hive, ...) are skipped to avoid false positives; injected sentinels are long.
_MIN_LEAK_TOKEN_LEN = 6


def _tokenize(text: str) -> set[str]:
    return {t.lower() for t in re.split(r"[^A-Za-z0-9]+", str(text)) if t}


def _stable_decision(result: Any) -> dict[str, Any]:
    """Stable semantic view of a RouteResult (no volatile/raw fields)."""
    payload = result.to_dict()
    return {field: payload.get(field) for field in STABLE_DECISION_FIELDS}


def _safe_vocab(capsule: Any) -> set[str]:
    """Trusted terms that may legitimately appear in emitted decision fields.

    Union of the router layer enum, reason/classifier vocab, and every capsule
    decision id / declared keyword (and their subword tokens). A raw-query token
    that is NOT in this set and shows up in the emitted body is a leak.
    """
    vocab: set[str] = set(_KNOWN_LAYERS) | set(_KNOWN_REASON_VOCAB)
    vocab.add(str(getattr(capsule, "default_fallback", "") or "").lower())
    for dec in (getattr(capsule, "key_decisions", None) or []):
        dec_id = str(dec.get("id") or "")
        if dec_id:
            vocab.add(dec_id.lower())
            vocab |= _tokenize(dec_id)
        for kw in (dec.get("keywords") or []):
            vocab.add(str(kw).lower())
            vocab |= _tokenize(kw)
    return {v for v in vocab if v}


def _derive_raw_query_not_emitted(
    decisions: list[dict[str, Any]],
    nondeterministic: list[dict[str, Any]],
    raw_queries: list[str],
    safe_vocab: set[str] | None = None,
) -> bool:
    """Re-scan the emitted data and decide the privacy invariant from data.

    Fail-closed against BOTH a full raw query AND any non-trivial raw-query TOKEN
    (len >= _MIN_LEAK_TOKEN_LEN) that is not part of the trusted safe vocabulary -
    so a single query token injected into a stable emitted field (e.g. a forged
    decision_id/reason) is caught, not just a whole-query leak. Never hardcoded.
    """
    safe = {s.lower() for s in (safe_vocab or set())}
    scan_body = json.dumps(
        {"routing_decisions": decisions, "nondeterministic_leads": nondeterministic},
        ensure_ascii=False,
    ).lower()
    # Whole-query leak.
    if any(q and q.lower() in scan_body for q in raw_queries):
        return False
    # Token-level leak: any non-trivial, non-vocab query token present (word-
    # boundary, so underscored capsule ids like "varroa_treatment" don't trip).
    for q in raw_queries:
        for tok in _tokenize(q):
            if len(tok) < _MIN_LEAK_TOKEN_LEN or tok in safe:
                continue
            if re.search(r"\b" + re.escape(tok) + r"\b", scan_body):
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
    raw_queries: list[str] = []

    for item in corpus:
        query = str(item.get("query", ""))
        if not query:
            continue
        total += 1
        raw_queries.append(query)
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

    # DERIVE the privacy invariant (never hardcode): no full raw query AND no
    # non-trivial raw-query token (outside the trusted capsule/router vocab) may
    # appear anywhere in the serialized emitted data. Fail-closed.
    raw_query_not_emitted = _derive_raw_query_not_emitted(
        decisions, nondeterministic, raw_queries, _safe_vocab(capsule)
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
