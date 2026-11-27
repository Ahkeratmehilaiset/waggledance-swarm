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
* admission requires the FULL best-possible-consensus (lead+tools build, two
  distinct eligible recognized RCOs, CI all-green, no recognized-RCO veto, head-exact,
  diff-clean, MAGMA receipt). Any missing/ambiguous element -> operator_review.

Two pure entry points (no side effects, dict in / dict out):
  classify_ab(changed_paths)            -> mechanical (a)/(b)/Mind classification.
  evaluate_standing_consensus_sign(...) -> admission verdict (admitted bool).
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from waggledance.core.idle_consensus_charter import evaluate_paths, load_charter

REPORT_VERSION = "wd.standing_consensus_sign_class.v0"

RECOGNIZED_RCOS = ("claude-rco-1", "claude-rco-2", "claude-rco-3")

from tools.bridge_rco_enrollment import (
    load_rco_enrollment_context,
    rco_event_enrollment_matches,
)

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
    # Option-B product gate hard-excludes: these are execution, egress, secret,
    # storage, provenance, or gate surfaces and must stay operator-explicit even
    # as neighboring read-only product paths become (b)-class.
    "waggledance/adapters/http/routes/solvers.py",
    "waggledance/core/v3_13_0/air01_sensor_http_transport.py",
    "waggledance/core/v3_13_0/eng01_price_feed_http_transport.py",
    "waggledance/core/v3_13_0/ssrf_host_guard.py",
    "waggledance/core/v3_13_0/credential_vault.py",
    "waggledance/core/v3_13_0/secret_markers.py",
    "waggledance/core/v3_13_0/write_rco_gate.py",
    "waggledance/core/v3_13_0/solver_provenance.py",
    "waggledance/core/v3_13_0/sqlite_read_transport.py",
    "waggledance/core/v3_13_0/doc_ingest.py",
})
# Verdict-computing gate code + bridge gate scripts + THIS module (self).
_A_CODE_SUBSTR = (
    "idle_consensus_auto_merge",
    "verify_bridge_consensus",
    "check_bridge_changes_requested",
    "check_rco_pass_present",
    "bridge_rule12_review_eligibility",      # Rule 12 evaluator, read by the gate
    "rule12_gate_wiring",                    # Rule 12 gate-wiring conformance anchor
    "rule12_grok_ledger_adapter",            # Grok consultations the gate reads (2026-10-08)
    "rule12_grok_gate_wiring",               # Grok RCO-slot gate-wiring conformance anchor
    "merge_with_bridge_receipt",
    "write_bridge_consensus_merge_receipt",
    "idle_consensus_charter",
    "check_proven_safe_autosign_class",
    "check_standing_consensus_sign_class",   # self -> no self-activation
    "bridge_event_taxonomy",                 # wired into the live veto consumer
    "bridge_event_writer",                   # canonical writer, including rename targets
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
    "waggledance/core/autonomy/",
)
_A_SUBSTR = ("auth", "secret", "token", "credential", ".env")

# ----------------------------------------------------------------------------
# (b)-class: recognized REVERSIBLE artifacts that MAY ride standing-sign. This is
# a CLOSED allowlist of categories. Anything matching neither (a) nor (b) falls
# through to (a) -- "when in doubt, operator-explicit" (fail-closed).
# ----------------------------------------------------------------------------
_B_EXACT = frozenset({
    # Read-only product routes. The solver execution/admission route is excluded
    # above and must remain (a)-class.
    "waggledance/adapters/http/routes/air01_advisory.py",
    "waggledance/adapters/http/routes/eng01_advisory.py",
    "waggledance/adapters/http/routes/eng06_advisory.py",
    "waggledance/adapters/http/routes/advisory_dashboard.py",
    # Snapshot refreshers/ticker delegate egress to hard-excluded transports.
    "waggledance/adapters/feeds/air01_advisory_refresher.py",
    "waggledance/adapters/feeds/eng01_advisory_refresher.py",
    "waggledance/adapters/feeds/eng06_advisory_refresher.py",
    "waggledance/adapters/feeds/advisory_refresh_ticker.py",
    # Product CLIs.
    "waggledance/adapters/cli/acct01_reconcile_bills.py",
    "waggledance/adapters/cli/air01_advisory.py",
    "waggledance/adapters/cli/email01_classify_inbox.py",
    "waggledance/adapters/cli/email02_index_vendor_emails.py",
    "waggledance/adapters/cli/eng01_recommend.py",
    "waggledance/adapters/cli/eng06_fireplace.py",
    "waggledance/adapters/cli/fin10_classify_receipts.py",
    "waggledance/adapters/cli/pdf01_extract_invoice.py",
    # Network-free v3.13.0 solver/card/parser/adapter modules from spec v3.
    "waggledance/core/v3_13_0/acct01_unpaid_bill_reconciler.py",
    "waggledance/core/v3_13_0/air01_air_quality_advisor.py",
    "waggledance/core/v3_13_0/air01_digheran_adapter.py",
    "waggledance/core/v3_13_0/email01_inbox_priority_classifier.py",
    "waggledance/core/v3_13_0/email02_vendor_email_indexer.py",
    "waggledance/core/v3_13_0/eng01_advisory_card.py",
    "waggledance/core/v3_13_0/eng01_price_feed_response_parser.py",
    "waggledance/core/v3_13_0/eng01_spot_electricity.py",
    "waggledance/core/v3_13_0/eng06_advisory_card.py",
    "waggledance/core/v3_13_0/eng06_burn_log_adapter.py",
    "waggledance/core/v3_13_0/eng06_fireplace_advisor.py",
    "waggledance/core/v3_13_0/fin10_receipt_classifier.py",
    "waggledance/core/v3_13_0/pdf01_invoice_field_extractor.py",
})
_B_PATTERNS = (
    re.compile(r"^docs/runs/[a-z0-9_./-]+\.md$"),             # sprint boards / run logs
    re.compile(r"^docs/benchmarks/[a-z0-9_./-]+\.md$"),       # perf docs (non-gate)
    re.compile(r"^docs/benchmarks/[a-z0-9_./-]+\.json$"),     # benchmark result artifacts
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
    return bool(p) and (p in _B_EXACT or any(rx.match(p) for rx in _B_PATTERNS))


def classify_ab(changed_paths: Sequence[str]) -> dict[str, Any]:
    """Classify a PR's changed paths as (a), (b) or Mind-class. FAIL-CLOSED: the PR is (b) ONLY
    if EVERY changed path is a recognized (b) category and NONE is (a). An empty or
    unrecognized path forces (a).

    Rule 14: a path list that is wholly Mind-class by ``classify_mind_class``
    (every path matches a documented pattern; none is (a)-class or Stage-2) is
    reported as ``ab_class == "mind"``. That is never (a)-class (Rule 14a: no
    operator signature) and never (b): the standing sign admits only ``"b"``.
    Every result carries ``mind_class``. A mixed list, and any list with an
    (a)-class path, keeps exactly the result it had before this class existed.
    """
    paths = list(changed_paths or [])
    a_hits: list[str] = []
    unrecognized: list[str] = []
    if not paths:
        return {
            "ab_class": "a",
            "a_hits": [],
            "unrecognized": [],
            "mind_class": False,
            "reason": "no changed paths -> operator-explicit (fail-closed)",
        }
    for raw in paths:
        if _path_is_a(raw):
            a_hits.append(_norm(raw))
        elif not _path_is_b(raw):
            unrecognized.append(_norm(raw))
    mind = classify_mind_class(paths)
    mind_class = mind["mind_class"] is True and not a_hits
    if mind_class and unrecognized:
        return {
            "ab_class": "mind",
            "a_hits": [],
            "unrecognized": [],
            "mind_class": True,
            "mind_matches": list(mind["matches"]),
            "reason": (
                "every changed path is Mind-class (Rule 14 path list) -> Rule 14a: "
                "not (a)-class, no per-PR operator signature; not (b) for the "
                "standing sign"
            ),
        }
    if a_hits or unrecognized:
        return {
            "ab_class": "a",
            "a_hits": sorted(set(a_hits)),
            "unrecognized": sorted(set(unrecognized)),
            "mind_class": False,
            "reason": (
                "(a)-class or unrecognized path present -> operator-explicit "
                f"(a_hits={sorted(set(a_hits))}, unrecognized={sorted(set(unrecognized))})"
            ),
        }
    return {
        "ab_class": "b",
        "a_hits": [],
        "unrecognized": [],
        "mind_class": mind_class,
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
    # Two distinct eligible identities, not every member of an expanding roster.
    # The verified upstream report supplies author/eligibility; never count its
    # author or an unknown/ineligible reviewer as the second voice. Real upstream
    # reports always carry both fields; absence is not legacy approval evidence.
    author = bc.get("author_agent")
    if not isinstance(author, str) or re.fullmatch(r"[a-z][a-z0-9_-]{1,32}", author) is None:
        reasons.append("verified consensus author is missing or invalid")
    enrollment_context = load_rco_enrollment_context()
    approved_rcos = [
        rco for rco in dict.fromkeys(recognized_rcos)
        if rco in RECOGNIZED_RCOS and rco != author
        and _approved(rco_by.get(rco))
        and rco_by[rco].get("eligible") is True
        and (
            rco != "claude-rco-3"
            or rco_event_enrollment_matches(
                rco_by[rco].get("enrollment_event") or {},
                context=enrollment_context,
            )
        )
    ]
    if len(approved_rcos) < 2:
        reasons.append("DUAL-RCO incomplete: two distinct eligible non-author RCO_PASS identities required at head")
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
        "dual_rco": approved_rcos[:2],
        "head_exact": True,
        "no_rco_veto": True,
        "ci_all_green": True,
    }
    return out


# ----------------------------------------------------------------------------
# Rule 14 Mind-class: the documented whole-file path list, checked mechanically.
# SEPARATE from classify_ab, whose output is unchanged. FAIL-CLOSED: a diff is
# Mind-class only when EVERY changed path is an exact plain repository path that
# matches one documented pattern as a whole file, NONE is (a)-class or Stage-2,
# and (when the diff text is given) the git file headers of the diff name exactly
# the same paths. Nothing is normalised into an allowed path. Prose never widens
# this list; a test pins it to the list in CLAUDE.md Rule 14.
# ----------------------------------------------------------------------------
MIND_CLASS_REPORT_VERSION = "wd.mind_class_paths.v1"
MIND_CLASS_PATTERNS = (
    "sandbox/mind/**",
    "configs/mind/**",
    "tools/wd_q4_*",
    "tests/tools/test_wd_q4_*",
    "ops/windows/reboot/Get-WdQ4*.ps1",
    "ops/windows/reboot/Invoke-WdQ4*.ps1",
    "tools/wd_mind_*",
    "tools/wd_bridge_mind*",
    "tools/bridge_mind_workspace.py",
    "tests/tools/test_wd_mind_*",
    "tests/tools/test_wd_bridge_mind*",
    "tests/tools/test_mind_*",
    "tests/tools/test_bridge_mind_workspace.py",
    "tests/windows/wd_mind_*",
    "ops/windows/reboot/Get-WdMind*.ps1",
    "ops/windows/reboot/Invoke-WdMind*.ps1",
    "ops/windows/reboot/Start-WdMind*.ps1",
)


def _mind_pattern_regex(pattern: str) -> re.Pattern[str]:
    """``**`` spans directories, ``*`` stays inside one path segment, the rest is literal."""
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            parts.append(".+")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(parts))


_MIND_CLASS_REGEXES = tuple(
    (pattern, _mind_pattern_regex(pattern)) for pattern in MIND_CLASS_PATTERNS
)
_MIND_PLAIN_PATH_RE = re.compile(r"[A-Za-z0-9._/-]+")
_MIND_STAGE2_SUBSTR = ("stage2", "stage-2", "stage_2", "cutover", "atomic_flip", "atomic-flip")
_MIND_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_DIFF_SPECIAL_ENTRY_RE = re.compile(
    r"(?:new file mode|deleted file mode|old mode|new mode) (?:120000|160000)"
    r"|index [0-9a-f]+[.][.][0-9a-f]+ (?:120000|160000)"
)


def _mind_path_refusal(path: Any) -> str:
    """Why ``path`` is not an exact plain repository file path ('' when it is one)."""
    if type(path) is not str or not path:
        return "path is empty or not a string"
    if _MIND_PLAIN_PATH_RE.fullmatch(path) is None:
        return "path has a character outside A-Z a-z 0-9 . _ / -"
    if path.startswith("/") or path.endswith("/"):
        return "path is absolute or names a directory"
    for segment in path.split("/"):
        if segment in ("", ".", ".."):
            return "path has an empty, dot or dot-dot segment"
        if segment.endswith("."):
            return "path segment ends with a dot"
        if segment.split(".", 1)[0].lower() in _MIND_RESERVED_STEMS:
            return "path segment is a reserved device name"
    return ""


_DIFF_HUNK_HEADER_RE = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(?: .*)?")
_DIFF_INDEX_LINE_RE = re.compile(r"index ([0-9a-f]{7,64})[.][.]([0-9a-f]{7,64})(?: ([0-7]{6}))?")
_DIFF_MODE_LINE_RE = re.compile(r"(new file mode|deleted file mode|old mode|new mode) ([0-7]{6})")
# A diff shows an entry's file mode only on these lines. An exact rename or copy
# has none of them, so its mode (a symlink, for example) cannot be read at all.
_DIFF_REGULAR_MODES = frozenset({"100644", "100755"})
_DIFF_SIMILARITY_LINE_RE = re.compile(r"(dis)?similarity index (\d{1,3})%")
_DIFF_EMPTY_BLOB_SHA1 = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
_DIFF_FILE_LINE_PREFIXES = (
    "index ", "--- ", "+++ ", "new file mode ", "deleted file mode ", "old mode ",
    "new mode ", "similarity index ", "dissimilarity index ", "rename from ",
    "rename to ", "copy from ", "copy to ", "Binary files ",
)


def _diff_new_entry() -> dict[str, Any]:
    return {
        "lines": 0, "old_side": False, "new_side": False, "hunks": 0,
        "binary": False, "needs_index": False, "index": False, "content_changed": False,
        "needs_content": False, "mode": False, "old_mode": False, "new_mode": False,
    }


def _diff_entry_error(entry: Mapping[str, Any]) -> str:
    """Why one file entry of a diff is structurally incomplete, or empty when it is whole."""
    if not entry["lines"]:
        return "a file entry has no line after its git header"
    if entry["old_side"] != entry["new_side"]:
        return "a file entry has only one of its two side lines"
    if entry["old_side"] and not entry["hunks"]:
        return "a file entry names its sides but has no hunk"
    if entry["old_mode"] != entry["new_mode"]:
        return "a file entry has only one of its two mode lines"
    if not entry["mode"] and not entry["old_mode"]:
        return (
            "a file entry does not show a regular-file mode "
            "(an exact rename or copy cannot be checked)"
        )
    if entry["needs_index"] and not entry["index"]:
        return "an added or deleted file entry has no index line"
    if (
        (entry["content_changed"] or entry["needs_content"])
        and not entry["hunks"]
        and not entry["binary"]
    ):
        return "a file entry changes content but has neither a hunk nor a binary note"
    return ""


def _diff_entry_line_error(entry: dict[str, Any], line: str) -> str:
    """Record one file-header line of the current entry; non-empty when it is out of place."""
    entry["lines"] += 1
    if line.startswith("--- "):
        if entry["old_side"]:
            return "a file entry repeats a side line"
        entry["old_side"] = True
    elif line.startswith("+++ "):
        if not entry["old_side"] or entry["new_side"]:
            return "a file entry has its side lines out of order"
        entry["new_side"] = True
    elif entry["old_side"]:
        return "a file entry has a header line after its side lines"
    elif line.startswith("Binary files "):
        entry["binary"] = True
    elif line.startswith(("new file mode ", "deleted file mode ", "old mode ", "new mode ")):
        match = _DIFF_MODE_LINE_RE.fullmatch(line)
        if match is None or match.group(2) not in _DIFF_REGULAR_MODES:
            return "a file entry has an unreadable or non-regular file mode"
        kind = match.group(1)
        seen = "old_mode" if kind == "old mode" else "new_mode" if kind == "new mode" else "mode"
        if entry[seen]:
            return "a file entry repeats a mode line"
        entry[seen] = True
        entry["needs_index"] = entry["needs_index"] or seen == "mode"
    elif line.startswith(("similarity index ", "dissimilarity index ")):
        match = _DIFF_SIMILARITY_LINE_RE.fullmatch(line)
        if match is None:
            return "a file entry has an unreadable similarity line"
        # Anything but an exact rename or copy must show its content change.
        entry["needs_content"] = bool(match.group(1)) or match.group(2) != "100"
    elif line.startswith("index "):
        match = _DIFF_INDEX_LINE_RE.fullmatch(line)
        if match is None or entry["index"]:
            return "a file entry has an unreadable or repeated index line"
        old, new, mode = match.groups()
        if mode is not None and mode not in _DIFF_REGULAR_MODES:
            return "a file entry has an unreadable or non-regular file mode"
        entry["index"] = True
        entry["mode"] = entry["mode"] or mode is not None
        empty_add = set(old) == {"0"} and _DIFF_EMPTY_BLOB_SHA1.startswith(new)
        empty_delete = set(new) == {"0"} and _DIFF_EMPTY_BLOB_SHA1.startswith(old)
        entry["content_changed"] = old != new and not empty_add and not empty_delete
    return ""


def _diff_header_paths(diff_text: Any) -> dict[str, Any]:
    """Paths named by the git file headers of a unified diff, read exactly.

    ``error`` is non-empty when the text cannot prove the complete file list:
    no header at all, text before the first header, a quoted or unreadable
    header, a rename or copy header without matching from/to lines, a symlink
    or submodule entry, a line that is neither a known file-header line nor a
    hunk, a hunk whose line count differs from its ``@@`` header, a file entry
    that shows no regular-file mode (an exact rename or copy), a file entry
    that is structurally incomplete (no header line, one side line, side lines
    without a hunk, a content change or an inexact rename without a hunk or
    binary note), or text
    that does not end with a newline. Hunk lines are consumed by their declared
    count, so hunk content is never read as a header. A cut exactly between two
    files is left to the caller's comparison with the changed-path list; a cut
    inside the very last line cannot be seen at all.
    """
    result: dict[str, Any] = {"paths": set(), "error": ""}
    if type(diff_text) is not str:
        result["error"] = "diff text is not a string"
        return result
    if not diff_text.endswith("\n"):
        result["error"] = "the diff does not end with a newline"
        return result
    paths: set[str] = result["paths"]
    headers = 0
    pending = ""    # "a/X b/Y" of a header whose sides differ, until from/to lines confirm it
    source = ""
    old_left = new_left = 0
    entry = _diff_new_entry()
    for line in diff_text[:-1].split("\n"):
        if old_left or new_left:
            marker = line[:1]
            if marker == " ":
                old_left -= 1
                new_left -= 1
            elif marker == "-":
                old_left -= 1
            elif marker == "+":
                new_left -= 1
            elif marker != "\\":
                result["error"] = "a hunk ends before its declared length"
                return result
            if old_left < 0 or new_left < 0:
                result["error"] = "a hunk has more lines than its header declares"
                return result
            continue
        if line.startswith("diff --git "):
            if pending:
                result["error"] = "a rename or copy header has no matching from/to lines"
                return result
            if headers:
                result["error"] = _diff_entry_error(entry)
                if result["error"]:
                    return result
            headers += 1
            source = ""
            entry = _diff_new_entry()
            rest = line[len("diff --git "):]
            half, odd = divmod(len(rest) - 5, 2)
            same = rest[2:2 + half] if half > 0 and not odd else ""
            if same and rest == f"a/{same} b/{same}":
                paths.add(same)
            else:
                pending = rest
            continue
        if not headers:
            result["error"] = "the diff has text before its first git file header"
            return result
        hunk = _DIFF_HUNK_HEADER_RE.fullmatch(line)
        if hunk is not None:
            if pending or not entry["new_side"]:
                result["error"] = "a hunk comes before its file entry is complete"
                return result
            old_left = 1 if hunk.group(1) is None else int(hunk.group(1))
            new_left = 1 if hunk.group(2) is None else int(hunk.group(2))
            entry["hunks"] += 1
            continue
        if entry["hunks"]:
            if line.startswith("\\ "):
                continue    # "no newline at end of file" note after a hunk's last line
            result["error"] = "a hunk has more lines than its header declares"
            return result
        if not line.startswith(_DIFF_FILE_LINE_PREFIXES):
            result["error"] = "the diff has a line that is neither a file header nor a hunk"
            return result
        if _DIFF_SPECIAL_ENTRY_RE.fullmatch(line) is not None:
            result["error"] = "the diff has a symlink or submodule entry"
            return result
        result["error"] = _diff_entry_line_error(entry, line)
        if result["error"]:
            return result
        if not pending:
            continue
        for word in ("rename", "copy"):
            if line.startswith(word + " from "):
                source = line[len(word) + 6:]
            elif line.startswith(word + " to ") and source:
                target = line[len(word) + 4:]
                if pending != f"a/{source} b/{target}":
                    result["error"] = "a rename or copy header disagrees with its from/to lines"
                    return result
                paths.update((source, target))
                pending = ""
                source = ""
    if old_left or new_left:
        result["error"] = "the diff ends inside a hunk"
    elif pending:
        result["error"] = "a rename or copy header has no matching from/to lines"
    elif not headers:
        result["error"] = "the diff has no git file header"
    else:
        result["error"] = _diff_entry_error(entry)
    return result


def classify_mind_class(changed_paths: Any, diff_text: Any = None) -> dict[str, Any]:
    """Mechanical Rule 14 Mind-class check of a whole diff. FAIL-CLOSED.

    ``mind_class`` is True only when every changed path is Mind-class. With
    ``diff_text`` the git headers of the diff must also name exactly the changed
    paths (``diff_checked``); the merge gate requires that. ``classify_ab``
    reports a wholly Mind-class path list from this function's path verdict.
    """
    out: dict[str, Any] = {
        "report_version": MIND_CLASS_REPORT_VERSION,
        "mind_class": False,
        "reason": "",
        "patterns": list(MIND_CLASS_PATTERNS),
        "matches": [],
        "refused": [],
        "diff_checked": False,
        "only_in_diff": [],
        "only_in_changed_paths": [],
    }
    if not isinstance(changed_paths, (list, tuple)) or not changed_paths:
        out["reason"] = "changed paths must be a non-empty list -> not Mind-class (fail-closed)"
        return out
    seen: set[str] = set()
    for raw in changed_paths:
        refusal = _mind_path_refusal(raw)
        if not refusal:
            if raw.lower() in seen:
                refusal = "path repeats or aliases another changed path"
            seen.add(raw.lower())
        if not refusal and _path_is_a(raw):
            refusal = "(a)-class path"
        if not refusal and any(part in raw.lower() for part in _MIND_STAGE2_SUBSTR):
            refusal = "Stage-2 path"
        pattern = "" if refusal else next(
            (name for name, regex in _MIND_CLASS_REGEXES if regex.fullmatch(raw) is not None), ""
        )
        if not refusal and not pattern:
            refusal = "no documented Mind-class pattern matches the whole file path"
        if refusal:
            shown = raw if type(raw) is str else repr(raw)
            out["refused"].append({"path": shown[:300], "reason": refusal})
        else:
            out["matches"].append({"path": raw, "pattern": pattern})
    if out["refused"]:
        out["reason"] = "not every changed path is Mind-class: " + "; ".join(
            f"{row['path']!r}: {row['reason']}" for row in out["refused"]
        )
        return out
    if diff_text is None:
        out["mind_class"] = True
        out["reason"] = (
            "every changed path matches a documented Mind-class pattern "
            "(diff headers not checked)"
        )
        return out
    header = _diff_header_paths(diff_text)
    if header["error"]:
        out["reason"] = "the diff cannot confirm the changed paths: " + header["error"]
        return out
    listed = {row["path"] for row in out["matches"]}
    out["only_in_diff"] = sorted(header["paths"] - listed)
    out["only_in_changed_paths"] = sorted(listed - header["paths"])
    if out["only_in_diff"] or out["only_in_changed_paths"]:
        out["reason"] = "the diff headers and the changed paths name different files"
        return out
    out["diff_checked"] = True
    out["mind_class"] = True
    out["reason"] = (
        "every changed path matches a documented Mind-class pattern and the diff "
        "headers name the same files"
    )
    return out
