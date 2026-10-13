# SPDX-License-Identifier: BUSL-1.1
"""Read-only missing-slot board: what each open PR still lacks before it can merge.

For every open PR (or the ``--pr`` numbers given) the board takes a ``tools/pr_status_snapshot.py``
snapshot and runs the merge gate ``tools/idle_consensus_auto_merge.py`` on it in its dry-run form
(``--require-bridge-consensus``, never ``--apply``). It then sorts the gate's own blockers into the
missing element:

* ``author``: the gate cannot resolve the PR author.
* ``draft`` / ``merge_state``: the PR is a draft, or GitHub does not report it mergeable.
* ``ci_pending`` / ``ci_failed``: named checks that are not green, split by the snapshot's check state.
* ``veto`` / ``block``: an unretracted block on the canonical task, from a recognized RCO or from
  another lane.
* ``build_lead`` / ``build_tools`` / ``rco``: no head-bound approval in that consensus slot.
* ``signature``: the charter path gate or diff gate refuses (denylist, off-allowlist, or a code
  pattern), so the operator's explicit per-PR signature is needed after consensus.
* ``accepted_queue``: the gate cannot see the whole accepted bridge queue in canonical history. This
  is a runtime condition, not a fault of the PR.
* ``hold``: a hold that Lead passes with ``--hold``, for a dependency the gate cannot see (for
  example an activation that must happen first).
* ``other``: any gate blocker this board does not recognize, kept verbatim. Nothing is dropped.

When the snapshot tool itself refuses a PR, the gate is not run, and the row gets the element ``base``
(a stale base: the PR needs a base update) or ``snapshot`` (any other refusal). For these rows only
the refusal's decision name is recorded, never its text. A privacy refusal names the guard's own
marker, and that word must not travel on in a report that may be posted to the bridge.

In a dry run the gate always asks for a receipt bundle. That is a merge-time artifact, not a missing
review element, so it is reported separately. Each row ends in one of six states:

* ``merge_ready``: nothing is missing. The gate itself would still require the receipt.
* ``signature_only``: the operator signature is the only missing element. These rows form the
  morning signature list.
* ``held``: only Lead holds are missing.
* ``signature_after_consensus``: a signature is needed, but other elements are missing too.
* ``snapshot_refused``: the snapshot tool refused the PR, so the gate could not be evaluated.
* ``in_progress``: anything else.

Observe-only: the board posts no bridge event and merges nothing. It writes only ``--out``, plus
per-PR snapshot files in a temporary directory that it removes. It is not on the verdict path: the
gate decides, and the board only reports what the gate said. A PR whose snapshot or gate output
cannot be read becomes a row error, never a silent skip, and the exit code is 1.

PR titles, branch names, gate texts, hold reasons and error tails all come from outside the board.
Every string it writes is checked after decoding: a string that holds a guarded private-marker word,
plain or ``\\uXXXX``-escaped, is replaced by a fixed placeholder, and the row counts it in
``withheld``. The word itself is never written. Classification runs before this, so a withheld text
keeps its element and state.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The shared guarded-word check, imported so this file carries no marker literal: the charter diff gate
# refuses those literals, and a copied word list could drift from the shared one.
from tools.bridge_diff_privacy import _marker as _guarded_word  # noqa: E402

SNAPSHOT_TOOL = REPO_ROOT / "tools" / "pr_status_snapshot.py"
GATE_TOOL = REPO_ROOT / "tools" / "idle_consensus_auto_merge.py"
SCHEMA = "wd.pr-missing-slot-board.v1"
RECEIPT_BLOCKERS = ("receipt_bundle_path is required before merge",)
PASS_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
CONSENSUS_PREFIX = "bridge consensus incomplete: "
DECISION_NAME = re.compile(r"[a-z0-9_]{1,64}")
WITHHELD = "[withheld: holds a guarded word]"
_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")
# Exact gate texts and prefixes -> element. Order matters only for prefixes that nest.
EXACT = {
    "PR must not be a draft": "draft",
    "status checks snapshot is required before merge": "ci_pending",
    "missing exact-head RCO_PASS from recognized non-author RCO": "rco",
    CONSENSUS_PREFIX + "author_agent must be an exact canonical agent id": "author",
}
PREFIXES = (
    ("PR author resolution requires operator review: ", "author"),
    ("mergeable state is not clean: ", "merge_state"),
    ("path gate failed: ", "signature"),
    ("diff gate failed: ", "signature"),
    ("accepted bridge queue is not fully visible in canonical history: ", "accepted_queue"),
    ("unresolved peer bridge block", "block"),
    (CONSENSUS_PREFIX + "recognized RCO veto blocks consensus: ", "veto"),
    (CONSENSUS_PREFIX + "build_lead (", "build_lead"),
    (CONSENSUS_PREFIX + "build_tools (", "build_tools"),
    (CONSENSUS_PREFIX + "rco (", "rco"),
)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(list(command), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=False, timeout=600)


def snapshot_command(python: str, number: int, repo: str, out: Path) -> list[str]:
    return [python, "-B", str(SNAPSHOT_TOOL), str(number), "--repo", repo, "--out", str(out), "--json"]


def gate_command(python: str, snapshot: Path, head: str, task_id: str, repo: str,
                 bridge_root: Path | None) -> list[str]:
    """The gate's dry-run form only: no --apply, no artifact or receipt output, no opt-in admission paths."""
    command = [python, "-B", str(GATE_TOOL), "--pr-status-file", str(snapshot), "--expected-head", head,
               "--consensus-proposal-id", task_id, "--bridge-task-id", task_id, "--repo", repo,
               "--require-bridge-consensus", "--json"]
    if bridge_root is not None:
        command += ["--bridge-root", str(bridge_root)]
    return command


def _element_of(blocker: str) -> str | None:
    if blocker in EXACT:
        return EXACT[blocker]
    for prefix, element in PREFIXES:
        if blocker.startswith(prefix):
            return element
    return None


def _check_state(snapshot: Mapping[str, Any]) -> dict[str, str]:
    states: dict[str, str] = {}
    for check in snapshot.get("checks") or []:
        if not isinstance(check, Mapping):
            continue
        name = str(check.get("name", ""))
        status = str(check.get("status", "")).upper()
        conclusion = str(check.get("conclusion", "") or check.get("state", "")).upper()
        if status and status != "COMPLETED":
            states[name] = "pending"
        elif conclusion in PASS_CONCLUSIONS:
            states[name] = "pass"
        elif not conclusion or conclusion in {"PENDING", "EXPECTED", "QUEUED"}:
            states[name] = "pending"
        else:
            states[name] = "failed"
    return states


def _approved_by(consensus: Mapping[str, Any]) -> list[str]:
    identities = consensus.get("identities") or {}
    holders = []
    for role in ("build_lead", "build_tools"):
        slot = identities.get(role) or {}
        if slot.get("approved") is True and slot.get("agent"):
            holders.append(f"{role}:{slot['agent']}")
    for agent, slot in sorted(((identities.get("rco") or {}).get("by_agent") or {}).items()):
        if isinstance(slot, Mapping) and slot.get("approved") is True:
            holders.append(f"rco:{agent}")
    return holders


def _state(elements: set[str]) -> str:
    if not elements:
        return "merge_ready"
    if elements == {"signature"}:
        return "signature_only"
    if elements == {"hold"}:
        return "held"
    if "signature" in elements:
        return "signature_after_consensus"
    return "in_progress"


def classify(gate: Mapping[str, Any], snapshot: Mapping[str, Any], holds: Sequence[str] = ()) -> dict[str, Any]:
    """Sort every gate blocker into exactly one missing element, a merge-time item, or ``other``."""
    consensus = gate.get("bridge_consensus") or {}
    recognized = set(consensus.get("recognized_rco_agents") or [])
    eligible = [agent for agent in consensus.get("eligible_rco_agents") or [] if isinstance(agent, str)]
    checks = _check_state(snapshot)
    author = str((gate.get("author_resolution") or {}).get("author_agent") or "") or None
    missing: dict[str, dict[str, Any]] = {}
    merge_time: list[str] = []
    other: list[str] = []

    def add(element: str, detail: str, actors: Sequence[str] = ()) -> None:
        entry = missing.setdefault(element, {"element": element, "details": [], "actors": []})
        if detail not in entry["details"]:
            entry["details"].append(detail)
        for actor in actors:
            if actor and actor not in entry["actors"]:
                entry["actors"].append(actor)

    for raw in gate.get("reasons") or []:
        blocker = str(raw)
        if blocker in RECEIPT_BLOCKERS:
            merge_time.append(blocker)
            continue
        if blocker.startswith("status checks not green: "):
            for name in [part.strip() for part in blocker.split(": ", 1)[1].split(",") if part.strip()]:
                add("ci_failed" if checks.get(name) == "failed" else "ci_pending", name)
            continue
        element = _element_of(blocker)
        if element is None:
            other.append(blocker)
        elif element == "block":
            latest = (gate.get("bridge_peer_gate") or {}).get("latest_blocking_event") or {}
            agent = str(latest.get("agent") or "")
            add("veto" if agent in recognized else "block", blocker, [agent])
        elif element == "veto":
            add("veto", blocker, consensus.get("blocking_rco_agents") or [])
        elif element == "rco":
            add("rco", blocker, [agent for agent in eligible if agent != author])
        elif element == "build_lead":
            add("build_lead", blocker, ["codex-lead-1"])
        elif element == "build_tools":
            add("build_tools", blocker, ["codex-tools-1"])
        elif element == "signature" and blocker.startswith("path gate failed: "):
            path_gate = gate.get("path_gate") or {}
            paths = list(path_gate.get("blocked_paths") or []) + list(path_gate.get("unmatched_paths") or [])
            add("signature", blocker + (" (" + ", ".join(paths) + ")" if paths else ""), ["operator"])
        elif element == "signature":
            add("signature", blocker, ["operator"])
        elif element in ("author", "draft", "merge_state"):
            add(element, blocker, [author] if author and element == "draft" else [])
        else:
            add(element, blocker)
    for reason in holds:
        add("hold", reason, ["codex-lead-1"])
    if other:
        missing["other"] = {"element": "other", "details": other, "actors": []}
    return {"state": _state(set(missing)), "missing": [missing[key] for key in sorted(missing)], "merge_time": merge_time,
            "approved": _approved_by(consensus), "gate_decision": gate.get("decision"), "author": author}


def _has_marker(text: str) -> bool:
    """A guarded word in the text, or in its ``\\uXXXX``-unescaped form (for example an echoed raw JSON line)."""
    unescaped = _ESCAPED.sub(lambda match: chr(int(match.group(1), 16)), text)
    return _guarded_word(text) is not None or _guarded_word(unescaped) is not None


def _withhold(value: Any) -> tuple[Any, int]:
    """Replace every string that holds a guarded word with the placeholder; return the value and the count."""
    if isinstance(value, str):
        return (WITHHELD, 1) if _has_marker(value) else (value, 0)
    if isinstance(value, list):
        items = [_withhold(item) for item in value]
        return [item for item, _ in items], sum(count for _, count in items)
    if isinstance(value, dict):
        if any(_has_marker(str(key)) for key in value):
            raise ValueError("a board key holds a guarded word; nothing is written")
        items = {key: _withhold(item) for key, item in value.items()}
        return {key: item for key, (item, _) in items.items()}, sum(count for _, count in items.values())
    return value, 0


def _read_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _snapshot_refusal(done: subprocess.CompletedProcess) -> str | None:
    """The snapshot tool's refusal decision name, if its output is one refusal object; never its text."""
    for stream in (done.stdout, done.stderr):
        value = _read_json_object((stream or "").strip())
        if value is not None and value.get("ok") is False:
            decision = value.get("decision")
            if isinstance(decision, str) and DECISION_NAME.fullmatch(decision):
                return decision
    return None


def board_row(pr: Mapping[str, Any], repo: str, workdir: Path, bridge_root: Path | None,
              run: Runner, python: str, holds: Sequence[str] = ()) -> dict[str, Any]:
    number = int(pr["number"])
    head = str(pr.get("headRefOid", ""))
    task_id = str(pr.get("headRefName", ""))
    row: dict[str, Any] = {"pr": number, "branch": task_id, "head": head, "title": str(pr.get("title", "")),
                           "is_draft": pr.get("isDraft")}
    snapshot_path = workdir / f"pr-{number}.json"
    snap = run(snapshot_command(python, number, repo, snapshot_path))
    snapshot = _read_json_object(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.exists() else None
    refusal = _snapshot_refusal(snap) if snap.returncode != 0 else None
    if refusal is not None:
        missing = [{"element": "base" if refusal == "stale_base_ref" else "snapshot", "details": [refusal],
                    "actors": []}]
        missing += [{"element": "hold", "details": list(holds), "actors": ["codex-lead-1"]}] if holds else []
        row.update({"state": "snapshot_refused", "missing": missing, "merge_time": [], "approved": [],
                    "gate_decision": None, "author": None})
        return row
    if snap.returncode != 0 or snapshot is None:
        row["error"] = "snapshot failed: " + (snap.stderr or snap.stdout or "no output").strip()[-400:]
        return row
    if snapshot.get("head_sha") != head:
        head = str(snapshot.get("head_sha", ""))
        row["head"] = head
    row["base_sha"] = snapshot.get("base_sha")
    gate_run = run(gate_command(python, snapshot_path, head, task_id, repo, bridge_root))
    gate = _read_json_object(gate_run.stdout)
    if gate is None or "decision" not in gate or not isinstance(gate.get("reasons", []), list):
        row["error"] = "gate output unreadable: " + (gate_run.stderr or gate_run.stdout or "no output").strip()[-400:]
        return row
    row.update(classify(gate, snapshot, holds))
    return row


def build_board(prs: Sequence[Mapping[str, Any]], repo: str, bridge_root: Path | None, run: Runner | None = None,
                python: str = sys.executable, main_sha: str = "",
                holds: Mapping[int, Sequence[str]] | None = None) -> dict[str, Any]:
    run = run or _run
    holds = holds or {}
    with tempfile.TemporaryDirectory(prefix="wd-slot-board-") as tmp:
        rows = [board_row(pr, repo, Path(tmp), bridge_root, run, python, holds.get(int(pr["number"]), ()))
                for pr in prs]
    withheld = []
    for index, row in enumerate(rows):
        if main_sha and row.get("base_sha"):
            row["base_is_main"] = row["base_sha"] == main_sha
        clean, count = _withhold(row)
        if count:
            clean["withheld"] = count
            withheld.append(clean["pr"])
        rows[index] = clean
    signature_list = [{"pr": row["pr"], "head": row["head"], "title": row["title"],
                       "signature_detail": row["missing"][0]["details"], "approved": row["approved"]}
                      for row in rows if row.get("state") == "signature_only"]
    board, _ = _withhold({
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": repo,
        "main_sha": main_sha or None,
        "gate_tool_sha256": hashlib.sha256(GATE_TOOL.read_bytes()).hexdigest(),
        "observe_only": True,
        "rows": rows,
        "merge_ready": [row["pr"] for row in rows if row.get("state") == "merge_ready"],
        "held": [row["pr"] for row in rows if row.get("state") == "held"],
        "snapshot_refused": [row["pr"] for row in rows if row.get("state") == "snapshot_refused"],
        "signature_list": signature_list,
        "withheld": withheld,
        "errors": [row["pr"] for row in rows if "error" in row],
    })
    return board


def list_open_prs(repo: str, run: Runner | None = None) -> list[dict[str, Any]]:
    done = (run or _run)(["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "200", "--json",
                "number,headRefName,headRefOid,isDraft,title"])
    value = json.loads(done.stdout) if done.returncode == 0 else None
    if not isinstance(value, list):
        tail = (done.stderr or "").strip()[-400:]
        raise SystemExit("gh pr list failed: " + (WITHHELD if _has_marker(tail) else tail))
    return sorted(value, key=lambda pr: int(pr["number"]))


def render_text(board: Mapping[str, Any]) -> str:
    lines = []
    for row in board["rows"]:
        if "error" in row:
            lines.append(f"#{row['pr']} ERROR {row['error']}")
            continue
        parts = [entry["element"] + ("(" + ",".join(entry["actors"]) + ")" if entry["actors"] else "")
                 for entry in row["missing"]]
        lines.append(f"#{row['pr']} {row['head'][:8]} {row['state']}: " + (" ".join(parts) or "nothing missing"))
    lines.append("signature list: " + (", ".join(f"#{item['pr']}" for item in board["signature_list"]) or "none"))
    lines.append("merge ready: " + (", ".join(f"#{pr}" for pr in board["merge_ready"]) or "none"))
    lines.append("held: " + (", ".join(f"#{pr}" for pr in board["held"]) or "none"))
    lines.append("snapshot refused: " + (", ".join(f"#{pr}" for pr in board["snapshot_refused"]) or "none"))
    lines.append("texts withheld: " + (", ".join(f"#{pr}" for pr in board["withheld"]) or "none"))
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only missing-slot board for open PRs.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, action="append", default=[], help="Only these PR numbers.")
    parser.add_argument("--bridge-root", type=Path, default=None,
                        help="Passed to the gate as --bridge-root (default: the gate's own resolution).")
    parser.add_argument("--main-sha", default="", help="Current main SHA, to mark rows whose base is not main.")
    parser.add_argument("--hold", action="append", default=[], metavar="PR=REASON",
                        help="A Lead hold the gate cannot see, for example an activation dependency.")
    parser.add_argument("--out", type=Path, default=None, help="Write the board JSON here.")
    parser.add_argument("--json", action="store_true", help="Print the board JSON instead of the text summary.")
    args = parser.parse_args(argv)
    holds: dict[int, list[str]] = {}
    for item in args.hold:
        number, sep, reason = item.partition("=")
        if not sep or not number.strip().isdigit() or not reason.strip():
            parser.error(f"--hold needs PR=REASON, got {item!r}")
        holds.setdefault(int(number), []).append(reason.strip())
    prs = list_open_prs(args.repo)
    if args.pr:
        wanted = set(args.pr)
        prs = [pr for pr in prs if int(pr["number"]) in wanted]
    board = build_board(prs, args.repo, args.bridge_root, main_sha=args.main_sha, holds=holds)
    text = json.dumps(board, indent=2, sort_keys=False) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text if args.json else render_text(board))
    return 1 if board["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
