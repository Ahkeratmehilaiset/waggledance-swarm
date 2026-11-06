"""Inactive in-memory resolved-Q4 helper; not a CLI or trust anchor.

Caller supplies exact bytes and independently anchored SHA256 pins, config,
source and non-review policy. This module does not authenticate any of them,
read files, establish freshness or identity, prevent check/use races, or prove
that an outer transport and its logs keep query and projection bytes private.
"""

import hashlib
import hmac
import json
import re

from sandbox.mind.step2 import shared_projection as sp
from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_result_resolver as resolver
from tools import wd_q4_agent_query as q4

MAX_POLICY = 65536
MAX_ARTIFACT = 16 * 1024 * 1024
MAX_OUTPUT = 32768
MAX_TASKS = 512
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TEXT = re.compile(r"[A-Za-z0-9._: -]{1,320}\Z")
_REF_KIND = "sha256_of_projected_metadata"
_SCHEMA = "wd.q4-resolved-child.v1"


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError()
        out[key] = value
    return out


def _pinned(data, expected, limit):
    if (type(data) is not bytes or len(data) > limit or not isinstance(expected, str)
            or not _HASH.fullmatch(expected)
            or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected)):
        raise ValueError()


def _policy(data):
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (not isinstance(value, dict) or set(value) != {"schema", "approved_tasks"}
            or value["schema"] != "wd.q4-non-review-policy.v1" or not isinstance(value["approved_tasks"], list)
            or len(value["approved_tasks"]) > MAX_TASKS
            or any(not isinstance(item, str) or sp.TASK.fullmatch(item) is None
                   or any(marker in item for marker in sp.SEALED_MARKERS) for item in value["approved_tasks"])):
        raise ValueError()
    tasks = frozenset(value["approved_tasks"])
    if len(tasks) != len(value["approved_tasks"]):
        raise ValueError()
    return tasks


def _validate_projection(data, tasks, roster):
    """Refuse UNKNOWN/unapproved tasks and RCO origins before invoking Q4.

    Projection loses recipient information; this check does not authenticate
    source, prove non-review classification, or detect hidden RCO recipients.
    """
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if json.dumps(value, sort_keys=True, indent=1).encode("utf-8") != data:
        raise ValueError()
    sp.validate_projection(value, approved_tasks=tasks, include_refs=False,
                           roster=roster, exclude_review_signal=True)
    for record in value["records"]:
        meta = record["meta"]
        if meta["task_id"] not in tasks or meta["agent"] in ("claude-rco-1", "claude-rco-2"):
            raise ValueError()


def _result(value):
    if (not isinstance(value, dict) or set(value) != {"status", "results"}
            or value["status"] not in ("ANSWER", "ABSTAIN", "DEGRADED")
            or not isinstance(value["results"], list) or len(value["results"]) > 100
            or (value["status"] == "ANSWER" and not value["results"])
            or (value["status"] == "ABSTAIN" and value["results"])):
        raise ValueError()
    clean = []
    for item in value["results"]:
        if (not isinstance(item, dict)
                or set(item) != {"ref_sha256", "doc_id_sha256", "stored_text", "ref_kind", "canonical_event"}
                or item["ref_kind"] != _REF_KIND or item["canonical_event"] is not False
                or not isinstance(item["stored_text"], str) or _TEXT.fullmatch(item["stored_text"]) is None
                or any(not isinstance(item[key], str) or _HASH.fullmatch(item[key]) is None
                       for key in ("ref_sha256", "doc_id_sha256"))):
            raise ValueError()
        clean.append({"ref_sha256": item["ref_sha256"], "doc_id_sha256": item["doc_id_sha256"],
                      "stored_text": item["stored_text"], "ref_kind": _REF_KIND, "canonical_event": False})
    return {"schema": _SCHEMA, "status": value["status"], "results": clean}


def run_resolved(config, stdin, stdout, *, policy_bytes, policy_sha256, memory_bytes, memory_sha256,
                 projection_bytes, projection_sha256, roster_bytes=None, roster_sha256=None):
    """Emit bounded resolved metadata from caller-asserted and pinned inputs."""
    try:
        _pinned(policy_bytes, policy_sha256, MAX_POLICY)
        _pinned(memory_bytes, memory_sha256, MAX_ARTIFACT)
        _pinned(projection_bytes, projection_sha256, MAX_ARTIFACT)
        tasks, roster = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        if tasks != _policy(policy_bytes):
            raise ValueError()
        _validate_projection(projection_bytes, tasks, roster)
        # Validate the exact pinned tuple before invoking Q4. This empty-result
        # call checks schema and pin consistency; it does not assert clock or
        # source authenticity, nor does it make a query against the index.
        resolver.resolve_results(memory_manifest=memory_bytes, expected_memory_sha256=memory_sha256,
                                 projection_bytes=projection_bytes, results=[], query_utc="2000-01-01T00:00:00Z",
                                 policy_bytes=policy_bytes, policy_sha256=policy_sha256,
                                 roster_bytes=roster_bytes, roster_sha256=roster_sha256)
        result = _result(q4.query_and_resolve(config, stdin, memory_bytes, projection_bytes, tasks,
                         policy_bytes=policy_bytes, policy_sha256=policy_sha256,
                         roster_bytes=roster_bytes, roster_sha256=roster_sha256,
                         expected_memory_sha256=memory_sha256))
        text = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        if len(text.encode("utf-8")) > MAX_OUTPUT:
            raise ValueError()
        code = 0
    except Exception:
        text = '{"schema":"wd.q4-resolved-child.v1","status":"REFUSED","reason":"resolved_refused"}\n'
        code = 2
    try:
        written = stdout.write(text)
        if type(written) is not int or written != len(text):
            return 2
    except Exception:
        return 2
    return code
