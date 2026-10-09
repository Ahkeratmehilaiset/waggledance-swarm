# SPDX-License-Identifier: BUSL-1.1
"""Inert regional review diagnostic for one exact base..head pair (I1/I2 design).

Diagnostic only, on every path. It derives the changed-line inventory and one
conservative interaction group from git, checks supplied review evidence against
them, and reports coverage. It NEVER grants approval: every result carries
authority_effect="none", allowed_to_merge=False, approval_granted=False and
rco_pass=False, whatever the evidence says. It is not imported by any merge
gate, receipt writer or executor, and it ships no active mode: origin_policy
"deny" and mode "diagnostic" are the only accepted values, anything else refuses.

I2: the interaction group is every changed path enumerated by
``git diff --raw --no-renames`` (rename endpoints and mode-only changes
included), ordered bytewise by path, bound to base/head/tree and full blob ids.
Callers cannot choose or split groups; a supplied group manifest must match
exactly or the interaction requirement stays unmet.

D1: a path is unsupported by CONTENT (gitlink, symlink, NUL byte or invalid
UTF-8 in either blob), never by .gitattributes; unsupported paths keep the
diagnostic incomplete. Unknown or unverifiable evidence covers nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA = "wd.regional-review-diagnostic.v1"
INVENTORY_SCHEMA = "wd.regional-review-inventory.v1"
ORIGIN_POLICY_DENY = "deny"
ORIGIN_POLICIES = frozenset({ORIGIN_POLICY_DENY})   # the only implemented policy
MODE_DIAGNOSTIC = "diagnostic"
MODES = frozenset({MODE_DIAGNOSTIC})               # no active mode exists
# Must equal tools.check_rco_pass_present.DEFAULT_RCO_AGENTS (drift-guard test); not imported on purpose.
RECOGNIZED_RCOS: tuple[str, ...] = ("claude-rco-1", "claude-rco-2")
RCO_REGION = "rco_region"
GROK_REGION = "grok_region"
RCO_INTERACTION = "rco_interaction"
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
NULL_BLOB = "0" * 40
GITLINK, SYMLINK = "160000", "120000"
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
DIFF = ("diff", "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv", "--full-index")


class DiagnosticRefused(ValueError):
    """A request outside the inert contract (active mode, other origin policy, bad input)."""


def inert_fields() -> dict[str, Any]:
    """The authority fields every result carries; nothing in this module can change them."""
    return {"authority_effect": "none", "allowed_to_merge": False, "approval_granted": False,
            "rco_pass": False, "origin_policy": ORIGIN_POLICY_DENY, "mode": MODE_DIAGNOSTIC}


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if result.returncode != 0:
        raise DiagnosticRefused(f"git {' '.join(args[:2])} failed ({result.returncode}): "
                                + result.stderr.decode("utf-8", "replace").strip()[:300])
    return result.stdout


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unsupported_reason(repo: Path, entry: Mapping[str, str]) -> str | None:
    for mode in (entry["old_mode"], entry["new_mode"]):
        if mode == GITLINK:
            return "gitlink"
        if mode == SYMLINK:
            return "symlink"
    for side in ("base_blob", "head_blob"):
        blob = entry[side]
        if blob == NULL_BLOB:
            continue
        data = _git(repo, "cat-file", "blob", blob)
        if b"\0" in data:
            return f"nul_byte_in_{side}"
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return f"invalid_utf8_in_{side}"
    return None


def _changed_lines(repo: Path, base: str, head: str, path: str) -> list[dict[str, Any]]:
    """Changed lines from a -U0 --text patch: removed lines on the base side, added on the head side."""
    patch = _git(repo, *DIFF, "--text", "-U0", base, head, "--", ":(literal)" + path).decode("utf-8")
    lines = [line + "\n" for line in patch.split("\n")]
    lines[-1] = lines[-1][:-1]
    out: list[dict[str, Any]] = []
    old = new = None
    for line in lines:
        match = HUNK_RE.match(line)
        if match:
            old, new = int(match.group(1)), int(match.group(2))
            continue
        if old is None or line.startswith("\\"):
            continue
        body = line[1:]
        if body.endswith("\n"):
            body = body[:-1]
        if line.startswith("+"):
            out.append({"side": "head", "line": new, "sha256": _sha256(body.encode("utf-8"))})
            new += 1
        elif line.startswith("-"):
            out.append({"side": "base", "line": old, "sha256": _sha256(body.encode("utf-8"))})
            old += 1
    return out


def group_identity(base: str, head: str, tree: str, entries: Sequence[Mapping[str, Any]]) -> str:
    """D2: sha256 over base/head/tree and the bytewise-ordered full entry tuples."""
    rows = [[e["path"], e["status"], e["old_mode"], e["new_mode"], e["base_blob"], e["head_blob"]] for e in entries]
    canonical = json.dumps({"base": base, "head": head, "tree": tree, "entries": rows},
                           ensure_ascii=False, separators=(",", ":"))
    return _sha256(canonical.encode("utf-8"))


def build_inventory(repo: Path, base: str, head: str) -> dict[str, Any]:
    """Every changed path and line of base..head from git objects only (read-only git)."""
    base = _git(repo, "rev-parse", "--verify", base + "^{commit}").decode().strip()
    head = _git(repo, "rev-parse", "--verify", head + "^{commit}").decode().strip()
    tree = _git(repo, "rev-parse", head + "^{tree}").decode().strip()
    raw = _git(repo, *DIFF, "--raw", "--no-abbrev", "-z", base, head).split(b"\0")
    entries = []
    index = 0
    while index < len(raw) - 1:
        meta = raw[index].decode("ascii")
        path = raw[index + 1].decode("utf-8")
        old_mode, new_mode, base_blob, head_blob, status = meta[1:].split(" ")
        entries.append({"path": path, "status": status, "old_mode": old_mode, "new_mode": new_mode,
                        "base_blob": base_blob, "head_blob": head_blob})
        index += 2
    entries.sort(key=lambda e: e["path"].encode("utf-8"))
    for entry in entries:
        reason = _unsupported_reason(repo, entry)
        entry["unsupported_reason"] = reason
        entry["changed_lines"] = [] if reason else _changed_lines(repo, base, head, entry["path"])
        entry["metadata_only"] = reason is None and not entry["changed_lines"]
    return {"schema": INVENTORY_SCHEMA, "base": base, "head": head, "tree": tree,
            "group_identity": group_identity(base, head, tree, entries), "entries": entries}


def _binds(record: Mapping[str, Any], inventory: Mapping[str, Any]) -> bool:
    return all(record.get(key) == inventory[key] for key in ("base", "head", "tree"))


def _rco_record_problem(record: Mapping[str, Any], inventory: Mapping[str, Any]) -> str | None:
    reviewer = record.get("reviewer")
    if not isinstance(reviewer, str) or reviewer not in RECOGNIZED_RCOS:
        return "reviewer is not a recognized RCO"
    if not _binds(record, inventory):
        return "record is not bound to the exact base/head/tree"
    if record.get("independence_attested") is not True:
        return "no positive independence attestation"
    if record.get("participation_disclosed", []) != []:
        return "reviewer disclosed author/design/measurement participation"
    basis = record.get("eligibility_basis")
    if not isinstance(basis, list) or not basis or not all(isinstance(item, str) and item for item in basis):
        return "eligibility basis missing"
    return None


def assess(inventory: Mapping[str, Any], evidence: Mapping[str, Any] | None = None, *,
           origin_policy: str = ORIGIN_POLICY_DENY, mode: str = MODE_DIAGNOSTIC) -> dict[str, Any]:
    """Coverage diagnostic. Refuses any non-inert request; the result never carries authority."""
    if mode not in MODES:
        raise DiagnosticRefused(f"mode {mode!r} refused: only the inert diagnostic mode exists")
    if origin_policy not in ORIGIN_POLICIES:
        raise DiagnosticRefused(f"origin policy {origin_policy!r} refused: only 'deny' is implemented")
    evidence = evidence or {}
    if not isinstance(evidence, Mapping):
        raise DiagnosticRefused("evidence must be a JSON object")
    notes: list[str] = []
    covered: set[tuple[str, str, int]] = set()
    ignored: list[dict[str, Any]] = []
    known = {(e["path"], line["side"], line["line"]): line["sha256"]
             for e in inventory["entries"] for line in e["changed_lines"]}
    records = evidence.get("region_records", [])
    for position, record in enumerate(records if isinstance(records, list) else []):
        if not isinstance(record, Mapping):
            ignored.append({"index": position, "reason": "record is not an object"})
            continue
        kind = record.get("kind")
        if kind == GROK_REGION:
            ignored.append({"index": position, "reason": "origin_policy=deny: external records never count"})
            continue
        if kind != RCO_REGION:
            ignored.append({"index": position, "reason": f"unknown record kind {kind!r}"})
            continue
        problem = _rco_record_problem(record, inventory)
        if problem:
            ignored.append({"index": position, "reason": problem})
            continue
        lines = record.get("lines")
        if not isinstance(lines, list):
            ignored.append({"index": position, "reason": "lines missing"})
            continue
        for item in lines:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str) \
                    or not isinstance(item.get("side"), str) or type(item.get("line")) is not int:
                continue
            key = (item["path"], item["side"], item["line"])
            # A line counts only if it is in the git inventory and the reviewed content hash matches.
            if key in known and item.get("sha256") == known[key]:
                covered.add(key)
    if not isinstance(records, list):
        notes.append("region_records is not a list; nothing counted")
    unsupported = [{"path": e["path"], "reason": e["unsupported_reason"]}
                   for e in inventory["entries"] if e["unsupported_reason"]]
    uncovered = sorted(set(known) - covered, key=lambda k: (k[0].encode("utf-8"), k[1], k[2]))
    interaction = _interaction_status(inventory, evidence)
    content_complete = not uncovered and not unsupported
    result = {"schema": SCHEMA, **inert_fields(), "base": inventory["base"], "head": inventory["head"],
              "tree": inventory["tree"], "group_identity": inventory["group_identity"],
              "paths": len(inventory["entries"]), "changed_lines": len(known), "covered_lines": len(covered),
              "uncovered_lines": len(uncovered), "uncovered_examples": [list(k) for k in uncovered[:20]],
              "unsupported_paths": unsupported, "ignored_records": ignored, "interaction": interaction,
              "content_complete": content_complete,
              "diagnostic_complete": content_complete and interaction["complete"], "notes": notes}
    result.update(inert_fields())   # last word: no input can set authority
    return result


def _interaction_status(inventory: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    status: dict[str, Any] = {"group_identity": inventory["group_identity"], "members": len(inventory["entries"]),
                              "complete": False, "reasons": []}
    manifest = evidence.get("group_manifest")
    if manifest is not None:
        expected = [[e["path"], e["status"], e["old_mode"], e["new_mode"], e["base_blob"], e["head_blob"]]
                    for e in inventory["entries"]]
        if not isinstance(manifest, Mapping) or manifest.get("entries") != expected \
                or manifest.get("group_identity") != inventory["group_identity"]:
            status["reasons"].append("supplied group manifest differs from the git-derived group (omit/add/reorder/abbreviation)")
            return status
    records = evidence.get("interaction_records", [])
    valid = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, Mapping) or record.get("kind") != RCO_INTERACTION:
            continue
        if record.get("group_identity") != inventory["group_identity"]:
            status["reasons"].append("interaction record for another or caller-split group ignored")
            continue
        if _rco_record_problem(record, inventory):
            status["reasons"].append("interaction record without a valid recognized-RCO binding ignored")
            continue
        valid.append(record.get("reviewer"))
    if not valid:
        status["reasons"].append("no whole-group interaction review evidence")
        return status
    if any(e["unsupported_reason"] for e in inventory["entries"]):
        status["reasons"].append("group has unsupported members")
        return status
    status["complete"] = True
    status["reviewers"] = sorted(set(valid))
    return status


def fresh_recheck(repo: Path, result: Mapping[str, Any], ref: str) -> dict[str, Any]:
    """Refusal-only freshness check: refuses when ref moved off the assessed head or the group changed.

    Passing it grants nothing; the returned record is as inert as the diagnostic itself.
    """
    current = build_inventory(repo, str(result.get("base", "")), ref)
    if current["head"] != result.get("head"):
        raise DiagnosticRefused(f"head moved: assessed {result.get('head')}, now {current['head']}")
    if current["tree"] != result.get("tree") or current["group_identity"] != result.get("group_identity"):
        raise DiagnosticRefused("tree or interaction group changed since the assessment")
    return {"schema": SCHEMA, **inert_fields(), "still_current": True, "head": current["head"]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--origin-policy", default=ORIGIN_POLICY_DENY)
    parser.add_argument("--mode", default=MODE_DIAGNOSTIC)
    args = parser.parse_args(argv)
    try:
        if args.mode not in MODES or args.origin_policy not in ORIGIN_POLICIES:
            assess({"entries": []}, origin_policy=args.origin_policy, mode=args.mode)   # raises the refusal
        evidence = json.loads(args.evidence.read_text(encoding="utf-8")) if args.evidence else {}
        result = assess(build_inventory(args.repo, args.base, args.head), evidence,
                        origin_policy=args.origin_policy, mode=args.mode)
    except (DiagnosticRefused, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"schema": SCHEMA, **inert_fields(), "refused": True, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0   # a computed diagnostic, complete or not; never a permission


if __name__ == "__main__":
    sys.exit(main())
