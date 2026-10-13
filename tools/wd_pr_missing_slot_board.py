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

Dormant mind mode (``--mind-snapshot`` with ``--audit-root``, explicit only)
---------------------------------------------------------------------------
It reads ONE reflected ``wd.bridge-mind.v1`` snapshot: the projector core's snapshot with the reflective
workspace's ``attention`` and ``next_question``. The file must sit in an owned ``.codex-audit`` tree. The mode
shows that snapshot's PR observations, attention and next question with their refs, and recomputes nothing.
It never imports, runs or reads the merge gate or the snapshot tool. It never calls GitHub or a provider,
never reads the canonical bridge log and never writes a bridge event. The legacy imports
(``subprocess``, ``tempfile``) do not run on this path. It infers no ready or merge state and no
gate slot. Author, recusal and RCO presence are not in the snapshot, so they stay ``UNKNOWN``.

* **Input checks.** Schema, ``authority: none`` and the bounded scope are checked. The whole input is read
  with the canonical bridge JSON reader (no duplicate keys, no NaN). Any guarded word anywhere refuses the
  whole input. The guard catches any letter case and compatibility form, before and after each
  ``\\uXXXX`` unescape.
* **Binding to refs.** Every attention item must carry refs and ``suggestion_only``. Every next-question
  entry must name an attention item and carry that item's refs. A PR suggestion must name a PR whose CI is
  green at its head, with no observed veto. Otherwise the snapshot is refused, with no partial view.
* **What counts as current.**
  * CI counts as green at the head only when it is ``reported_green`` for the PR's exact head.
  * A vote is current only when it is declared and bound to that head. Any other vote is listed as not
    current.
  * A veto observation keeps activity ``UNKNOWN``.
* **Blocked source.** A blocked source (``source.blocked`` not null) suppresses every PR suggestion and
  every question bound to one. CI-at-head then reads ``UNKNOWN``.
* **Output.** A refusal exits 2 with a fixed message and writes nothing. ``--out`` must be a new file in the
  same audit root, created exclusively.
* **Paths.** ``..``, links, junctions and hard-linked inputs are refused.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Sequence
import unicodedata

if TYPE_CHECKING:  # annotations only: the gate-audit mode imports subprocess where it runs a process
    import subprocess

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

Runner = Callable[[Sequence[str]], Any]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess:
    import subprocess  # the gate-audit mode only; the mind mode never runs a process

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
    import tempfile  # the gate-audit mode only

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


# -- Dormant mind mode: one owned reflected Mind snapshot, shown with its refs; never the gate ----------------------

MIND_SCHEMA = "wd.bridge-mind.v1"
MIND_SCOPE = "bounded_projection_not_gate"
MIND_KEYS = frozenset({"schema", "authority", "scope", "source", "prs", "lanes", "facts", "tensions", "attention",
                       "next_question"})
MIND_VIEW_SCHEMA = "wd.pr-missing-slot-board.mind-view.v1"
MIND_INPUT_LIMIT = 16 * 1024 * 1024
UNKNOWN = "UNKNOWN"
UNESCAPE_ROUNDS = 4
PR_KEYS = frozenset({"pr", "head", "base", "reported_ci", "source_refs", "class", "votes", "stale_votes",
                     "veto_observations", "missing"})
CI_KEYS = frozenset({"status", "head", "source_refs", "verification"})
CI_STATUSES = (UNKNOWN, "reported_green", "reported_not_green")
VOTE_KEYS = frozenset({"agent", "agent_uuid", "head", "status", "source_refs", "verification"})
VETO_KEYS = VOTE_KEYS | {"activity"}
ATTENTION_KEYS = frozenset({"kind", "subject", "actors", "detail", "refs", "rank", "suggestion_only"})
QUESTION_KEYS = frozenset({"question", "attention_rank", "refs"})
SOURCE_FIELDS = ("log_identity", "generation", "cursor_start", "cursor_end", "coverage", "event_count", "as_of_utc",
                 "last_event_ref")
# Not in the snapshot, so never guessed: no slot is read from an agent name, no recusal from a git author.
NOT_IN_SNAPSHOT = ("author", "recusal", "rco_presence", "gate_slots")
_REF = re.compile(r"[0-9a-f]{64}")
_HEAD = re.compile(r"[0-9a-f]{40}")
# A PR suggestion is exactly "pr:<number>"; any other spelling (say "PR:1") is refused, so it cannot slip past the
# suppression of PR suggestions on a blocked source.
_SUBJECT = re.compile(r"pr:[1-9][0-9]{0,8}|tension:[0-9a-f]{16}")
_KIND = re.compile(r"[a-z_]{1,40}")
_REASON = re.compile(r"[a-z0-9_]{1,64}")
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: a Windows junction or symlink


class MindRefused(Exception):
    """A refusal of the mind mode. Its text is a fixed phrase from this file and never echoes the input."""


def _guarded_form(text: str) -> bool:
    """A guarded word in any letter case or compatibility form, before or after each ``\\uXXXX`` unescape.

    The shared check is case-sensitive, so each form is NFKC-normalized, case-folded and upper-cased first. The
    unescape repeats until nothing changes. Text that is still changing after ``UNESCAPE_ROUNDS`` rounds counts
    as guarded, so a deeper escape cannot hide a word.
    """
    form = text
    for _ in range(UNESCAPE_ROUNDS):
        if _guarded_word(unicodedata.normalize("NFKC", form).casefold().upper()) is not None:
            return True
        unescaped = _ESCAPED.sub(lambda match: chr(int(match.group(1), 16)), form)
        if unescaped == form:
            return False
        form = unescaped
    return True


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _lstat_no_link(path: Path) -> os.stat_result | None:
    """The lstat of ``path``, or None when it does not exist. A symlink, junction or other reparse point is refused."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MindRefused("an audit path cannot be inspected") from exc
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
        raise MindRefused("an audit path is a link or junction")
    return info


def _owned_audit_path(path: Path, audit_root: Path) -> tuple[Path, os.stat_result | None]:
    """The absolute path inside the owned ``.codex-audit`` root, as the Mind core checks its own snapshot paths.

    The path has no ``..``, no link or junction on it or on any parent, and an existing file there is a regular
    file with one link (a hard link could alias a file outside the audit tree). Returns the path and its lstat.
    """
    if ".." in Path(path).parts or ".." in Path(audit_root).parts:
        raise MindRefused("an audit path holds a parent reference")
    root = Path(os.path.abspath(audit_root))
    target = Path(os.path.abspath(path))
    if root.name != ".codex-audit":
        raise MindRefused("the audit root is not an owned .codex-audit directory")
    try:
        target.relative_to(root)
    except ValueError:
        raise MindRefused("an audit path is outside the audit root") from None
    if target == root:
        raise MindRefused("an audit path is the audit root itself")
    for parent in target.parents:
        info = _lstat_no_link(parent)
        if info is None or not stat.S_ISDIR(info.st_mode):
            raise MindRefused("an audit path parent is missing or not a directory")
    info = _lstat_no_link(target)
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise MindRefused("an audit file is not a regular file with one link")
    return target, info


def read_mind_snapshot(path: Path, audit_root: Path) -> tuple[dict[str, Any], str]:
    """One reflected snapshot from the owned audit tree, read whole and guarded whole. Returns it and its sha256."""
    target, info = _owned_audit_path(path, audit_root)
    if info is None:
        raise MindRefused("the mind snapshot does not exist")
    try:
        with open(target, "rb") as handle:
            opened = os.fstat(handle.fileno())
            data = handle.read(MIND_INPUT_LIMIT + 1)
    except OSError as exc:
        raise MindRefused("the mind snapshot cannot be read") from exc
    if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1):
        raise MindRefused("the mind snapshot changed while it was opened")
    if len(data) > MIND_INPUT_LIMIT:
        raise MindRefused("the mind snapshot is larger than the board reads")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise MindRefused("the mind snapshot is not UTF-8") from None
    if _guarded_form(text):
        raise MindRefused("the mind snapshot holds a guarded word; nothing is shown")
    # The canonical bridge JSON reader (no duplicate or case-colliding keys, no NaN, safe numbers), imported here:
    # the gate-audit mode never needs it.
    from waggledance.core.bridge_log_reader import parse_bridge_json_object

    try:
        value = parse_bridge_json_object(text)
    except (ValueError, RecursionError):
        raise MindRefused("the mind snapshot is not one canonical JSON object") from None
    if any(_guarded_form(item) for item in _strings(value)):
        raise MindRefused("the mind snapshot holds a guarded word; nothing is shown")
    return value, hashlib.sha256(data).hexdigest()


def _refs(value: Any, *, unknown_ok: bool = False) -> list[str]:
    if isinstance(value, list) and value and all(isinstance(ref, str) and _REF.fullmatch(ref) for ref in value):
        return list(value)
    if unknown_ok and value == [UNKNOWN]:
        return [UNKNOWN]
    raise MindRefused("an observation has no valid event ref")


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise MindRefused("a snapshot field does not match the reflected interface")
    return value


def _signal(row: Any, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(row, dict) or set(row) != keys:
        raise MindRefused("a PR signal does not match the reflected interface")
    view = {key: _text(row[key]) for key in ("agent", "agent_uuid", "head", "status", "verification")}
    view["source_refs"] = _refs(row["source_refs"])
    return view


def _pr_view(key: str, record: Any, blocked: bool) -> dict[str, Any]:
    """One PR as the snapshot reports it. Whether CI and votes are at the PR head is compared, never assumed."""
    if not isinstance(record, dict) or set(record) != PR_KEYS:
        raise MindRefused("a PR observation does not match the reflected interface")
    number, head = record["pr"], _text(record["head"])
    if type(number) is not int or number <= 0 or str(number) != key or not (head == UNKNOWN or _HEAD.fullmatch(head)):
        raise MindRefused("a PR observation does not match the reflected interface")
    ci = record["reported_ci"]
    if not isinstance(ci, dict) or "status" not in ci or set(ci) - CI_KEYS or ci["status"] not in CI_STATUSES:
        raise MindRefused("a PR CI observation does not match the reflected interface")
    ci_head = _text(ci.get("head", UNKNOWN))
    ci_verification = _text(ci.get("verification", UNKNOWN))
    ci_refs = _refs(ci["source_refs"]) if "source_refs" in ci else [UNKNOWN]
    at_head = head != UNKNOWN
    # A blocked source is not current, so nothing in it is green at a head. Otherwise CI is green only when it was
    # reported green for this exact head. CI from an old head, or with no head, is never green here.
    green: bool | str = UNKNOWN if blocked else (at_head and ci["status"] == "reported_green" and ci_head == head
                                                 and ci_verification == "declared")
    votes = []
    for row in record["votes"] if isinstance(record["votes"], list) else [None]:
        vote = _signal(row, VOTE_KEYS)
        vote["at_head"] = UNKNOWN if blocked else (at_head and vote["head"] == head
                                                   and vote["verification"] == "declared")
        votes.append(vote)
    stale = []
    for row in record["stale_votes"] if isinstance(record["stale_votes"], list) else [None]:
        vote = _signal(row, VOTE_KEYS)
        vote["at_head"] = False  # the snapshot itself calls it stale; a stale vote is never current
        stale.append(vote)
    vetoes = []
    for row in record["veto_observations"] if isinstance(record["veto_observations"], list) else [None]:
        veto = _signal(row, VETO_KEYS)
        veto["activity"] = UNKNOWN  # an observed veto is never cleared here, whatever the snapshot says
        vetoes.append(veto)
    missing = record["missing"]
    if not isinstance(missing, list) or not all(isinstance(item, str) for item in missing):
        raise MindRefused("a PR observation does not match the reflected interface")
    view = {"pr": number, "head": head, "base": _text(record["base"]), "class": _text(record["class"]),
            "snapshot_missing": list(missing), "source_refs": _refs(record["source_refs"]),
            "ci": {"status": ci["status"], "head": ci_head, "verification": ci_verification, "source_refs": ci_refs,
                   "green_at_head": green},
            "votes": votes, "stale_votes": stale, "veto_observations": vetoes}
    view.update({name: UNKNOWN for name in NOT_IN_SNAPSHOT})
    return view


def _attention_view(items: Any) -> list[dict[str, Any]]:
    """The reflected attention items as they are: ranked 1..n, refs bound, each one a suggestion only."""
    if not isinstance(items, list):
        raise MindRefused("the attention list does not match the reflected interface")
    view = []
    for rank, item in enumerate(items, 1):
        if not isinstance(item, dict) or set(item) != ATTENTION_KEYS:
            raise MindRefused("an attention item does not match the reflected interface")
        if item["suggestion_only"] is not True:
            raise MindRefused("an attention item is not marked as a suggestion only")
        subject, kind = _text(item["subject"]), _text(item["kind"])
        if type(item["rank"]) is not int or item["rank"] != rank or not _SUBJECT.fullmatch(subject) \
                or not _KIND.fullmatch(kind):
            raise MindRefused("an attention item does not match the reflected interface")
        actors = item["actors"]
        if not isinstance(actors, list) or not all(isinstance(actor, str) for actor in actors):
            raise MindRefused("an attention item does not match the reflected interface")
        # A PR suggestion must name the events it comes from; only a tension may say its refs are UNKNOWN.
        refs = _refs(item["refs"], unknown_ok=subject.startswith("tension:"))
        view.append({"rank": rank, "kind": kind, "subject": subject, "actors": list(actors),
                     "detail": _text(item["detail"]), "refs": refs, "suggestion_only": True})
    return view


def _question_view(entry: Any, by_rank: Mapping[int, Mapping[str, Any]], suppressed: set[int]) -> tuple[dict, bool]:
    """A next question bound to its attention item by rank and refs; UNKNOWN if that item is suppressed."""
    if not isinstance(entry, dict) or set(entry) != QUESTION_KEYS:
        raise MindRefused("a next question does not match the reflected interface")
    question, rank = _text(entry["question"]), entry["attention_rank"]
    if rank is None:
        if question != UNKNOWN or entry["refs"] != [UNKNOWN]:
            raise MindRefused("a next question is not bound to an attention item")
        return {"question": UNKNOWN, "attention_rank": None, "refs": [UNKNOWN]}, False
    if type(rank) is not int or rank not in by_rank or entry["refs"] != by_rank[rank]["refs"]:
        raise MindRefused("a next question is not bound to an attention item")
    if rank in suppressed:
        return {"question": UNKNOWN, "attention_rank": None, "refs": [UNKNOWN]}, True
    return {"question": question, "attention_rank": rank, "refs": list(entry["refs"])}, False


def build_mind_view(snapshot: Any, snapshot_sha256: str) -> dict[str, Any]:
    """The board's view of one reflected snapshot. It recomputes nothing and infers no ready or merge state."""
    if not isinstance(snapshot, dict) or set(snapshot) != MIND_KEYS:
        raise MindRefused("the input is not a reflected wd.bridge-mind.v1 snapshot")
    # The snapshot is a bounded projection with no authority. One that claims anything else is not read.
    if snapshot["schema"] != MIND_SCHEMA or snapshot["authority"] != "none" or snapshot["scope"] != MIND_SCOPE:
        raise MindRefused("the snapshot claims a schema, authority or scope this board does not read")
    source = snapshot["source"]
    if not isinstance(source, dict):
        raise MindRefused("the snapshot source does not match the reflected interface")
    source_view = {name: (source[name] if isinstance(source.get(name), (str, int)) and type(source[name]) is not bool
                          else UNKNOWN) for name in SOURCE_FIELDS}
    # Missing means not known to be current: a source with no blocked field is treated like a blocked one.
    marker = source.get("blocked", UNKNOWN)
    if marker is None:
        blocked, source_view["blocked"], source_view["blocked_reason"] = False, False, None
    elif isinstance(marker, dict):
        reason = marker.get("reason")
        blocked, source_view["blocked"] = True, True
        source_view["blocked_reason"] = reason if isinstance(reason, str) and _REASON.fullmatch(reason) else UNKNOWN
    elif marker == UNKNOWN:
        blocked, source_view["blocked"], source_view["blocked_reason"] = True, UNKNOWN, UNKNOWN
    else:
        raise MindRefused("the snapshot source does not match the reflected interface")
    prs = snapshot["prs"]
    if not isinstance(prs, dict):
        raise MindRefused("the snapshot PRs do not match the reflected interface")
    pr_views = [_pr_view(key, prs[key], blocked) for key in prs]
    pr_views.sort(key=lambda view: view["pr"])
    attention = _attention_view(snapshot["attention"])
    by_number = {view["pr"]: view for view in pr_views}
    for item in attention:
        if blocked or not item["subject"].startswith("pr:"):
            continue
        # The workspace suggests a vote or signature only for CI reported green at the PR head with no observed
        # veto. A PR suggestion without that backing would show an old head as current, so nothing is shown.
        pr = by_number.get(int(item["subject"][3:]))
        if pr is None or pr["ci"]["green_at_head"] is not True or pr["veto_observations"]:
            raise MindRefused("a PR suggestion is not backed by CI green at the PR head with no observed veto")
    by_rank = {item["rank"]: item for item in attention}
    # A blocked source is not current: no PR suggestion from it is shown, and no question bound to one.
    suppressed = {item["rank"] for item in attention if blocked and item["subject"].startswith("pr:")}
    questions = snapshot["next_question"]
    if not isinstance(questions, dict) or set(questions) != {"global", "lanes"} \
            or not isinstance(questions["lanes"], dict):
        raise MindRefused("the next question does not match the reflected interface")
    global_view, global_suppressed = _question_view(questions["global"], by_rank, suppressed)
    lanes, suppressed_lanes = {}, []
    for agent in sorted(questions["lanes"]):
        lanes[agent], hidden = _question_view(questions["lanes"][agent], by_rank, suppressed)
        if hidden:
            suppressed_lanes.append(agent)
    return {
        "schema": MIND_VIEW_SCHEMA,
        "mode": "mind_snapshot",
        "observe_only": True,
        "authority": "none",
        "gate_evaluated": False,
        "snapshot_sha256": snapshot_sha256,
        "source": source_view,
        "prs": pr_views,
        "attention": [item for item in attention if item["rank"] not in suppressed],
        "next_question": {"global": global_view, "lanes": lanes},
        "suppressed_by_block": {"attention_ranks": sorted(suppressed), "global_question": global_suppressed,
                                "lane_questions": suppressed_lanes},
        "not_in_snapshot": list(NOT_IN_SNAPSHOT),
    }


def render_mind_text(view: Mapping[str, Any]) -> str:
    source = view["source"]
    lines = [f"mind snapshot {view['snapshot_sha256'][:12]} as of {source['as_of_utc']}, "
             f"coverage {source['coverage']}, source blocked {source['blocked']}: observe only, no gate run; "
             "author, recusal and gate slots UNKNOWN"]
    for pr in view["prs"]:
        groups: dict[str, list[str]] = {"at head": [], "not current": [], "head binding UNKNOWN": []}
        for vote in pr["votes"] + pr["stale_votes"]:
            group = {True: "at head", False: "not current"}.get(vote["at_head"], "head binding UNKNOWN")
            groups[group].append(f"{vote['agent']} {vote['status']}@{vote['head'][:12]}")
        vetoes = [f"{veto['agent']} {veto['status']}@{veto['head'][:12]} (activity UNKNOWN)"
                  for veto in pr["veto_observations"]]
        lines.append(f"#{pr['pr']} {pr['head'][:12]} class {pr['class']}: "
                     f"CI green at head {pr['ci']['green_at_head']}; "
                     + "; ".join(f"votes {name}: {', '.join(items) or 'none'}" for name, items in groups.items())
                     + f"; vetoes: {', '.join(vetoes) or 'none'}")
    for item in view["attention"]:
        lines.append(f"attention {item['rank']} {item['kind']} {item['subject']} [{', '.join(item['actors'])}]: "
                     f"{item['detail']}")
    hidden = view["suppressed_by_block"]["attention_ranks"]
    if hidden:
        lines.append("suppressed, the source is blocked: attention ranks " + ", ".join(str(rank) for rank in hidden))
    lines.append("next question: " + view["next_question"]["global"]["question"])
    lines.extend(f"  {agent}: {entry['question']}" for agent, entry in view["next_question"]["lanes"].items())
    return "\n".join(lines) + "\n"


def serialize_mind_view(view: Mapping[str, Any], *, as_json: bool) -> tuple[str, str]:
    """The JSON text and the text to print, each guarded whole after the view itself is guarded."""
    if any(_guarded_form(item) for item in _strings(view)):
        raise MindRefused("a guarded word reached the board view; nothing is written")
    try:
        json_text = json.dumps(view, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
        text = json_text if as_json else render_mind_text(view)
    except (TypeError, ValueError, KeyError):
        raise MindRefused("the board view cannot be serialized") from None
    if _guarded_form(json_text) or _guarded_form(text):
        raise MindRefused("a guarded word reached the board output; nothing is written")
    return json_text, text


def _write_new_owned(path: Path, audit_root: Path, text: str) -> None:
    target, info = _owned_audit_path(path, audit_root)
    if info is not None:
        raise MindRefused("the output file exists; the board never overwrites one")
    try:
        with open(target, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError:
        raise MindRefused("the output file cannot be created") from None


def mind_main(args: argparse.Namespace) -> int:
    """The dormant mind mode, only when asked for explicitly. Exit 2 on any refusal, with nothing written."""
    try:
        if args.repo is not None or args.pr or args.bridge_root is not None or args.main_sha or args.hold:
            raise MindRefused("the gate-audit flags are not read in mind mode")
        if args.mind_snapshot is None or args.audit_root is None:
            raise MindRefused("--mind-snapshot and --audit-root must be given together")
        snapshot, digest = read_mind_snapshot(args.mind_snapshot, args.audit_root)
        json_text, text = serialize_mind_view(build_mind_view(snapshot, digest), as_json=args.json)
        if args.out is not None:
            _write_new_owned(args.out, args.audit_root, json_text)
    except MindRefused as exc:
        sys.stderr.write(f"missing-slot board mind mode refused: {exc}\n")
        return 2
    sys.stdout.write(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only missing-slot board for open PRs.")
    parser.add_argument("--repo", default=None, help="Required in the default gate-audit mode.")
    parser.add_argument("--pr", type=int, action="append", default=[], help="Only these PR numbers.")
    parser.add_argument("--bridge-root", type=Path, default=None,
                        help="Passed to the gate as --bridge-root (default: the gate's own resolution).")
    parser.add_argument("--main-sha", default="", help="Current main SHA, to mark rows whose base is not main.")
    parser.add_argument("--hold", action="append", default=[], metavar="PR=REASON",
                        help="A Lead hold the gate cannot see, for example an activation dependency.")
    parser.add_argument("--out", type=Path, default=None, help="Write the board JSON here.")
    parser.add_argument("--json", action="store_true", help="Print the board JSON instead of the text summary.")
    parser.add_argument("--mind-snapshot", type=Path, default=None,
                        help="Dormant mind mode: show one reflected wd.bridge-mind.v1 snapshot from an owned "
                             ".codex-audit tree. Runs no gate, no GitHub call and no provider.")
    parser.add_argument("--audit-root", type=Path, default=None,
                        help="Mind mode only: the owned .codex-audit root that holds the snapshot and any --out.")
    args = parser.parse_args(argv)
    if args.mind_snapshot is not None or args.audit_root is not None:
        return mind_main(args)
    if args.repo is None:
        parser.error("the following arguments are required: --repo")
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
