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

Entry points (dict in / dict out):
  classify_ab(changed_paths)            -> mechanical (a)/(b) classification, pure.
  evaluate_standing_consensus_sign(...) -> admission verdict (admitted bool), pure.

Only the optional git_context keyword does I/O (classify_ab, and the evaluator,
which forwards it only when enabled and supplied and keeps only the classifier's
own evidence): read-only Git child processes for the narrowly verified new-test
exception. The context is exactly {base, head, base_tree, head_tree, target,
git, cwd}: full lowercase
SHA-1 IDs, an absolute path to the Git executable (never a bare name resolved
through the current directory or PATH) and the absolute repository directory.
base must be the one merge base of target and head, so the proof covers the
whole change against the target, not a later slice of the branch. target is
caller-supplied: tools.pr_status_snapshot.build_added_test_context derives it
from the live PR snapshot's base-branch tip (and base from the compare merge
base), and this reader re-verifies both against local objects. Every
read disables replacement objects, repository grafts (GIT_GRAFT_FILE is the null
device), the commit-graph (core.commitGraph=false) and lazy fetching
(GIT_NO_LAZY_FETCH=1; a Git that predates the variable ignores it), and runs
with an environment built from fixed values only: no GIT_DIR, common-dir,
object, alternate, replace, graft, shallow, namespace or config variable, HOME
or XDG location of the caller reaches the child, and system and global Git
configuration are off. A shallow repository, or one with any partial-clone or
promisor key in its configuration, is refused before any ancestry evidence is
read. The repository's own configuration, alternates and objects still apply:
the trust root is the cwd repository, which must be the gate's own trusted
clone, never a PR-controlled checkout. At most 32 paths and 4 MiB per Git
output are accepted (a validation limit, not a memory ceiling), each read has a 10-second
timeout. A failed proof returns exactly the legacy path-only result; a returned
(b) grants no approval, signature, merge or runtime authority. The auto-merge
gate, the receipt plan and the merge executor take the trusted repository and
Git tool only as explicit API arguments (added_test_repository/added_test_git;
no CLI switch, default None keeps the legacy path): the gate re-derives a fresh
context, the receipt records the gate's evidence, and the fresh gate before a
merge must reproduce that evidence exactly. Cached snapshot class, status or
context is never proof, and nothing here turns standing-sign on.

Scope and limits. Threading standing_consensus_sign through the receipt plan
and the merge executor is Rule 9b wiring onto the merge runtime path: it stays
API-only and default False, a change that wires it is class (a) and needs the
operator's explicit signature, and no production caller or CLI enables it (the
one pre-existing enabling call is the plan-only replay canary, apply=False).
Purpose fit is limited: this added-test proof reaches only the dormant 9b
evaluator; the Rule 12 and charter consumers still classify path-only, so an
operational consumer that lifts the per-PR signature for an added-tests-only PR
is UNKNOWN here, not claimed. With the proof configured, any updated_at, check
or base-tip move between the plan and the fresh gate blocks the whole merge
(conservative; re-plan). The trusted clone must already hold the PR head and
merge-base objects: nothing fetches, and a missing object fails closed to the
legacy result. The evaluator records the cwd and Git tool the proof ran with
(git_provenance), and the receipt keeps it next to the evidence.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
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


# git diff hides a changed gitlink under diff.ignoreSubmodules or
# submodule.<name>.ignore=all: every diff refuses that filter (defense in depth;
# the full-tree comparison in the reader is the oracle).
_GIT_SUBMODULE_CONFIG = ("-c", "diff.ignoreSubmodules=none")
_DIFF_SUBMODULES = ("--ignore-submodules=none",)
_GIT_CHILD_FIXED = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull,
                    "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
                    "GIT_OPTIONAL_LOCKS": "0"}


def _measured_git_env(git: str) -> dict[str, str]:
    """The whole environment of a measured Git child: built, never inherited.

    Only fixed values and the folder of the explicit Git executable go in (plus
    the Windows system root a Windows child needs), so a caller's GIT_DIR,
    GIT_COMMON_DIR, object/alternate/replace/graft/shallow/namespace/config
    variables, HOME and XDG locations never reach Git. The repository's own
    info/grafts file is replaced by the null device and lazy fetching is off.
    """
    env = dict(_GIT_CHILD_FIXED)
    folder = os.path.dirname(git)
    if os.name == "nt":
        root = os.environ.get("SYSTEMROOT") or r"C:\Windows"
        env.update({"SYSTEMROOT": root, "WINDIR": root, "PATHEXT": ".COM;.EXE;.BAT;.CMD",
                    "PATH": os.pathsep.join((folder, os.path.join(root, "System32"), root))})
    else:
        env["PATH"] = os.pathsep.join((folder, "/usr/bin", "/bin"))
    return env


def _verified_added_test_diff(
    paths: Sequence[str], context: Mapping[str, str],
) -> dict[str, Any] | None:
    """Re-derive a narrow exception from exact immutable Git objects, never labels.

    This optional reader is NOT wired into any admission caller. Unknown input
    returns None so the unchanged path classifier remains authoritative. The
    bound is a validation limit, not a physical peak-memory promise.
    """
    required = {"base", "head", "base_tree", "head_tree", "target", "git", "cwd"}
    if not isinstance(context, Mapping) or set(context) != required:
        return None
    if not all(type(context[k]) is str and re.fullmatch(r"[0-9a-f]{40}", context[k])
               for k in required - {"git", "cwd"}):
        return None
    cwd = context["cwd"]
    if type(cwd) is not str or not os.path.isabs(cwd) or any(ord(c) < 32 for c in cwd):
        return None
    git = context["git"]
    if (type(git) is not str or not os.path.isabs(git) or any(ord(c) < 32 for c in git)
            or os.path.basename(git).casefold() not in ("git", "git.exe")):
        return None
    if (not paths or len(paths) > 32 or not all(type(p) is str for p in paths)
            or len(set(paths)) != len(paths)):
        return None
    reserved = ("fixture", "corpus", "corpora", "conformance", "hook", "config",
                "import", "conftest", "sitecustomize", "usercustomize")
    for path in paths:
        if (type(path) is not str
                or re.fullmatch(r"tests/(?:[a-z0-9_][a-z0-9_-]*/)*test_[a-z0-9_][a-z0-9_-]*\.py", path) is None
                or "windows" in path.split("/")
                or any(re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])", part)
                       for part in path.split("/"))
                or any(word in path for word in reserved)
                or _path_is_a(path)):
            return None

    limit = 4 * 1024 * 1024
    prefix = [git, "--no-pager", "--no-replace-objects", "--literal-pathspecs", "-c", "core.commitGraph=false",
              *_GIT_SUBMODULE_CONFIG]
    env = _measured_git_env(git)

    def read(*args: str) -> bytes:
        completed = subprocess.run(prefix + list(args), cwd=cwd,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, check=True, timeout=10, env=env)
        data = completed.stdout
        if type(data) is not bytes or len(data) > limit:
            raise ValueError("missing or oversized Git evidence")
        return data

    try:
        # A shallow file rewrites parents like grafts do, and a promisor remote
        # could lazily fetch a missing object over the network: refuse both
        # before any ancestry evidence is read.
        if read("rev-parse", "--is-shallow-repository") != b"false\n":
            return None
        config = read("config", "--null", "--list")
        if not config or not config.endswith(b"\0"):
            return None
        for entry in config.split(b"\0")[:-1]:
            key = entry.split(b"\n", 1)[0].decode("utf-8").casefold()
            if (key == "extensions.partialclone"
                    or re.fullmatch(r"remote\..+\.(?:promisor|partialclonefilter)", key, re.S)):
                return None
        base, head = context["base"], context["head"]
        for commit, tree in ((base, context["base_tree"]), (head, context["head_tree"])):
            if read("rev-parse", "--verify", commit + "^{commit}") != (commit + "\n").encode():
                return None
            if read("rev-parse", "--verify", commit + "^{tree}") != (tree + "\n").encode():
                return None
        target = context["target"]
        if read("rev-parse", "--verify", target + "^{commit}") != (target + "\n").encode():
            return None
        # base must be THE merge base of target and head: head^ of a longer
        # branch, or any other ancestor, would prove only a slice of the change.
        if read("merge-base", "--all", target, head) != (base + "\n").encode():
            return None
        # Exact lowercase ls-tree absence is insufficient on case-insensitive
        # filesystems. Inspect the entire immutable base namespace, including
        # ancestor directories, rather than trust caller path labels.
        base_names = read("ls-tree", "-r", "--name-only", "-z", base, "--")
        if base_names and not base_names.endswith(b"\0"):
            return None
        base_paths = [name.decode("utf-8") for name in base_names.split(b"\0")[:-1]]
        if len(set(base_paths)) != len(base_paths):
            return None
        prefixes: dict[str, set[str]] = {}

        def folded(parts: Sequence[str]) -> str:
            # Windows also aliases trailing spaces/dots; never normalize a new
            # candidate into acceptance. Candidate spellings were checked above.
            return "/".join(part.rstrip(" .").casefold() for part in parts)

        for old in base_paths:
            components = old.split("/")
            if (not old or old.startswith("/") or "\\" in old
                    or any(p in ("", ".", "..") for p in components)
                    or any(ord(c) < 32 for c in old)):
                return None
            for end in range(1, len(components) + 1):
                original = "/".join(components[:end])
                prefixes.setdefault(folded(components[:end]), set()).add(original)
        base_file_names = set(base_paths)
        for path in paths:
            components = path.split("/")
            for end in range(1, len(components) + 1):
                original = "/".join(components[:end])
                existing = prefixes.get(folded(components[:end]), set())
                if existing and (end == len(components) or existing != {original}
                                 or original in base_file_names):
                    return None
        raw = read("diff", *_DIFF_SUBMODULES, "--no-ext-diff", "--no-textconv", "--find-renames", "--find-copies",
                   "--find-copies-harder", "--raw", "--abbrev=40", "-z", base, head, "--")
        fields = raw.split(b"\0")
        if not raw or fields[-1] != b"" or len(fields) != len(paths) * 2 + 1:
            return None
        entries = []
        for index in range(0, len(fields) - 1, 2):
            match = re.fullmatch(rb":000000 100644 0{40} ([0-9a-f]{40}) A", fields[index])
            path = fields[index + 1].decode("ascii")
            if match is None or path not in paths:
                return None
            oid = match[1].decode("ascii")
            if oid == "0" * 40:
                return None
            entries.append((path, oid))
        if len({p for p, _ in entries}) != len(paths) or {p for p, _ in entries} != set(paths):
            return None
        names = read("diff", *_DIFF_SUBMODULES, "--no-ext-diff", "--no-textconv", "--find-renames", "--find-copies",
                     "--find-copies-harder", "--name-status", "-z", base, head, "--")
        if names != b"".join(b"A\0" + p.encode() + b"\0" for p, _ in entries):
            return None
        # A gitlink change hidden from git diff still changes the tree: the full
        # base and head listings must differ by exactly the candidate blobs.
        listings = []
        for commit in (base, head):
            listing = read("ls-tree", "-r", "-z", commit, "--")
            if listing and not listing.endswith(b"\0"):
                return None
            items = [item.partition(b"\t") for item in listing.split(b"\0")[:-1]]
            if any(not tab for _, tab, _ in items) or len({name for _, _, name in items}) != len(items):
                return None
            listings.append({name: meta for meta, _, name in items})
        expected_head = dict(listings[0])
        expected_head.update({p.encode(): f"100644 blob {oid}".encode() for p, oid in entries})
        if listings[1] != expected_head or len(expected_head) != len(listings[0]) + len(entries):
            return None
        actual_patch = read("diff", *_DIFF_SUBMODULES, "--no-color", "--no-ext-diff", "--no-textconv",
                            "--no-renames", "--full-index", "--src-prefix=a/",
                            "--dst-prefix=b/", "--no-relative", base, head, "--")
        expected_patch = b""
        for path, oid in entries:
            if read("ls-tree", "-z", base, "--", path) != b"":
                return None
            expected_tree = f"100644 blob {oid}\t{path}".encode() + b"\0"
            if read("ls-tree", "-z", head, "--", path) != expected_tree:
                return None
            data = read("cat-file", "blob", oid)
            blob_id = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False)
            if b"\0" in data or blob_id.hexdigest() != oid:
                return None
            data.decode("utf-8")
            expected_patch += (f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
                               f"index {'0' * 40}..{oid}\n").encode()
            if data:
                lines = data.split(b"\n")
                terminated = data.endswith(b"\n")
                if terminated:
                    lines.pop()
                extent = "1" if len(lines) == 1 else f"1,{len(lines)}"
                expected_patch += f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +{extent} @@\n".encode()
                expected_patch += b"".join(b"+" + line + b"\n" for line in lines)
                if not terminated:
                    expected_patch += b"\\ No newline at end of file\n"
            if len(expected_patch) > limit:
                return None
        if actual_patch != expected_patch:
            return None
        return {"base": base, "head": head, "base_tree": context["base_tree"],
                "head_tree": context["head_tree"], "target": target, "paths": [p for p, _ in entries],
                "blobs": dict(entries),
                "base_paths_sha256": hashlib.sha256(base_names).hexdigest(),
                "diff_sha256": hashlib.sha256(actual_patch).hexdigest(),
                "status": "rederived_git_added_regular_tests"}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return None


def classify_ab(
    changed_paths: Sequence[str], *, git_context: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Classify a PR's changed paths as (a) or (b). FAIL-CLOSED: the PR is (b) ONLY
    if EVERY changed path is a recognized (b) category and NONE is (a). An empty or
    unrecognized path forces (a). An explicit exact-object git_context may
    prove the bounded added-test-only exception; failed proof preserves the
    exact legacy result. No caller-supplied added/status boolean is accepted.
    """
    paths = list(changed_paths or [])
    if git_context is not None:
        evidence = _verified_added_test_diff(paths, git_context)
        if evidence is not None:
            return {"ab_class": "b", "a_hits": [], "unrecognized": [],
                    "reason": "exact Git diff only adds verified regular new tests",
                    "git_evidence": evidence}
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
    git_context: Mapping[str, str] | None = None,
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

    ab = (classify_ab(changed_paths) if git_context is None else
          classify_ab(changed_paths, git_context=git_context))
    out["ab_class"] = ab["ab_class"]
    out["a_hits"] = ab["a_hits"]
    out["unrecognized"] = ab["unrecognized"]
    if isinstance(ab.get("git_evidence"), Mapping):
        # Preserve the actual classifier proof, never caller/snapshot labels.
        out["git_evidence"] = dict(ab["git_evidence"])
        # The trust root the proof ran in: the context's absolute cwd and Git tool.
        out["git_provenance"] = {"cwd": str(git_context["cwd"]), "git": str(git_context["git"])}
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
