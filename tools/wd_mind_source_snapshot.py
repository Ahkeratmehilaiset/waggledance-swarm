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
import hmac
import re
import types
from functools import lru_cache

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
# Candidate policy v1: fleet bytes were independently anchored in the deployed
# 29a356a9 manifest, not authorized by a caller-supplied bytes/digest pair.
# Updating the fleet requires a separately reviewed policy update. This source
# still requires its own review, approval and pinned deployment before use.
_REVIEWED_FLEET_SHA256 = "35dedecaf4b8658a1d6ccb9f1fec88af3e3bbe8fffa0bafa1b3deb3ff3333be0"
_REVIEWED_POLICY_VERSION = "wd.mind-reviewed-fleet-cwd.v1"
# Digest of the exact content-detector source audited for this exception.
# A new detector version is refused until separately reviewed and pinned.
_REVIEWED_CONTENT_SOURCE_SHA256 = "629b7fe6872d3c2048fd0128d31a1f40fa7673027091a53f90e0542eadb022c3"
_COMMON_LITERAL = "waggledance-agent-worktrees"
_TOOLS_LITERAL = "codex-tools-1-post-reboot-tools-sandbox-followup-20260731"
_FABLE_LITERAL = "fable-5-hex-capability-probe-v1-20260730"
_REVIEWED_WORKTREES = {
    "codex-tools-1": "C:\\Python\\" + _COMMON_LITERAL + "\\" + _TOOLS_LITERAL,
    "fable-5": "C:\\Python\\" + _COMMON_LITERAL + "\\" + _FABLE_LITERAL,
}
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


class _OneReviewedPhoneMatch:
    """Preserve regex behavior except one exact false-positive span in one cwd."""

    def __init__(self, pattern, expected_text, allowed_span):
        self.pattern, self.expected_text, self.allowed_span = pattern, expected_text, allowed_span

    def search(self, text):
        first = self.pattern.search(text)
        if text != self.expected_text or first is None or first.span() != self.allowed_span:
            return first
        # Include overlapping matches: another phone-like match is never hidden.
        for position in range(first.start() + 1, len(text)):
            other = self.pattern.match(text, position)
            if other is not None and other.span() != self.allowed_span:
                return other
        return None


def _same_code(left, right):
    """Compare executable structure, ignoring only location/debug metadata."""
    if type(left) is not types.CodeType or type(right) is not types.CodeType:
        return False
    fields = ("co_code", "co_names", "co_varnames", "co_freevars", "co_cellvars", "co_flags",
              "co_argcount", "co_posonlyargcount", "co_kwonlyargcount", "co_exceptiontable")
    if any(getattr(left, field, None) != getattr(right, field, None) for field in fields):
        return False
    if len(left.co_consts) != len(right.co_consts):
        return False
    for a, b in zip(left.co_consts, right.co_consts):
        if type(a) is types.CodeType or type(b) is types.CodeType:
            if not _same_code(a, b):
                return False
        elif type(a) is not type(b) or a != b:
            return False
    return True


@lru_cache(maxsize=1)
def _reviewed_screen_code(source, expected_digest):
    if (type(source) is not bytes or not 0 < len(source) <= 2 * 1024 * 1024
            or type(expected_digest) is not str
            or not hmac.compare_digest(hashlib.sha256(source).hexdigest(), expected_digest)):
        return None
    try:
        compiled = compile(source, "<independently-pinned-mind-content-source>", "exec", dont_inherit=True)
        screens = [item for item in compiled.co_consts if type(item) is types.CodeType and item.co_name == "_screen"]
        return screens[0] if len(screens) == 1 else None
    except (ValueError, SyntaxError, TypeError, MemoryError):
        return None


def _reviewed_detector_matches(source):
    if type(source) is not bytes or not 0 < len(source) <= 2 * 1024 * 1024:
        return False
    reviewed_code = _reviewed_screen_code(source, _REVIEWED_CONTENT_SOURCE_SHA256)
    detector = content._screen
    return (reviewed_code is not None and type(detector) is types.FunctionType
            and detector.__globals__ is content.__dict__
            and detector.__kwdefaults__ == {"redact_hex": False, "identifier_policy": None}
            and _same_code(detector.__code__, reviewed_code))


def _screen_reviewed_fable_cwd(value, private):
    """Rerun the same detector code on raw text with one narrow phone exception."""
    start = value.find(_FABLE_LITERAL)
    if start < 0 or value[start:] != _FABLE_LITERAL:
        return False
    span = (start + 30, start + 40)
    if value[slice(*span)] != "1-20260730":
        return False
    detector = content._screen
    try:
        # Do not mutate module globals: another lane or thread keeps the
        # original complete detector. All other globals remain unchanged.
        isolated = dict(detector.__globals__)
        isolated["_PHONE_RE"] = _OneReviewedPhoneMatch(content._PHONE_RE, value, span)
        screened = types.FunctionType(detector.__code__, isolated, detector.__name__,
                                      detector.__defaults__, detector.__closure__)
        screened.__kwdefaults__ = detector.__kwdefaults__
        screened(value, identifier_policy=private)
        return True
    except Exception:
        return False


def _safe_exact_cwd(value, agent, worktrees, private, fleet_sha256, reviewed_policy, reviewed_detector_source):
    """Except only the exact pinned worktree, retaining other content checks."""
    if agent not in _BUILD_LANES or type(value) is not str or value != worktrees.get(agent):
        return False
    reviewed = (type(fleet_sha256) is str and hmac.compare_digest(fleet_sha256, _REVIEWED_FLEET_SHA256)
                and value == _REVIEWED_WORKTREES.get(agent) and reviewed_policy == _REVIEWED_POLICY_VERSION
                and _reviewed_detector_matches(reviewed_detector_source))
    try:
        if content._normalize(value, content.MAX_MESSAGE_CHARS) != value:
            return False
        # The exception is for location syntax alone; still reject credentials,
        # private identifiers, sealed material and other unsafe path components.
        try:
            content._screen(value, identifier_policy=private)
        except content._Drop as exc:
            if not (reviewed and agent == "fable-5" and exc.token == "personal"
                    and _screen_reviewed_fable_cwd(value, private)):
                return False
        parts = value[3:].split("\\")
        approved = ({1: _COMMON_LITERAL, 2: _TOOLS_LITERAL} if agent == "codex-tools-1"
                    else {1: _COMMON_LITERAL, 2: _FABLE_LITERAL} if agent == "fable-5" else {})
        return all(_unchanged_text(part, private) or
                   (reviewed and approved.get(position) == part)
                   for position, part in enumerate(parts))
    except Exception:
        return False


def _event_safe(event, policy, roster, private, excluded_tasks, ref, worktrees, fleet_sha256,
                reviewed_policy, reviewed_detector_source):
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
    if ("cwd" in event and not _safe_exact_cwd(event["cwd"], event["agent"], worktrees, private, fleet_sha256,
                                                reviewed_policy, reviewed_detector_source)):
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
                excluded_tasks=None, fleet_bytes=None, fleet_sha256=None, reviewed_policy=None,
                reviewed_detector_source=None):
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
        if not _event_safe(event, policy, roster, private, excluded_tasks, ref, worktrees, fleet_sha256,
                           reviewed_policy, reviewed_detector_source):
            return _excluded()
        return {"status": "accepted", "authority": "none", "line": line, "source_event_ref": ref,
                "privacy_coverage": "UNKNOWN", "source_membership": "UNKNOWN",
                "evaluation_exclusion_completeness": "UNKNOWN", "policy_authorization": "UNKNOWN"}
    except Exception:
        return _excluded()
