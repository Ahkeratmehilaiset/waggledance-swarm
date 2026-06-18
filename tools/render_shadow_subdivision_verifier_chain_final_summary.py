# SPDX-License-Identifier: BUSL-1.1
"""Path-free reviewer FINAL summary renderer for the shadow-subdivision verifier chain.

Advances the WD Image #1 *hexagonal upgrades* pillar. The already-merged
``tools/render_hex_subdivision_reviewer_summary.py`` (#1273) renders the SINGLE
deepest verifier verdict; this tool renders the end-to-end CHAIN rollup: one overall
``chain_clean`` over EVERY level of the shadow-subdivision replay verifier chain the
capability manifest constructs - WITHOUT appending it anywhere.

It combines two dimensions, both fail-closed:

* BREADTH / COMPLETENESS (absence != evidence): the FULL expected chain level set
  (``_CHAIN_LEVEL_KEYS``) must be present and each level's ``ok`` must be strictly
  True. A dropped or non-ok level drives the rollup False - it never default-cleans.
* DEPTH / RE-DERIVE-FROM-COMPONENTS: the three independent VERIFY verdicts (artifact
  verifier, template index-entry verifier, deepest index-entry verifier) are the
  actual verification gates, so each is re-derived from its OWN components - every
  ``checks`` key, every match field, every all-match dict, every authority/boundary
  boolean, strict-empty blocker/warning lists, strict-int counts - never trusting a
  verdict's own ``ok`` composite (carries #1273/#1274 lessons). A build level that
  forged ``ok=True`` is still caught here: the verify level that independently
  re-derives it fails closed.

``chain_clean`` is the conjunction of all-levels-present-and-ok AND all three deep
verify re-derivations. CONTENT-SAFE by construction: the summary emits ONLY derived
booleans and STRICT ints via a closed ``_SUMMARY_SAFE_KEYS`` allowlist (raises on any
non-allowlisted key), never a raw status string, identifier, proof id, conclusion,
path, or payload - so no caller-controlled content can survive regardless of what a
verdict carries. ``path_free_verified`` is DERIVED by scanning the rendered summary
with the chain's own ``_contains_path_marker`` (never hardcoded).

It is READ-ONLY and grants no authority: no bridge append, no payload inclusion, no
local path recording, no transport, no runtime mutation, no runtime subdivision
authority, no claim_safe upgrade, and no fake transition-count progress.

Exact validation commands::

    python tools/render_shadow_subdivision_verifier_chain_final_summary.py --json
    python -m pytest tests/test_render_shadow_subdivision_verifier_chain_final_summary.py -q

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

REPORT_VERSION = "wd.shadow_subdivision_verifier_chain_final_summary.v1"

# The ONLY keys the summary may contain - all derived booleans/strict ints (plus the
# fixed version string), never raw content. Enforced so a future edit cannot widen
# the surface to a raw field.
_SUMMARY_SAFE_KEYS = frozenset({
    "report_version",
    "chain_levels_total",
    "chain_levels_present",
    "chain_levels_ok",
    "chain_levels_all_ok",
    "chain_levels_shape_ok",
    "artifact_verify_clean",
    "index_entry_verify_clean",
    "deepest_verify_clean",
    "total_blocker_count",
    "total_warning_count",
    "chain_clean",
    "path_free_verified",
})

# The ordered shadow-subdivision replay verifier chain levels, exactly as the
# capability manifest stores them under the hexagonal_upgrades proof. BREADTH is
# checked across ALL of these; DEPTH is re-derived for the three verify levels below.
_CHAIN_LEVEL_KEYS: tuple[str, ...] = (
    "shadow_subdivision_replay",
    "shadow_subdivision_replay_verification",
    "shadow_subdivision_replay_verifier_summary",
    "shadow_subdivision_replay_verifier_summary_bridge_event_template",
    "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry",
    "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    "_verification",
    "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    "_verification_summary",
    "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    "_verification_summary_bridge_event_template",
    "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    "_verification_summary_bridge_event_template_index_entry",
    "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    "_verification_summary_bridge_event_template_index_entry_verification",
)

# The three independent VERIFY verdicts (the actual verification gates), re-derived
# deeply below. These are members of _CHAIN_LEVEL_KEYS.
_STAGE_A_KEY = "shadow_subdivision_replay_verification"
_STAGE_B_KEY = (
    "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    "_verification"
)
_STAGE_C_KEY = (
    "shadow_subdivision_replay_verifier_summary_bridge_event_template_index_entry"
    "_verification_summary_bridge_event_template_index_entry_verification"
)

# Stage A (verify_shadow_subdivision_replay_artifact) completeness anchor: a stable,
# safety-critical subset of its `checks` dict that MUST be present and True. Missing
# any of these -> not complete -> not clean (a partial/forged checks dict fails).
_EXPECTED_STAGE_A_CHECK_KEYS = frozenset({
    "artifact_path_free",
    "artifact_digest_match",
    "declared_ok_matches_recomputed_contract",
    "shadow_plan_no_runtime_mutation",
    "shadow_plan_target_state",
    "runtime_shadow_children_absent",
    "runtime_dispatch_disabled",
    "source_snapshot_path_free",
    "guardrail_no_runtime_topology_mutation",
    "guardrail_runtime_authority_changed",
    "guardrail_raw_query_or_payload_included",
    "guardrail_local_paths_recorded",
    "guardrail_network_transport_added",
})

# Stages B/C (the index-entry verifiers) expected verdict surface.
_INDEX_ENTRY_MATCH_FIELDS = (
    "source_contract_check",
    "rebuilt_index_entry_check",
    "bridge_event_schema_check",
)
_INDEX_ENTRY_ALL_MATCH_DICTS = (
    "digest_checks",
    "size_checks",
    "schema_version_checks",
)
# Authority / side-effect boundary fields that MUST be strictly False.
_INDEX_ENTRY_AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "automatic_release_decision",
    "direct_bridge_write_performed",
    "external_fetch_performed",
    "local_paths_recorded",
    "release_decision_made",
    "runtime_controls_added",
    "runtime_subdivision_authority_granted",
    "transport_added",
    "artifact_payloads_included",
)
# Review-discipline fields that MUST be strictly True.
_INDEX_ENTRY_TRUE_FIELDS = ("template_only", "manual_review_required")
_INDEX_ENTRY_REQUIRED_FIELDS = (
    frozenset(_INDEX_ENTRY_MATCH_FIELDS)
    | frozenset(_INDEX_ENTRY_ALL_MATCH_DICTS)
    | frozenset(_INDEX_ENTRY_AUTHORITY_FALSE_FIELDS)
    | frozenset(_INDEX_ENTRY_TRUE_FIELDS)
    | frozenset({"ok", "blockers", "warnings", "artifact_count_checked"})
)


def _count(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _strict_empty_list(value: Any) -> bool:
    """True iff value is strictly an empty list. A malformed (non-list) value must
    NOT collapse to 'clean' (the #1273 lenient-count fail-open)."""
    return isinstance(value, list) and len(value) == 0


def _strict_int_one(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def _all_match(value: Any) -> bool:
    """True iff a non-empty status map has every value == 'match' (fail-closed: an
    empty map or any non-match status -> False)."""
    if not isinstance(value, Mapping) or not value:
        return False
    return all(item == "match" for item in value.values())


def _all_true_nonempty(value: Any) -> bool:
    """True iff a non-empty mapping has every value strictly True (fail-closed)."""
    if not isinstance(value, Mapping) or not value:
        return False
    return all(item is True for item in value.values())


def _level_ok(value: Any) -> bool:
    """Breadth check: a level is a mapping whose `ok` is STRICTLY True (fail-closed:
    a missing/non-mapping level, or any non-True `ok`, is False)."""
    return isinstance(value, Mapping) and value.get("ok") is True


def _level_blocker_warning_shape_ok(value: Any) -> bool:
    """Breadth shape check for EVERY chain level (not only the three verify gates).

    A present `blockers` field MUST be a STRICT-EMPTY list: a non-list is malformed
    AND a non-empty list means the level reports a blocker, so neither is "clean" -
    both fail closed even when the level's own `ok` is True. It must never be silently
    treated as `_count`-zero / clean (the #1273 lenient-count fail-open; lead
    exact-head forge on PR #1276: `blockers='not-a-list'` on a non-verifier level
    previously left chain_clean True with total_blocker_count 0).

    A present `warnings` field MUST be a list (shape only): warnings are non-fatal, so
    a non-empty warnings list is allowed and surfaced via total_warning_count
    (matches the merged #1273 reviewer-summary treatment).

    An ABSENT field is allowed: not every chain level carries blockers/warnings (e.g.
    the replay artifact level carries neither, and the first verification level
    carries blockers but no warnings)."""
    if not isinstance(value, Mapping):
        return False
    if "blockers" in value and not _strict_empty_list(value["blockers"]):
        return False
    if "warnings" in value and not isinstance(value["warnings"], list):
        return False
    return True


def _stage_artifact_verify_clean(verdict: Any) -> bool:
    """Re-derive the artifact verifier (stage A) clean status from its components.

    Grounded in its `checks` dict (completeness): the expected safety-critical check
    keys must be present, every present check must be strictly True, and the verdict
    must declare ok + empty blockers + matching recomputed contract. Never trusts ok
    alone.
    """
    if not isinstance(verdict, Mapping):
        return False
    checks = verdict.get("checks")
    if not isinstance(checks, Mapping):
        return False
    if _EXPECTED_STAGE_A_CHECK_KEYS - set(checks):
        return False  # an expected check is absent -> not complete
    if not _all_true_nonempty(checks):
        return False  # any present check not strictly True -> not clean
    return (
        verdict.get("ok") is True
        and _strict_empty_list(verdict.get("blockers"))
        and verdict.get("artifact_declared_ok") is True
        and verdict.get("recomputed_contract_ok") is True
    )


def _stage_index_entry_verify_clean(verdict: Any) -> bool:
    """Re-derive an index-entry verifier (stage B/C) clean status from its own
    components: full expected field set present, ok + strict-empty blockers/warnings,
    strict-int artifact count, every match field == 'match', every all-match dict
    clean, the True-required fields True, and every authority/boundary field strictly
    False. Never trusts ok alone."""
    if not isinstance(verdict, Mapping):
        return False
    if _INDEX_ENTRY_REQUIRED_FIELDS - set(verdict):
        return False  # an expected field is absent -> not complete
    if verdict.get("ok") is not True:
        return False
    if not _strict_empty_list(verdict.get("blockers")):
        return False
    if not _strict_empty_list(verdict.get("warnings")):
        return False
    if not _strict_int_one(verdict.get("artifact_count_checked")):
        return False
    for field in _INDEX_ENTRY_MATCH_FIELDS:
        if verdict.get(field) != "match":
            return False
    for field in _INDEX_ENTRY_ALL_MATCH_DICTS:
        if not _all_match(verdict.get(field)):
            return False
    for field in _INDEX_ENTRY_TRUE_FIELDS:
        if verdict.get(field) is not True:
            return False
    for field in _INDEX_ENTRY_AUTHORITY_FALSE_FIELDS:
        if verdict.get(field) is not False:
            return False
    return True


def render_chain_final_summary(proof: Any) -> dict[str, Any]:
    """Render a path-free FINAL chain summary from a hexagonal_upgrades proof mapping.

    Emits only DERIVED booleans/strict ints. A non-mapping proof, a missing/non-ok
    level, or any malformed verify verdict fails closed (chain_clean False).
    """
    p: Mapping[str, Any] = proof if isinstance(proof, Mapping) else {}

    levels = [p.get(key) for key in _CHAIN_LEVEL_KEYS]
    total = len(_CHAIN_LEVEL_KEYS)
    present = sum(1 for level in levels if isinstance(level, Mapping))
    levels_ok = sum(1 for level in levels if _level_ok(level))
    # Breadth, fail-closed: every expected level present AND every level ok==True.
    chain_levels_all_ok = present == total and levels_ok == total
    # Breadth shape, fail-closed: EVERY present level's blockers/warnings (when the
    # field exists) must be a strict list. A malformed (non-list) value on ANY level
    # - including the non-verifier build levels - drives the rollup not-clean and is
    # never silently counted as zero. Requires every expected level present too, so a
    # dropped level cannot pass the shape gate by absence.
    chain_levels_shape_ok = present == total and all(
        _level_blocker_warning_shape_ok(level) for level in levels
    )
    total_blockers = sum(
        _count(level.get("blockers")) for level in levels if isinstance(level, Mapping)
    )
    total_warnings = sum(
        _count(level.get("warnings")) for level in levels if isinstance(level, Mapping)
    )

    # Depth, fail-closed: re-derive each verify gate from its own components.
    artifact_clean = _stage_artifact_verify_clean(p.get(_STAGE_A_KEY))
    index_clean = _stage_index_entry_verify_clean(p.get(_STAGE_B_KEY))
    deepest_clean = _stage_index_entry_verify_clean(p.get(_STAGE_C_KEY))

    summary: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "chain_levels_total": total,
        "chain_levels_present": present,
        "chain_levels_ok": levels_ok,
        "chain_levels_all_ok": chain_levels_all_ok,
        "chain_levels_shape_ok": chain_levels_shape_ok,
        "artifact_verify_clean": artifact_clean,
        "index_entry_verify_clean": index_clean,
        "deepest_verify_clean": deepest_clean,
        "total_blocker_count": total_blockers,
        "total_warning_count": total_warnings,
        "chain_clean": bool(
            chain_levels_all_ok
            and chain_levels_shape_ok
            and artifact_clean
            and index_clean
            and deepest_clean
        ),
    }
    # DERIVE path_free_verified by scanning the rendered output (never hardcoded). By
    # construction the summary holds only booleans/ints/the fixed version string, so
    # this is True - but it is verified, so a future raw field would flip it.
    summary["path_free_verified"] = not _contains_path_marker(summary)

    extra = set(summary) - _SUMMARY_SAFE_KEYS
    if extra:
        raise ValueError(f"chain final summary has non-allowlisted keys: {sorted(extra)}")
    return summary


def render_summary(summary: Mapping[str, Any]) -> str:
    return "\n".join([
        "Shadow-subdivision verifier chain final summary (path-free, read-only)",
        f"  chain_clean={summary['chain_clean']} "
        f"chain_levels_all_ok={summary['chain_levels_all_ok']} "
        f"path_free_verified={summary['path_free_verified']}",
        f"  levels_ok={summary['chain_levels_ok']}/{summary['chain_levels_total']} "
        f"present={summary['chain_levels_present']} "
        f"shape_ok={summary['chain_levels_shape_ok']} "
        f"blockers={summary['total_blocker_count']} "
        f"warnings={summary['total_warning_count']}",
        f"  artifact_verify_clean={summary['artifact_verify_clean']} "
        f"index_entry_verify_clean={summary['index_entry_verify_clean']} "
        f"deepest_verify_clean={summary['deepest_verify_clean']}",
    ])


def assert_vocabulary_clean(text: str) -> None:
    hit = [
        p for p in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(p) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered text: {hit}")


def _build_live_proof() -> dict[str, Any]:
    """Read the real hexagonal_upgrades proof from the capability manifest
    (read-only; reuses the manifest's own chain construction as single source of
    truth rather than fragilely re-mirroring the chain here)."""
    from tools.wd_image1_capability_manifest import build_manifest  # noqa: PLC0415

    manifest = build_manifest(ROOT)
    for capability in manifest.get("capabilities", []):
        if capability.get("capability_id") == "hexagonal_upgrades":
            proof = capability.get("proof")
            if isinstance(proof, Mapping):
                return dict(proof)
    raise SystemExit("hexagonal_upgrades proof not found in manifest")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = render_chain_final_summary(_build_live_proof())
    text = render_summary(summary)
    assert_vocabulary_clean(text)
    json_report = json.dumps(summary, indent=2, sort_keys=True)
    # safe-scalar emission: the summary holds only bools/ints/the version string, so
    # it must be path-free (verified, not assumed).
    if _contains_path_marker(summary):
        raise SystemExit("path marker present in chain final summary")
    assert_vocabulary_clean(json_report)
    print(json_report if args.json else text)
    return 0 if summary["chain_clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
