"""Default-off, explicit-input public Q4 child adapter; no CLI or live wiring.

The caller supplies all pins and already obtained policy bytes. The exact
tuple is structurally validated before the fixed public query function is
imported. The caller must independently verify and contain that function and
its runtime before invoking this adapter; this module cannot authenticate code,
the publisher or source, prove freshness or prevent same-user substitution.
Result text is not privacy-filtered by this child. Callers must buffer and
release output only after exit code 0; a failed or short write may already
have left partial bytes in the stream.
An optional caller-pinned synthetic card route decision (default None) is
forwarded to the fixed public query and its pin must come back in the output
binding; it authenticates no issuer and enables nothing by itself.
run_content (default OFF) is the caller-pinned content snapshot route B: the
content decision, descriptor, memory and projection pins all come back bound.
"""

import hashlib
import hmac
import io
import json
import re

from tools.wd_q4_agent_query import _validate_config

MAX_QUERY = 65536
MAX_POLICY = 65536
MAX_ROSTER = 1048576
MAX_OUTPUT = 32768
MAX_DECISION = 256 * 1024  # == shared_card_projection.MAX_DECISION (tested)
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


def _validated_output(value, expected, binding_keys=_BINDING_KEYS):
    if (type(value) is not dict or set(value) != _ROOT_KEYS or value["schema"] != _SCHEMA
            or value["status"] not in ("ANSWER", "ABSTAIN", "DEGRADED")
            or type(value["results"]) is not list or len(value["results"]) > 100
            or (value["status"] == "ANSWER" and not value["results"])
            or (value["status"] == "ABSTAIN" and value["results"])
            or any(value[key] != "UNKNOWN" for key in _UNKNOWN_KEYS)):
        raise ValueError()
    binding = value["binding"]
    if (type(binding) is not dict or set(binding) != binding_keys
            or any(not _pin(binding[key]) for key in binding_keys)
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
               public_policy_bytes=None, public_policy_sha256=None, enabled=False,
               card_decision_bytes=None, card_decision_sha256=None):
    """Write one validated public envelope or a fixed refusal; return 0 or 2.

    The fixed public query module is lazily imported after exact plan validation.
    This is not a trust anchor; producer, identity and freshness remain unknown.
    It does not privacy-filter result text. A short write or flush failure may
    have emitted bytes; callers must release output only when return code is 0.
    """
    try:
        card = card_decision_bytes is not None or card_decision_sha256 is not None
        if card and not _pinned(card_decision_bytes, card_decision_sha256, MAX_DECISION):
            raise ValueError()
        forward = ({"card_decision_bytes": card_decision_bytes,
                    "card_decision_sha256": card_decision_sha256} if card else {})
        if (enabled is not True
                or not all(_pin(value) for value in (expected_pointer_sha256, expected_source_sha256,
                                                     expected_encoder_sha256))
                or not _pinned(policy_bytes, policy_sha256, MAX_POLICY)
                or not _pinned(roster_bytes, roster_sha256, MAX_ROSTER)
                or not _pinned(public_policy_bytes, public_policy_sha256, MAX_POLICY)):
            raise ValueError()
        if card:
            from sandbox.mind.step2 import shared_card_projection as cards
            decision = cards.verify_route_decision(card_decision_bytes, card_decision_sha256,
                            policy_sha256=policy_sha256, roster_sha256=roster_sha256)
            if decision["synthetic"] is not True:
                raise ValueError()  # Use run_staged_card with the actual C2 tuple.
        _validate_config(config)  # Syntax/shape only; it authenticates no caller or files.
        from tools.wd_q4_public_plan import validate_public_plan
        plan = validate_public_plan({
            "schema": "wd.q4-public-plan.v1", "config": config, "root": str(root),
            "expected_pointer_sha256": expected_pointer_sha256,
            "expected_source_sha256": expected_source_sha256,
            "expected_encoder_sha256": expected_encoder_sha256,
            "policy_bytes": policy_bytes, "policy_sha256": policy_sha256,
            "roster_bytes": roster_bytes, "roster_sha256": roster_sha256,
            "public_policy_bytes": public_policy_bytes, "public_policy_sha256": public_policy_sha256,
        }, enabled=True)
        query = _read_query(stdin)
        from tools.wd_q4_public_query import query_public
        result = query_public(plan.config, io.BytesIO(query), root=plan.root,
                              expected_pointer_sha256=plan.expected_pointer_sha256,
                              expected_source_sha256=plan.expected_source_sha256,
                              expected_encoder_sha256=plan.expected_encoder_sha256,
                              policy_bytes=plan.policy_bytes, policy_sha256=plan.policy_sha256,
                              roster_bytes=plan.roster_bytes, roster_sha256=plan.roster_sha256,
                              public_policy_bytes=plan.public_policy_bytes,
                              public_policy_sha256=plan.public_policy_sha256, enabled=True, **forward)
        expected = {"pointer_sha256": plan.expected_pointer_sha256,
                    "source_anchor_sha256": plan.expected_source_sha256,
                    "encoder_sha256": plan.expected_encoder_sha256, "policy_sha256": plan.policy_sha256,
                    "roster_sha256": plan.roster_sha256, "public_policy_sha256": plan.public_policy_sha256,
                    "descriptor_sha256": plan.config.descriptor_sha256}
        keys = _BINDING_KEYS
        if card:
            expected["card_decision_sha256"] = card_decision_sha256
            keys = _BINDING_KEYS | {"card_decision_sha256"}
        text, code = _validated_output(result, expected, keys), 0
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


_STAGED_SCHEMA = 'wd.q4-card-snapshot-query.v1'
_STAGED_FAIL = '{"schema":"wd.q4-card-snapshot-query.v1","status":"REFUSED","reason":"staged_query_refused"}\n'
_STAGED_UNKNOWN = _UNKNOWN_KEYS | {'completeness'}
_STAGED_BINDING = frozenset({'source_mode', 'pointer_sha256', 'source_anchor_sha256', 'encoder_sha256',
    'policy_sha256', 'roster_sha256', 'card_decision_sha256', 'card_policy_sha256',
    'descriptor_sha256', 'memory_sha256', 'projection_sha256'})


def _staged_pairs(items):
    out = {}
    for key, value in items:
        if key in out:
            raise ValueError()
        out[key] = value
    return out


def _validated_staged_output(value, expected):
    if (type(value) is not dict or set(value) != {'schema', 'status', 'results', 'binding'} | _STAGED_UNKNOWN
            or value['schema'] != _STAGED_SCHEMA or value['status'] not in ('ANSWER', 'ABSTAIN', 'DEGRADED')
            or type(value['results']) is not list or len(value['results']) > 100
            or (value['status'] == 'ANSWER' and not value['results'])
            or (value['status'] == 'ABSTAIN' and value['results'])
            or any(value[key] != 'UNKNOWN' for key in _STAGED_UNKNOWN)):
        raise ValueError()
    binding = value['binding']
    if (type(binding) is not dict or set(binding) != _STAGED_BINDING
            or binding['source_mode'] != 'card-snapshot-v1'
            or any(not _pin(binding[key]) for key in _STAGED_BINDING - {'source_mode'})
            or any(not hmac.compare_digest(binding[key], pin) for key, pin in expected.items())):
        raise ValueError()
    for item in value['results']:
        if (type(item) is not dict or set(item) != _RESULT_KEYS or type(item['stored_text']) is not str
                or len(item['stored_text'].encode('utf-8', errors='strict')) > 320
                or item['ref_kind'] != 'sha256_of_projected_metadata' or item['canonical_event'] is not False
                or not _pin(item['ref_sha256']) or not _pin(item['doc_id_sha256'])):
            raise ValueError()
    text = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n'
    if len(text.encode('utf-8')) > MAX_OUTPUT:
        raise ValueError()
    return text


def run_staged_card(config, stdin, stdout, *, root=None, pointer_bytes=None, expected_pointer_sha256=None,
                    expected_source_sha256=None, expected_encoder_sha256=None, policy_bytes=None,
                    policy_sha256=None, roster_bytes=None, roster_sha256=None, card_decision_bytes=None,
                    card_decision_sha256=None, card_policy_bytes=None, source_path=None,
                    generation_path=None, end_cursor=None, enabled=False):
    """Run one explicitly enabled C2 snapshot query; no source or publisher authentication is implied.

    The input stream is handed to the fixed query function without being read
    here, so its actual C2 check runs before the query is consumed. The caller
    must independently pin and contain this code, runtime and source tuple.
    """
    try:
        if (enabled is not True or not _pinned(pointer_bytes, expected_pointer_sha256, 1024 * 1024)
                or not _pinned(policy_bytes, policy_sha256, MAX_POLICY)
                or not _pinned(roster_bytes, roster_sha256, MAX_ROSTER)
                or not _pinned(card_decision_bytes, card_decision_sha256, MAX_DECISION)
                or type(card_policy_bytes) is not bytes or not 0 < len(card_policy_bytes) <= MAX_POLICY
                or not _pin(expected_source_sha256) or not _pin(expected_encoder_sha256)):
            raise ValueError()
        _validate_config(config)
        pointer = json.loads(pointer_bytes.decode('ascii'), object_pairs_hook=_staged_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        artifacts = pointer['artifacts']
        expected = {'pointer_sha256': expected_pointer_sha256, 'source_anchor_sha256': expected_source_sha256,
                    'encoder_sha256': expected_encoder_sha256, 'policy_sha256': policy_sha256,
                    'roster_sha256': roster_sha256, 'card_decision_sha256': card_decision_sha256,
                    'card_policy_sha256': hashlib.sha256(card_policy_bytes).hexdigest(),
                    'descriptor_sha256': config.descriptor_sha256, 'memory_sha256': artifacts['memory.json'],
                    'projection_sha256': artifacts['projection.json']}
        if any(not _pin(value) for value in expected.values()):
            raise ValueError()
        from tools.wd_q4_public_query import query_staged_card_snapshot
        result = query_staged_card_snapshot(config, stdin, root=root, pointer_bytes=pointer_bytes,
            expected_pointer_sha256=expected_pointer_sha256, expected_source_sha256=expected_source_sha256,
            expected_encoder_sha256=expected_encoder_sha256, policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256, card_decision_bytes=card_decision_bytes,
            card_decision_sha256=card_decision_sha256, card_policy_bytes=card_policy_bytes,
            source_path=source_path, generation_path=generation_path, end_cursor=end_cursor, enabled=True)
        text, code = _validated_staged_output(result, expected), 0
    except Exception:
        text, code = _STAGED_FAIL, 2
    try:
        written = stdout.write(text)
        if type(written) is not int or written != len(text):
            return 2
        stdout.flush()
    except Exception:
        return 2
    return code


_CONTENT_SCHEMA = 'wd.q4-content-snapshot-query.v1'
_CONTENT_FAIL = '{"schema":"wd.q4-content-snapshot-query.v1","status":"REFUSED","reason":"content_query_refused"}\n'
_CONTENT_BINDING = frozenset({'source_mode', 'encoder_sha256', 'policy_sha256', 'roster_sha256',
    'content_decision_sha256', 'descriptor_sha256', 'memory_sha256', 'projection_sha256'})
MAX_CONTENT_DECISION = 65536  # == content_memory.MAX_DECISION (tested)


def _validated_content_output(value, expected):
    if (type(value) is not dict or set(value) != {'schema', 'status', 'results', 'binding'} | _STAGED_UNKNOWN
            or value['schema'] != _CONTENT_SCHEMA or value['status'] not in ('ANSWER', 'ABSTAIN', 'DEGRADED')
            or type(value['results']) is not list or len(value['results']) > 100
            or (value['status'] == 'ANSWER' and not value['results'])
            or (value['status'] == 'ABSTAIN' and value['results'])
            or any(value[key] != 'UNKNOWN' for key in _STAGED_UNKNOWN)):
        raise ValueError()
    binding = value['binding']
    if (type(binding) is not dict or set(binding) != _CONTENT_BINDING
            or binding['source_mode'] != 'content-snapshot-v1'
            or set(expected) != _CONTENT_BINDING - {'source_mode'}
            or any(not _pin(binding[key]) or not hmac.compare_digest(binding[key], expected[key])
                   for key in expected)):
        raise ValueError()
    for item in value['results']:
        if (type(item) is not dict or set(item) != _RESULT_KEYS or type(item['stored_text']) is not str
                or len(item['stored_text'].encode('utf-8', errors='strict')) > 320
                or item['ref_kind'] != 'sha256_of_projected_metadata' or item['canonical_event'] is not False
                or not _pin(item['ref_sha256']) or not _pin(item['doc_id_sha256'])):
            raise ValueError()
    text = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n'
    if len(text.encode('utf-8')) > MAX_OUTPUT:
        raise ValueError()
    return text


def run_content(config, stdin, stdout, *, expected_encoder_sha256=None, policy_bytes=None, policy_sha256=None,
                roster_bytes=None, roster_sha256=None, content_decision_bytes=None, content_decision_sha256=None,
                expected_memory_sha256=None, expected_projection_sha256=None, content_sources=None, trusted_git=None,
                enabled=False):
    """Run one explicitly enabled caller-pinned content snapshot query (route B, default OFF).

    The input stream is handed to the fixed query function without being read here, so the decision and artifact
    checks run before the query is consumed; ``content_sources`` is required and the query function re-derives
    the pinned projection from them before stdin is read; ``trusted_git`` (the Git executable and digest from an
    independently verified source, or None) is forwarded unchanged and is the only Git the re-derivation may start.
    Every pin must come back in the output binding. No publisher or issuer is authenticated; the caller must
    independently pin and contain this code, runtime and snapshot.
    """
    try:
        if (enabled is not True or not _pinned(policy_bytes, policy_sha256, MAX_POLICY)
                or not _pinned(roster_bytes, roster_sha256, MAX_ROSTER)
                or not _pinned(content_decision_bytes, content_decision_sha256, MAX_CONTENT_DECISION)
                or not all(_pin(pin) for pin in (expected_encoder_sha256, expected_memory_sha256,
                                                 expected_projection_sha256))
                or type(content_sources) is not dict
                or (trusted_git is not None and type(trusted_git) is not dict)):
            raise ValueError()
        _validate_config(config)
        expected = {'encoder_sha256': expected_encoder_sha256, 'policy_sha256': policy_sha256,
                    'roster_sha256': roster_sha256, 'content_decision_sha256': content_decision_sha256,
                    'descriptor_sha256': config.descriptor_sha256, 'memory_sha256': expected_memory_sha256,
                    'projection_sha256': expected_projection_sha256}
        if any(not _pin(value) for value in expected.values()):
            raise ValueError()
        from tools.wd_q4_public_query import query_content_snapshot
        result = query_content_snapshot(config, stdin, expected_encoder_sha256=expected_encoder_sha256,
            policy_bytes=policy_bytes, policy_sha256=policy_sha256, roster_bytes=roster_bytes,
            roster_sha256=roster_sha256, content_decision_bytes=content_decision_bytes,
            content_decision_sha256=content_decision_sha256, expected_memory_sha256=expected_memory_sha256,
            expected_projection_sha256=expected_projection_sha256, content_sources=content_sources,
            trusted_git=trusted_git, enabled=True)
        text, code = _validated_content_output(result, expected), 0
    except Exception:
        text, code = _CONTENT_FAIL, 2
    try:
        written = stdout.write(text)
        if type(written) is not int or written != len(text):
            return 2
        stdout.flush()
    except Exception:
        return 2
    return code
