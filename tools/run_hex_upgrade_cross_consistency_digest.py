# SPDX-License-Identifier: BUSL-1.1
"""Path-free, measurement-only cross-consistency digest for the hex-upgrade views.

Advances the WD Image #1 *hexagonal upgrades* pillar. Three independent
measurement-only views of the same shadow-subdivision verifier chain are already
wired into ``hex_upgrade_proof``:

* ``reviewer_summary``        - the deepest verifier verdict, rendered clean (#1273/#1274)
* ``shadow_only_invariant``   - AFFIRMATIVE proof the subdivision stays shadow-only (#1275)
* ``chain_final_summary``     - the end-to-end chain rollup (#1276/#1277)

This digest confirms those three views are MUTUALLY CONSISTENT and surfaces a single
derived ``cross_consistent`` boolean. Per the lead-approved STRICT semantics,
``cross_consistent`` is True ONLY when all three views are EACH re-derived clean from
their OWN components (never their own ``review_clean`` / ``invariant_holds`` /
``chain_clean`` aggregates - #1274) and therefore agree; any view absent, malformed,
component-degraded, or in contradiction drives it False (fail-closed).

It is READ-ONLY and grants no authority: content-safe by construction (emits ONLY
derived booleans/strict ints via a closed ``_DIGEST_SAFE_KEYS`` allowlist, raising on
any non-allowlisted key), ``path_free_verified`` DERIVED via the chain's own
``_contains_path_marker`` (never hardcoded), ``claim_safe`` hardcoded False. It never
appends to the bridge, includes payloads/paths, transports, mutates runtime, grants
runtime subdivision authority, upgrades any claim, or raises the honest-zero
transition counter; it is NOT folded into any proof ``ok``.

Exact validation commands::

    python tools/run_hex_upgrade_cross_consistency_digest.py --json
    python -m pytest tests/test_run_hex_upgrade_cross_consistency_digest.py -q

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

REPORT_VERSION = "wd.hex_upgrade_cross_consistency_digest.v1"

# The ONLY keys the digest may contain - all derived booleans (plus the fixed
# version string), never raw content. Enforced so a future edit cannot widen the
# surface to a raw field.
_DIGEST_SAFE_KEYS = frozenset({
    "report_version",
    "reviewer_summary_present",
    "shadow_only_invariant_present",
    "chain_final_summary_present",
    "all_views_present",
    "reviewer_clean",
    "shadow_only_clean",
    "chain_summary_clean",
    "cross_consistent",
    "path_free_verified",
    "claim_safe",
})

# reviewer_summary (#1273/#1274) component booleans that must each be strictly True.
_REVIEWER_TRUE_FIELDS = (
    "verdict_ok",
    "path_free_verified",
    "source_contract_match",
    "rebuilt_index_entry_match",
    "digest_all_match",
    "size_all_match",
    "schema_version_all_match",
)
# shadow_only_invariant.invariant (#1275) component booleans that must be strictly True.
_SHADOW_INV_TRUE_FIELDS = (
    "artifact_ok",
    "target_state_is_shadow",
    "no_runtime_mutation",
    "guardrails_all_clean",
)
# chain_final_summary (#1276/#1277) component booleans that must each be strictly True.
_CHAIN_TRUE_FIELDS = (
    "path_free_verified",
    "chain_levels_all_ok",
    "chain_levels_shape_ok",
    "artifact_verify_clean",
    "index_entry_verify_clean",
    "deepest_verify_clean",
)


def _strict_zero_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reviewer_clean(view: Any) -> bool:
    """Re-derive the reviewer summary clean verdict from its COMPONENTS (never its
    own ``review_clean`` aggregate): every match/derived bool strictly True and a
    strict-int-0 blocker count. Absent/malformed -> False."""
    if not isinstance(view, Mapping):
        return False
    if not _strict_zero_int(view.get("blocker_count")):
        return False
    return all(view.get(field) is True for field in _REVIEWER_TRUE_FIELDS)


def _shadow_only_clean(view: Any) -> bool:
    """Re-derive shadow-only enforcement from the proof's COMPONENTS (never its own
    ``invariant_holds`` aggregate): ok + deterministic replay + the positive invariant
    booleans True, transition NOT occurred, no injected count violation, a strict-int-0
    transition count, and the measurement-only proof must NOT self-declare claim_safe.
    Absent/malformed -> False."""
    if not isinstance(view, Mapping):
        return False
    inv = _mapping(view.get("invariant"))
    replay = _mapping(view.get("deterministic_replay"))
    if not _strict_zero_int(inv.get("shadow_to_candidate_subdivision_transitions_total")):
        return False
    if view.get("ok") is not True:
        return False
    if replay.get("stable_identical") is not True:
        return False
    if inv.get("transition_occurred") is not False:
        return False
    if inv.get("injected_transition_count_violation") is not False:
        return False
    if inv.get("claim_safe") is True:
        return False
    return all(inv.get(field) is True for field in _SHADOW_INV_TRUE_FIELDS)


def _chain_summary_clean(view: Any) -> bool:
    """Re-derive the chain final summary clean verdict from its COMPONENTS (never its
    own ``chain_clean`` aggregate): every level/verify bool strictly True, an equal
    strict-positive-int level count (all levels present), and a strict-int-0 blocker
    count. Absent/malformed -> False."""
    if not isinstance(view, Mapping):
        return False
    total = view.get("chain_levels_total")
    present = view.get("chain_levels_present")
    levels_complete = (
        isinstance(total, int)
        and not isinstance(total, bool)
        and total > 0
        and present == total
    )
    if not levels_complete:
        return False
    if not _strict_zero_int(view.get("total_blocker_count")):
        return False
    return all(view.get(field) is True for field in _CHAIN_TRUE_FIELDS)


def build_cross_consistency_digest(proof: Any) -> dict[str, Any]:
    """Build the path-free cross-consistency digest from a hexagonal_upgrades proof.

    Emits only DERIVED booleans. cross_consistent is True ONLY when all three views
    are present AND each re-derived clean from its own components (strict semantics);
    any absence/malformation/degradation/contradiction fails closed.
    """
    p: Mapping[str, Any] = proof if isinstance(proof, Mapping) else {}
    reviewer = p.get("reviewer_summary")
    shadow = p.get("shadow_only_invariant")
    chain = p.get("chain_final_summary")

    reviewer_present = isinstance(reviewer, Mapping)
    shadow_present = isinstance(shadow, Mapping)
    chain_present = isinstance(chain, Mapping)
    all_present = reviewer_present and shadow_present and chain_present

    reviewer_clean = _reviewer_clean(reviewer)
    shadow_only_clean = _shadow_only_clean(shadow)
    chain_summary_clean = _chain_summary_clean(chain)

    # STRICT (lead-approved): consistent ONLY when every view is present and each is
    # re-derived clean from components - so the three independent views AGREE. A view
    # claiming clean while a sibling is not-clean cannot pass (conjunction catches the
    # contradiction); all-not-clean is reported via the component flags but is NOT a
    # clean cross-check, so cross_consistent stays False.
    cross_consistent = bool(
        all_present
        and reviewer_clean
        and shadow_only_clean
        and chain_summary_clean
    )

    digest: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "reviewer_summary_present": reviewer_present,
        "shadow_only_invariant_present": shadow_present,
        "chain_final_summary_present": chain_present,
        "all_views_present": all_present,
        "reviewer_clean": reviewer_clean,
        "shadow_only_clean": shadow_only_clean,
        "chain_summary_clean": chain_summary_clean,
        "cross_consistent": cross_consistent,
        # measurement-only: this digest never grants authority or upgrades a claim.
        "claim_safe": False,
    }
    # DERIVE path_free_verified by scanning the rendered output (never hardcoded). By
    # construction the digest holds only booleans + the fixed version string, so this
    # is True - but it is verified, so a future raw field would flip it.
    digest["path_free_verified"] = not _contains_path_marker(digest)

    extra = set(digest) - _DIGEST_SAFE_KEYS
    if extra:
        raise ValueError(f"cross-consistency digest has non-allowlisted keys: {sorted(extra)}")
    return digest


def render_summary(digest: Mapping[str, Any]) -> str:
    return "\n".join([
        "Hex-upgrade cross-consistency digest (path-free, read-only, measurement-only)",
        f"  cross_consistent={digest['cross_consistent']} "
        f"all_views_present={digest['all_views_present']} "
        f"path_free_verified={digest['path_free_verified']}",
        f"  reviewer_clean={digest['reviewer_clean']} "
        f"shadow_only_clean={digest['shadow_only_clean']} "
        f"chain_summary_clean={digest['chain_summary_clean']}",
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
    (read-only; reuses the manifest's own construction as single source of truth)."""
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
    digest = build_cross_consistency_digest(_build_live_proof())
    text = render_summary(digest)
    assert_vocabulary_clean(text)
    json_report = json.dumps(digest, indent=2, sort_keys=True)
    # safe-scalar emission: the digest holds only bools + the version string, so it
    # must be path-free (verified, not assumed).
    if _contains_path_marker(digest):
        raise SystemExit("path marker present in cross-consistency digest")
    assert_vocabulary_clean(json_report)
    print(json_report if args.json else text)
    return 0 if digest["cross_consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
