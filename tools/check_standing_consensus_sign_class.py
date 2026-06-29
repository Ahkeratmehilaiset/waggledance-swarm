#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""9b standing-consensus-sign class checker + admission decision.

CLAUDE.md Rule 9b / BRIDGE_CONSENSUS_APPROVAL_V1 standing-consensus-sign amendment.
SEPARATE from the P1 proven-safe auto-sign checker (reuses no P1-specific policy).

Decides whether an OFF-ALLOWLIST PR may have the operator's per-PR signature
satisfied by a STANDING signature because best-possible-consensus holds. The whole
design is FAIL-CLOSED and this module is itself (a)-class + charter-denylisted, so:

* it classifies any change to gate-verdict code / governance / itself as (a) ->
  such a PR can NEVER ride standing-consensus-sign (no self-activation, no
  gate-weakening via the mechanism the gate grants);
* admission is DEFAULT-OFF -- the caller must explicitly enable it, so merging the
  PR that wires this module does not by itself turn standing-sign on;
* admission requires the FULL best-possible-consensus (lead+tools build, DUAL-RCO
  BOTH recognized RCOs, CI all-green, no recognized-RCO veto, head-exact,
  diff-clean, MAGMA receipt). Any missing/ambiguous element -> operator_review.

Two pure entry points (no side effects, dict in / dict out):
  classify_ab(changed_paths)            -> mechanical (a)/(b) classification.
  evaluate_standing_consensus_sign(...) -> admission verdict (admitted bool).
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from waggledance.core.idle_consensus_charter import evaluate_paths, load_charter

REPORT_VERSION = "wd.standing_consensus_sign_class.v0"

RECOGNIZED_RCOS = ("claude-rco-1", "claude-rco-2")

# ----------------------------------------------------------------------------
# (a)-class: ALWAYS operator-EXPLICIT. Never rides standing-consensus-sign.
# These lists are the SECURITY-CRITICAL half: any gate-verdict file, governance
# doc, bridge runtime script, gate-conformance anchor, outward/secret surface, or
# THIS module MUST resolve to (a). A path is classified by exact name, by a code
# substring (so a rename/move/refactor cannot escape), or by a prefix.
# ----------------------------------------------------------------------------
_A_EXACT = frozenset({
    "claude.md",
    "agents.md",
    "docs/architecture/idle_autonomy_charter.md",
    "docs/architecture/bridge_consensus_approval_v1.md",
    "docs/architecture/idle_consensus_artifact_v1.md",
    "docs/architecture/policy_surface_v0.md",
    "docs/architecture/idle_protocol_v1.md",
    "docs/architecture/stage2_cutover_rfc.md",
    "docs/architecture/magma_substrate_audit_2026_05_17.md",
    "license",
    "readme.md",
    "pyproject.toml",
})
# Verdict-computing gate code + bridge gate scripts + THIS module (self).
_A_CODE_SUBSTR = (
    "idle_consensus_auto_merge",
    "verify_bridge_consensus",
    "check_bridge_changes_requested",
    "check_rco_pass_present",
    "merge_with_bridge_receipt",
    "write_bridge_consensus_merge_receipt",
    "idle_consensus_charter",
    "check_proven_safe_autosign_class",
    "check_standing_consensus_sign_class",   # self -> no self-activation
    "bridge_event_taxonomy",                 # wired into the live veto consumer
    "check_status_name_safe",
    "idle_consensus_to_pr",
)
# gate-CONFORMANCE anchors (tests/corpora that LOCK the gate fail-closed behaviour).
# "conformance" as a substring catches a FUTURE gate-conformance anchor that was
# not enumerated (rco-2 forward-hardening note ii); p4c corpus CASES do not contain
# it so they stay (b).
_A_CONFORMANCE_SUBSTR = (
    "conformance",                             # any *conformance* anchor -> (a)
    "test_cause_b_rco_finding_blocks_by_type",
    "test_check_bridge_changes_requested",
    "test_check_rco_pass_present",
    "test_standing_consensus_sign_class",      # this module's own conformance (self)
    "validate_p4c_corpus",                     # p4c VALIDATOR anchor (corpus CASES stay (b))
)
# GOVERNANCE-CONTRACT doc substrings: catch FUTURE versions (V2/V3) of the core
# governance/contract docs so they never ride (b) via the docs/architecture/*.md
# pattern (rco-2 forward-hardening note i). _A_EXACT pins the current files; these
# substrings future-proof renames/versions.
_A_GOV_DOC_SUBSTR = (
    "consensus_approval",      # BRIDGE_CONSENSUS_APPROVAL_V*
    "autonomy_charter",        # IDLE_AUTONOMY_CHARTER*
    "consensus_artifact",      # IDLE_CONSENSUS_ARTIFACT_V*
    "stage2_cutover",          # STAGE2_CUTOVER_RFC*
    "policy_surface",          # POLICY_SURFACE_V*
    "idle_protocol_v",         # IDLE_PROTOCOL_V*
    "magma_substrate_audit",   # MAGMA_SUBSTRATE_AUDIT* (complete _A_EXACT gov family)
)
_A_PREFIX = (
    ".agent-bridge/bin/",
    ".agent-bridge/",                # bridge runtime
    "deploy/", "deployment/", "configs/deployment/",
)
_A_SUBSTR = ("secret", "token", "credential", ".env")

# ----------------------------------------------------------------------------
# (b)-class: recognized REVERSIBLE artifacts that MAY ride standing-sign. This is
# a CLOSED allowlist of categories. Anything matching neither (a) nor (b) falls
# through to (a) -- "when in doubt, operator-explicit" (fail-closed).
# ----------------------------------------------------------------------------
_B_PATTERNS = (
    re.compile(r"^docs/runs/[a-z0-9_./-]+\.md$"),             # sprint boards / run logs
    re.compile(r"^docs/benchmarks/[a-z0-9_./-]+\.md$"),       # perf docs (non-gate)
    # dormant proof/harness runners ONLY (the run_* convention is deliberately
    # narrowed to *_proof / *_dry_run / run_hex_* so a hypothetical gate runner
    # named tools/run_*.py cannot ride as (b); anything else falls through to (a)).
    re.compile(r"^tools/run_hex_[a-z0-9_]+\.py$"),
    re.compile(r"^tools/run_[a-z0-9_]+_(proof|dry_run|harness)\.py$"),
    re.compile(r"^tests/tools/test_(hex|run)_[a-z0-9_]+\.py$"),
    re.compile(r"^tests/security/p4c_corpus/[a-z0-9_./-]+$"), # p4c corpus CASES (validator is (a))
)


def _norm(path: str) -> str:
    """Lowercase, forward-slash, strip leading ./ and slashes for stable matching."""
    p = str(path or "").strip().replace("\\", "/").lower()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _path_hits_charter_denylist(path: str) -> bool:
    """True when the source-of-truth charter explicitly denylists this path.

    Paths missing from the charter allowlist are not automatically (a) here; the
    closed (b) allowlist below decides whether they are reversible standing-sign
    candidates. Explicit denylist hits, however, must never be reclassified as
    (b), even if a local pattern would otherwise match.
    """
    try:
        decision = evaluate_paths(load_charter(), [path])
    except Exception:
        return True
    return (not decision.allowed) and decision.reason == "denylist hit"


def _path_is_a(path: str) -> bool:
    p = _norm(path)
    if not p:
        return True                              # empty/unknown -> (a) fail-closed
    if _path_hits_charter_denylist(p):
        return True
    if p in _A_EXACT:
        return True
    if any(s in p for s in _A_CODE_SUBSTR):
        return True
    if any(s in p for s in _A_CONFORMANCE_SUBSTR):
        return True
    if any(s in p for s in _A_GOV_DOC_SUBSTR):
        return True
    if any(p.startswith(pre) for pre in _A_PREFIX):
        return True
    if any(s in p for s in _A_SUBSTR):
        return True
    return False


def _path_is_b(path: str) -> bool:
    p = _norm(path)
    return bool(p) and any(rx.match(p) for rx in _B_PATTERNS)


def classify_ab(changed_paths: Sequence[str]) -> dict[str, Any]:
    """Classify a PR's changed paths as (a) or (b). FAIL-CLOSED: the PR is (b) ONLY
    if EVERY changed path is a recognized (b) category and NONE is (a). An empty or
    unrecognized path forces (a)."""
    paths = list(changed_paths or [])
    a_hits: list[str] = []
    unrecognized: list[str] = []
    if not paths:
        return {
            "ab_class": "a",
            "a_hits": [],
            "unrecognized": [],
            "reason": "no changed paths -> operator-explicit (fail-closed)",
        }
    for raw in paths:
        if _path_is_a(raw):
            a_hits.append(_norm(raw))
        elif not _path_is_b(raw):
            unrecognized.append(_norm(raw))
    if a_hits or unrecognized:
        return {
            "ab_class": "a",
            "a_hits": sorted(set(a_hits)),
            "unrecognized": sorted(set(unrecognized)),
            "reason": (
                "(a)-class or unrecognized path present -> operator-explicit "
                f"(a_hits={sorted(set(a_hits))}, unrecognized={sorted(set(unrecognized))})"
            ),
        }
    return {
        "ab_class": "b",
        "a_hits": [],
        "unrecognized": [],
        "reason": "all changed paths are recognized reversible (b)-class artifacts",
    }


def _approved(identity: Any) -> bool:
    return isinstance(identity, Mapping) and identity.get("approved") is True


def evaluate_standing_consensus_sign(
    *,
    enabled: bool,
    changed_paths: Sequence[str],
    bridge_consensus: Mapping[str, Any],
    ci_all_green: bool,
    diff_gate_allowed: bool,
    head_matches: bool,
    receipt_present: bool,
    recognized_rcos: Sequence[str] = RECOGNIZED_RCOS,
) -> dict[str, Any]:
    """Decide whether a (b)-class off-allowlist PR's operator signature is satisfied
    by the STANDING signature (best-possible-consensus). Fail-closed at every step."""
    out: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "admitted": False,
        "ab_class": None,
        "reasons": [],
        "basis": {},
    }
    if not enabled:
        out["reasons"] = ["standing-consensus-sign disabled (default-off; not self-activating)"]
        return out

    ab = classify_ab(changed_paths)
    out["ab_class"] = ab["ab_class"]
    out["a_hits"] = ab["a_hits"]
    out["unrecognized"] = ab["unrecognized"]
    if ab["ab_class"] != "b":
        out["reasons"] = [ab["reason"]]
        return out

    reasons: list[str] = []
    if not head_matches:
        reasons.append("head is not an exact match")
    bc = bridge_consensus or {}
    if not bc.get("ok"):
        reasons.append("bridge consensus not verified at head")
    ids = bc.get("identities") or {}
    if not _approved(ids.get("build_lead")):
        reasons.append("lead build_consensus missing/blocked at head")
    if not _approved(ids.get("build_tools")):
        reasons.append("tools build_consensus missing/blocked at head")
    rco_by = (ids.get("rco") or {}).get("by_agent") or {}
    for rco in recognized_rcos:
        if not _approved(rco_by.get(rco)):
            reasons.append(f"DUAL-RCO incomplete: {rco} has no approved RCO_PASS at head")
    blocking = list(bc.get("blocking_rco_agents") or [])
    if blocking:
        reasons.append(f"recognized-RCO veto present (outranks pass): {sorted(blocking)}")
    if not ci_all_green:
        reasons.append("CI not all-required-green at head")
    if not diff_gate_allowed:
        reasons.append("diff-content gate not clear")
    if not receipt_present:
        reasons.append("MAGMA receipt basis missing")

    if reasons:
        out["reasons"] = reasons
        return out

    out["admitted"] = True
    out["reasons"] = [
        "(b)-class off-allowlist PR admitted: best-possible-consensus "
        "(lead+tools build + DUAL-RCO + CI-green + no veto, head-exact) satisfies "
        "the operator signature per CLAUDE.md 9b"
    ]
    out["basis"] = {
        "class": "b",
        "operator_signature": "satisfied_by_standing_consensus_sign",
        "best_possible_consensus": True,
        "build": [str(ids.get("build_lead", {}).get("agent", "")),
                  str(ids.get("build_tools", {}).get("agent", ""))],
        "dual_rco": list(recognized_rcos),
        "head_exact": True,
        "no_rco_veto": True,
        "ci_all_green": True,
    }
    return out
