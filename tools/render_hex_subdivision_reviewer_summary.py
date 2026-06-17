# SPDX-License-Identifier: BUSL-1.1
"""Path-free reviewer summary renderer for the shadow-subdivision verifier chain.

Advances the WD Image #1 *hexagonal upgrades* pillar's next step: a path-free
reviewer summary renderer for the deepest shadow-subdivision replay verifier
(`verify_shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry`
in tools/hex_shadow_subdivision_replay.py) - WITHOUT appending it anywhere.

It is READ-ONLY and grants no authority. To be content-safe by construction it
emits ONLY DERIVED booleans and counts - never a raw blocker string, status
value, identifier, path, query, or payload. So no caller-controlled content can
survive into the rendered summary regardless of what the verdict carries. As an
extra check the rendered output is scanned for path markers (reusing the chain's
own `_contains_path_marker`) and `path_free_verified` is DERIVED from that scan,
never hardcoded.

Exact validation commands::

    python tools/render_hex_subdivision_reviewer_summary.py --json
    python -m pytest tests/test_render_hex_subdivision_reviewer_summary.py -q

Engineering record; offline; read-only; forbidden-vocabulary guarded. No claim of
superiority over any external system.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hex_shadow_subdivision_replay import (  # noqa: E402
    _contains_path_marker,
)

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

REPORT_VERSION = "wd.hex_subdivision_reviewer_summary.v1"

# The ONLY keys the summary may contain - all derived booleans/counts, never raw
# content. Enforced so a future edit cannot widen the surface to a raw field.
_SUMMARY_SAFE_KEYS = frozenset({
    "report_version",
    "verdict_ok",
    "blocker_count",
    "warning_count",
    "source_contract_match",
    "rebuilt_index_entry_match",
    "digest_all_match",
    "size_all_match",
    "schema_version_all_match",
    "all_checks_match",
    "path_free_verified",
    "review_clean",
})


def _count(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _all_match(value: Any) -> bool:
    """True iff a non-empty status map has every value == 'match' (fail-closed:
    an empty map or any non-match status -> False)."""
    if not isinstance(value, Mapping) or not value:
        return False
    return all(item == "match" for item in value.values())


def render_reviewer_summary(verdict: Any) -> dict[str, Any]:
    """Render a path-free reviewer summary from a deepest-verifier verdict.

    Emits only DERIVED booleans/counts. A non-mapping verdict fails closed
    (verdict_ok False, everything False/0).
    """
    v: Mapping[str, Any] = verdict if isinstance(verdict, Mapping) else {}
    digest_all = _all_match(v.get("digest_checks"))
    size_all = _all_match(v.get("size_checks"))
    schema_all = _all_match(v.get("schema_version_checks"))
    summary: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "verdict_ok": v.get("ok") is True,
        "blocker_count": _count(v.get("blockers")),
        "warning_count": _count(v.get("warnings")),
        # status values are reduced to booleans - the raw status string is never
        # rendered, so no free-form/identifier content can ride along.
        "source_contract_match": v.get("source_contract_check") == "match",
        "rebuilt_index_entry_match": v.get("rebuilt_index_entry_check") == "match",
        "digest_all_match": digest_all,
        "size_all_match": size_all,
        "schema_version_all_match": schema_all,
        "all_checks_match": digest_all and size_all and schema_all,
        "review_clean": (
            v.get("ok") is True
            and _count(v.get("blockers")) == 0
            and digest_all
            and size_all
            and schema_all
        ),
    }
    # DERIVE path_free_verified by scanning the rendered output (never hardcoded).
    # By construction the summary holds only booleans/ints/the fixed version
    # string, so this is True - but it is verified, not assumed, so a future field
    # that introduced raw content would flip it.
    summary["path_free_verified"] = not _contains_path_marker(summary)
    extra = set(summary) - _SUMMARY_SAFE_KEYS
    if extra:
        raise ValueError(f"reviewer summary has non-allowlisted keys: {sorted(extra)}")
    return summary


def render_summary(summary: Mapping[str, Any]) -> str:
    return "\n".join([
        "Hex-subdivision reviewer summary (path-free, read-only)",
        f"  verdict_ok={summary['verdict_ok']} review_clean={summary['review_clean']} "
        f"path_free_verified={summary['path_free_verified']}",
        f"  blockers={summary['blocker_count']} warnings={summary['warning_count']}",
        f"  source_contract_match={summary['source_contract_match']} "
        f"rebuilt_index_entry_match={summary['rebuilt_index_entry_match']} "
        f"all_checks_match={summary['all_checks_match']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    hit = [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered text: {hit}")


# The capability-manifest already constructs the full (12-step) shadow-subdivision
# verifier chain for the hexagonal_upgrades proof and stores the DEEPEST verifier
# verdict under this key - so the CLI reuses that single source-of-truth
# construction (with all its exact reviewer/handoff/template args) rather than
# fragilely re-mirroring the chain here.
_DEEPEST_VERDICT_KEY = (
    "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    "_verification_summary_bridge_event_template_index_entry_verification"
)


def _build_live_verdict() -> dict[str, Any]:
    """Read the real deepest-verifier verdict from the capability manifest
    (read-only; reuses the manifest's own chain construction)."""
    from tools.wd_image1_capability_manifest import build_manifest  # noqa: PLC0415

    manifest = build_manifest(ROOT)
    for capability in manifest.get("capabilities", []):
        if capability.get("capability_id") == "hexagonal_upgrades":
            proof = capability.get("proof") or {}
            verdict = proof.get(_DEEPEST_VERDICT_KEY)
            if isinstance(verdict, Mapping):
                return dict(verdict)
    raise SystemExit("deepest shadow-subdivision verifier verdict not found in manifest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = render_reviewer_summary(_build_live_verdict())
    text = render_summary(summary)
    assert_vocabulary_clean(text)
    json_report = json.dumps(summary, indent=2, sort_keys=True)
    assert_vocabulary_clean(json_report)
    print(json_report if args.json else text)
    return 0 if summary["review_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
