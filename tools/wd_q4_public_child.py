"""Default-off, explicit-input public Q4 child adapter; no CLI or live wiring.

The caller supplies the query function, all pins and already obtained bytes.
No environment, file, process, model, source identity or authenticity is read
or established here. An injected function can itself have effects: a real
caller must separately authorize and contain those effects. This adapter does
not prove freshness or prevent same-user substitution and ABA races.
Result text is not privacy-filtered by this child: only a trusted query function
may provide it. Callers must buffer and release output only after exit code 0;
a failed or short write may already have left partial bytes in the stream.
"""

import hashlib
import hmac
import io
import json
from pathlib import Path
import re

from tools.wd_q4_agent_query import _validate_config

MAX_QUERY = 65536
MAX_POLICY = 65536
MAX_ROSTER = 1048576
MAX_OUTPUT = 32768
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SCHEMA = "wd.q4-public-query.v2"
_FAIL = '{"schema":"wd.q4-public-query.v2","status":"REFUSED","reason":"public_query_refused"}\n'
_RESULT_KEYS = frozenset({"stored_text", "ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"})
_ARTIFACT_KEYS = frozenset({"descriptor_sha256", "memory_sha256", "projection_sha256", "receipt_sha256"})
_UNKNOWN_KEYS = frozenset({"publisher_authentication", "source_filter_attestation", "freshness", "same_user_aba"})
_ROOT_KEYS = frozenset({"schema", "status", "results", "binding"}) | _UNKNOWN_KEYS
_BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                           "roster_sha256", "public_policy_sha256"}) | _ARTIFACT_KEYS


def _pin(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _pinned(data, expected, limit):
    return (type(data) is bytes and len(data) <= limit and _pin(expected)
            and hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected))


def _read_query(stream):
    chunks = []
    size = 0
    while True:
        part = stream.read(MAX_QUERY + 1 - size)
        if type(part) is not bytes:
            raise ValueError()
        if not part:
            break
        size += len(part)
        if size > MAX_QUERY:
            raise ValueError()
        chunks.append(part)
    if size == 0:
        raise ValueError()
    return b"".join(chunks)


def _validated_output(value, expected):
    if (type(value) is not dict or set(value) != _ROOT_KEYS or value["schema"] != _SCHEMA
            or value["status"] not in ("ANSWER", "ABSTAIN", "DEGRADED")
            or type(value["results"]) is not list or len(value["results"]) > 100
            or (value["status"] == "ANSWER" and not value["results"])
            or (value["status"] == "ABSTAIN" and value["results"])
            or any(value[key] != "UNKNOWN" for key in _UNKNOWN_KEYS)):
        raise ValueError()
    binding = value["binding"]
    if (type(binding) is not dict or set(binding) != _BINDING_KEYS
            or any(not _pin(binding[key]) for key in _BINDING_KEYS)
            or any(not hmac.compare_digest(binding[key], pin) for key, pin in expected.items())):
        raise ValueError()
    for item in value["results"]:
        if (type(item) is not dict or set(item) != _RESULT_KEYS
                or type(item["stored_text"]) is not str
                or len(item["stored_text"].encode("utf-8", errors="strict")) > 320
                or item["ref_kind"] != "sha256_of_projected_metadata" or item["canonical_event"] is not False
                or not _pin(item["ref_sha256"]) or not _pin(item["doc_id_sha256"])):
            raise ValueError()
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
    if len(text.encode("utf-8")) > MAX_OUTPUT:
        raise ValueError()
    return text


def run_public(config, stdin, stdout, *, root=None, expected_pointer_sha256=None,
               expected_source_sha256=None, expected_encoder_sha256=None,
               policy_bytes=None, policy_sha256=None, roster_bytes=None, roster_sha256=None,
               public_policy_bytes=None, public_policy_sha256=None, enabled=False, query_fn=None):
    """Write one validated public envelope or a fixed refusal; return 0 or 2.

    A caller must explicitly inject the query function. This is a transport seam,
    not a trust anchor; unverified producer, identity and freshness remain unknown.
    It does not privacy-filter result text. A short write or flush failure may
    have emitted bytes; callers must release output only when return code is 0.
    """
    try:
        if (enabled is not True or not callable(query_fn) or not isinstance(root, (str, Path)) or not str(root)
                or not all(_pin(value) for value in (expected_pointer_sha256, expected_source_sha256,
                                                     expected_encoder_sha256))
                or not _pinned(policy_bytes, policy_sha256, MAX_POLICY)
                or not _pinned(roster_bytes, roster_sha256, MAX_ROSTER)
                or not _pinned(public_policy_bytes, public_policy_sha256, MAX_POLICY)):
            raise ValueError()
        _validate_config(config)  # Syntax/shape only; it authenticates no caller or files.
        query = _read_query(stdin)
        result = query_fn(config, io.BytesIO(query), root=root, expected_pointer_sha256=expected_pointer_sha256,
                          expected_source_sha256=expected_source_sha256, expected_encoder_sha256=expected_encoder_sha256,
                          policy_bytes=policy_bytes, policy_sha256=policy_sha256, roster_bytes=roster_bytes,
                          roster_sha256=roster_sha256, public_policy_bytes=public_policy_bytes,
                          public_policy_sha256=public_policy_sha256, enabled=True)
        expected = {"pointer_sha256": expected_pointer_sha256, "source_anchor_sha256": expected_source_sha256,
                    "encoder_sha256": expected_encoder_sha256, "policy_sha256": policy_sha256,
                    "roster_sha256": roster_sha256, "public_policy_sha256": public_policy_sha256,
                    "descriptor_sha256": config.descriptor_sha256}
        text, code = _validated_output(result, expected), 0
    except Exception:
        text, code = _FAIL, 2
    try:
        written = stdout.write(text)
        if type(written) is not int or written != len(text):
            return 2
        stdout.flush()
    except Exception:
        return 2
    return code
