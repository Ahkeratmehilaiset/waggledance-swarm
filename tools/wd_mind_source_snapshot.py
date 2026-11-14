"""Opt-in in-memory whole-event screening; not a snapshot writer or attestation.

This conservative core accepts complete caller-supplied JSONL lines only. It
does no file, network, bridge or publishing I/O and never mutates a source.
The caller must independently anchor policies and source bytes before using a
result. An accepted event retains its original line and canonical event hash;
neither that hash nor roster consistency authenticates the sender or proves
membership in a real bridge log. Identifier coverage, including unlisted names,
and completeness of excluded evaluation material remain UNKNOWN.

Schema support is intentionally narrow. Unknown fields and any free text that
the existing content policy would alter or reject exclude the entire event. In
particular, a clean message does not exempt a nested payload from screening.
"""
from __future__ import annotations

import hashlib
import re

from tools import wd_mind_content_query_readonly as content
from waggledance.core import bridge_log_reader as bridge

MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_VALUE_NODES = 65536
MAX_DEPTH = 32
MAX_FLEET_BYTES = 65536
_REQUIRED = frozenset(("ts_utc", "agent", "agent_uuid", "type", "task_id", "message"))
_OPTIONAL = frozenset(("status", "severity", "to", "paths", "write_scope", "run_id", "pid",
                       "cwd", "payload", "role", "session_id", "capabilities"))
_PAYLOAD_KEYS = frozenset(("summary", "detail", "notes", "items", "metadata"))
_BUILD_LANES = frozenset(("codex-lead-1", "codex-tools-1", "fable-5"))
_BRIDGE_UTC = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,7})?(?:Z|\+00:00)\Z"
)


class SourceScreenRefused(ValueError):
    """Fixed diagnostic, with no input or private policy in its message."""


def _excluded():
    return {"status": "excluded", "authority": "none", "reason": "event_excluded",
            "privacy_coverage": "UNKNOWN", "source_membership": "UNKNOWN",
            "evaluation_exclusion_completeness": "UNKNOWN"}


def _unchanged_text(value, private, *, empty=False, limit=content.MAX_MESSAGE_CHARS):
    if type(value) is not str:
        return False
    if value == "":
        return empty
    verdict, encoded = content._classify_with_policy(value, limit, private)
    if verdict != "KEEP":
        return False
    try:
        # KEEP alone is insufficient: that API can redact a secret, path or URL.
        # Only a byte-equivalent text value is eligible for the original row.
        return content.decode_text(encoded) == value
    except Exception:
        return False


def _payload_safe(value, private):
    stack = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_VALUE_NODES or depth > MAX_DEPTH:
            return False
        if type(current) is dict:
            for key, child in current.items():
                if type(key) is not str or key not in _PAYLOAD_KEYS:
                    return False
                stack.append((child, depth + 1))
        elif type(current) is list:
            stack.extend((child, depth + 1) for child in current)
        elif type(current) is str:
            if not _unchanged_text(current, private, empty=True):
                return False
        elif current is None or type(current) is bool:
            continue
        else:
            return False
    return True


def _fleet_worktrees(fleet_bytes, fleet_sha256, roster):
    """Bind exact lane strings to caller-pinned fleet bytes and the roster.

    The caller must independently establish the fleet pin and approve its
    contents. This proves consistency only, not sender or fleet authenticity.
    """
    if fleet_bytes is None and fleet_sha256 is None:
        return {}
    fleet = content._pinned(fleet_bytes, fleet_sha256, limit=MAX_FLEET_BYTES)
    if (type(fleet) is not dict or type(fleet.get("schema_version")) is not int
            or fleet["schema_version"] != 2 or type(fleet.get("lanes")) is not list
            or len(fleet["lanes"]) != 4 or type(fleet.get("tools_supervisor")) is not dict):
        raise ValueError("invalid_fleet")
    entries = fleet["lanes"] + [fleet["tools_supervisor"]]
    worktrees = {}
    for entry in entries:
        if type(entry) is not dict:
            raise ValueError("invalid_fleet")
        agent, identity, path = entry.get("agent"), entry.get("agent_uuid"), entry.get("worktree")
        if (type(agent) is not str or agent not in roster or agent in worktrees
                or identity != roster[agent] or type(path) is not str
                or not re.fullmatch(r"[A-Za-z]:\\(?:[A-Za-z0-9_.-]+\\)*[A-Za-z0-9_.-]+", path)
                or any(part in (".", "..") or part.endswith(".") for part in path[3:].split("\\"))):
            raise ValueError("invalid_fleet")
        worktrees[agent] = path
    if (set(worktrees) != set(roster) or fleet["tools_supervisor"].get("agent") != "codex-tools-1"
            or len({path.casefold() for path in worktrees.values()}) != len(worktrees)):
        raise ValueError("invalid_fleet")
    return worktrees


def _safe_exact_cwd(value, agent, worktrees, private):
    """Except only the exact pinned worktree, retaining other content checks."""
    if agent not in _BUILD_LANES or type(value) is not str or value != worktrees.get(agent):
        return False
    try:
        if content._normalize(value, content.MAX_MESSAGE_CHARS) != value:
            return False
        # The exception is for location syntax alone; still reject credentials,
        # private identifiers, sealed material and other unsafe path components.
        content._screen(value, identifier_policy=private)
        return all(_unchanged_text(part, private) for part in value[3:].split("\\"))
    except Exception:
        return False


def _event_safe(event, policy, roster, private, excluded_tasks, ref, worktrees):
    if type(event) is not dict or not _REQUIRED <= set(event) or not set(event) <= _REQUIRED | _OPTIONAL:
        return False
    if type(event["ts_utc"]) is not str or not _BRIDGE_UTC.fullmatch(event["ts_utc"]):
        return False
    if event.get("task_id") in excluded_tasks or ref in policy["removed_source_refs"]:
        return False
    if "approved_source_refs" in policy and ref not in policy["approved_source_refs"]:
        return False
    fields = content._fields(event, policy, roster)
    if type(fields) is not dict:
        return False
    if (not _unchanged_text(event["message"], private, limit=policy["max_message_chars"])
            or not _unchanged_text(event["task_id"], private)):
        return False
    if "to" in event:
        recipients = event["to"]
        if type(recipients) is str:
            recipients = [recipients] if recipients else []
        if type(recipients) is not list or any(type(item) is not str or item not in _BUILD_LANES
                                                for item in recipients):
            return False
    if "payload" in event and (type(event["payload"]) is not dict or not _payload_safe(event["payload"], private)):
        return False
    if "cwd" in event and not _safe_exact_cwd(event["cwd"], event["agent"], worktrees, private):
        return False
    for key in ("severity", "run_id", "role", "session_id"):
        if key in event and not _unchanged_text(event[key], private, empty=True):
            return False
    for key in ("paths", "write_scope", "capabilities"):
        if key in event and (type(event[key]) is not list or
                any(not _unchanged_text(item, private) for item in event[key])):
            return False
    if "pid" in event and (type(event["pid"]) is not int or not 0 <= event["pid"] <= 999999):
        return False
    return True


def screen_line(line, *, enabled=False, roster_bytes=None, roster_sha256=None,
                policy_bytes=None, policy_sha256=None, identifier_policy_binding=None,
                excluded_tasks=None, fleet_bytes=None, fleet_sha256=None):
    """Screen one original JSONL line; return its exact bytes only on acceptance.

    All controls are explicit in-memory inputs. Their matching digests are not
    operator authorization. Rejected events yield no source ref or content.
    """
    try:
        if enabled is not True or type(excluded_tasks) not in (tuple, frozenset):
            raise ValueError("config")
        if len(excluded_tasks) > 10000 or any(type(item) is not str or not content._task(item)
                                             for item in excluded_tasks):
            raise ValueError("config")
        roster_document = content._pinned(roster_bytes, roster_sha256)
        if content._canonical(roster_document) != roster_bytes:
            raise ValueError("config")
        roster = content._roster(roster_document)
        worktrees = _fleet_worktrees(fleet_bytes, fleet_sha256, roster)
        policy = content._message_policy(content._pinned(policy_bytes, policy_sha256), None)
        if type(identifier_policy_binding) is not tuple or len(identifier_policy_binding) != 3:
            raise ValueError("config")
        private = content._load_identifier_policy(*identifier_policy_binding)
    except Exception:
        raise SourceScreenRefused("source_screen_refused") from None
    try:
        if (type(line) is not bytes or not 0 < len(line) <= MAX_LINE_BYTES or
                not line.endswith(b"\n") or line.count(b"\n") != 1):
            return _excluded()
        rows = bridge._parse_rows(line, starts_at_zero=True)
        if len(rows) != 1:
            return _excluded()
        event = rows[0]
        ref = hashlib.sha256(content._canonical(event)).hexdigest()
        if not _event_safe(event, policy, roster, private, excluded_tasks, ref, worktrees):
            return _excluded()
        return {"status": "accepted", "authority": "none", "line": line, "source_event_ref": ref,
                "privacy_coverage": "UNKNOWN", "source_membership": "UNKNOWN",
                "evaluation_exclusion_completeness": "UNKNOWN", "policy_authorization": "UNKNOWN"}
    except Exception:
        return _excluded()
