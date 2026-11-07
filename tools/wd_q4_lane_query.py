"""Default-off lane-side public Q4 caller over ``tools.wd_q4_lane_link``; neither a CLI, a transport nor a wiring.

One lane turn asks the pinned public Q4 wrapper one bounded question and receives a closed answer. Everything
this module needs comes from the caller as explicit arguments: the bundle root, commit, deployment-manifest and
wrapper digests, the hash-pinned public plan, the caller's OWN expected binding digests (pointer, source anchor,
encoder, policy, roster, public policy, descriptor, projection), the bytes those pins cover (projection, policy and
roster) and an injected transport. Nothing is read from the environment, argv, a log or a default location; the
situation text travels only as in-memory stdin bytes to the transport, never in arguments, exceptions or output.

v2 (query-echo fix): no text returned by the child is ever output. A result is accepted only if its two digests
(``sha256(provenance.event_ref)``, ``sha256(record_id)``, exactly as the Q4 runtime reports them) name one record of
the caller-pinned projection, its ``stored_text`` equals that record's text byte for byte and the record is visible at
the query time; the text that is output is the record's, read from the caller-pinned bytes. An echoing transport or
child therefore fails ``result_refused``: the situation is not a projection record.

v4 (plan before query): the pinned public plan is read through the seam's own reader
(``wd_q4_lane_link._public_plan``: path, caller digest, closed schema) and every caller binding pin the plan carries
(the six plan pins and ``config.descriptor_sha256``) is cross-compared with ``expected_binding`` BEFORE the query is
sent; a disagreeing plan refuses ``binding_refused`` with the transport never invoked. ``projection_sha256`` is not a
plan field; it is bound by the caller's projection bytes before transport and by the reply binding after. The seam
repeats the plan check inside ``invoke_public`` (its own binding), so the reply re-check is unchanged.

v3 (projection trust and exception chain): the projection is not trusted on its pin alone. The caller also supplies
``policy_bytes`` and ``roster_bytes``; both are parsed by the Lead adapter ``shared_capture_adapter.strict_pins``
against the caller's own ``policy_sha256`` / ``roster_sha256`` pins (``pins_refused``), and the projection is then
revalidated with the REAL ``shared_projection.validate_projection`` under those approved tasks and that roster with
``exclude_review_signal=True`` (every record re-derived from its own metadata, roster authorship and review exclusion
re-pinned: ``projection_refused``). Every record's ``available_utc`` and ``time_utc`` must be canonical UTC text
(``corpus.utc_text(corpus.parse_utc(x)) == x``); a repeated record identity refuses even when byte-identical. All of
this happens BEFORE any transport call. Every refusal is raised after the handler that observed the failure has been
left, so a child, transport or parser exception is never attached as ``__cause__`` or ``__context__``. Raw query bytes
and raw transport output are lane-local: they are never logged, posted or returned.

``lane`` is a declaration shared by the five known lanes; it is never authentication. Pins are integrity of what the
caller supplied, not attestation that the projection, policy or roster are public, fresh or authentic; this module
does not prove freshness, publisher authentication, source filtering, same-user protection, process containment or a
whole-call time bound. Until a separately trusted transport exists (``TRANSPORT_INTERFACE``), only an injected
synthetic callable can be used; nothing here enables, installs or activates a producer, runtime or model.

Card mode (synthetic, default OFF): ``expected_binding`` may add ``card_decision_sha256``. The pinned plan must then
be a v2 plan carrying the same pin (and a v2 plan needs it in ``expected_binding``), the reply binding must carry
it, and the caller-pinned projection must be a v7 card projection that ``shared_card_projection`` re-derives
(``validate_shared_card_projection``) with every card task in the caller-pinned policy. Only the head-masked card
text (``head:unknown`` in place of the card's own head) is accepted and output, re-derived from the caller's own
projection bytes. The decision itself is verified by the child and the resolver, not here.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from os import PathLike
import re

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_projection as sp
from tools import wd_q4_lane_link as link
from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
from tools.wd_q4_secure_transport import TransportCleanupUnknown

SCHEMA = "wd.q4-lane-answer.v1"
LANES = link._LANES
MAX_SITUATION_CHARS = 4000
MAX_QUERY_BYTES = 65536
MAX_RESULTS = 100
MAX_PROJECTION_BYTES = 16 * 1024 * 1024  # == shared_result_resolver.MAX_BYTES
MAX_POLICY_BYTES = 65536  # == shared_capture_adapter._POLICY_LIMIT
MAX_ROSTER_BYTES = sp.ROSTER_BUDGET
VISIBILITY_GUARD_SECONDS = 1.0  # == the resolver guard band over shared_projection.visible_at
REF_KIND = "sha256_of_projected_metadata"
TRANSPORT_INTERFACE = "transport(*, wrapper: str, arguments: tuple, stdin_bytes: bytes) -> (bytes, int)"
PLAN_READER = "wd_q4_lane_link._public_plan(path, expected_sha256) -> (path, raw, pins)"
BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                          "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256"})
CARD_BINDING_KEYS = BINDING_KEYS | {"card_decision_sha256"}
# The public-policy digest is a plan/input pin, not a claim in a staged reply.
STAGED_BINDING_KEYS = CARD_BINDING_KEYS | {"card_policy_sha256"}
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PIN64 = re.compile(r"[0-9A-Fa-f]{64}\Z")
_TEXT = re.compile(r"[A-Za-z0-9._: -]{1,320}\Z")
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_SEAM_REASONS = frozenset({"disabled", "invalid_lane", "transport_unavailable", "binding_refused", "invalid_query",
                           "transport_refused"})
_TRUST = {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN",
          "projection_public_attestation": "UNKNOWN", "transport": "caller_injected", "projection": "caller_pinned"}


class LaneQueryRefused(ValueError):
    """Fixed public reason only: never the situation, a path, a pin, a reply, a record or a transport detail; never
    raised inside an exception handler, so it carries no ``__cause__`` and no ``__context__``."""


def _hex(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _bounded(value, limit):
    return type(value) is bytes and 1 <= len(value) <= limit


def _inputs(situation, time, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
            expected_public_plan_sha256, expected_binding, bundle_root, public_plan_path, projection_bytes,
            policy_bytes, roster_bytes):
    """Syntax of every caller input, checked before any file is touched; returns the canonical query bytes."""
    if (not _hex(expected_commit, _HEX40) or not _hex(expected_manifest_sha256, _PIN64)
            or not _hex(expected_wrapper_sha256, _PIN64) or not _hex(expected_public_plan_sha256, _HEX64)
            or type(expected_binding) is not dict or set(expected_binding) not in
               (BINDING_KEYS, CARD_BINDING_KEYS, STAGED_BINDING_KEYS)
            or any(not _hex(expected_binding[key], _HEX64) for key in expected_binding)
            or not isinstance(bundle_root, (str, PathLike)) or not isinstance(public_plan_path, (str, PathLike))
            or not _bounded(projection_bytes, MAX_PROJECTION_BYTES) or not _bounded(policy_bytes, MAX_POLICY_BYTES)
            or not _bounded(roster_bytes, MAX_ROSTER_BYTES)):
        raise ValueError()
    if (type(situation) is not str or not 1 <= len(situation) <= MAX_SITUATION_CHARS
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in situation)):
        raise ValueError()
    if not isinstance(time, datetime) or time.tzinfo is None or time.utcoffset() is None:
        raise ValueError()
    when = time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = json.dumps({"situation": situation, "time": when}, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(query) > MAX_QUERY_BYTES:
        raise ValueError()
    return query, when


def _constant(_):
    raise ValueError()  # NaN / Infinity never enter a projection


def _pairs(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError()
        document[key] = value
    return document


def _canonical_utc(value):
    """Exactly the ``corpus.utc_text`` form of a parseable aware instant; anything else refuses."""
    if type(value) is not str or not value:
        raise ValueError()
    parsed = sp.corpus_mod.parse_utc(value)
    if parsed is None or sp.corpus_mod.utc_text(parsed) != value:
        raise ValueError()
    return value


def _records(projection_bytes, expected_sha256, tasks, roster):
    """The record index ``(sha256(event_ref), sha256(record_id)) -> record`` of a projection that hashes to the
    caller's pin, is the builder's canonical serialization (``sort_keys``, ``indent=1``) and passes the Lead
    ``validate_projection`` under the caller-pinned approved tasks and roster with review signal excluded; every
    record must be an event record with the projected-metadata ref kind, ``canonical_event`` False, ``record_id ==
    "event:" + event_ref`` and canonical UTC ``time_utc`` / ``available_utc``; any repeated identity refuses."""
    if not hmac.compare_digest(hashlib.sha256(projection_bytes).hexdigest(), expected_sha256):
        raise ValueError()
    projection = json.loads(projection_bytes.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                            parse_constant=_constant)
    if (type(projection) is not dict
            or json.dumps(projection, sort_keys=True, indent=1).encode("utf-8") != projection_bytes
            or projection.get("schema") != sp.SCHEMA or projection.get("scope") != sp.SCOPE
            or projection.get("authority") != "none" or type(projection.get("records")) is not list):
        raise ValueError()
    sp.validate_projection(projection, approved_tasks=tasks, include_refs=False, roster=roster,
                           exclude_review_signal=True)  # ProjectionRefused is a ValueError
    records = {}
    for record in projection["records"]:
        if type(record) is not dict or type(record.get("provenance")) is not dict:
            raise ValueError()
        provenance = record["provenance"]
        ref = provenance["event_ref"]
        if (record["kind"] != "event" or provenance["ref_kind"] != REF_KIND or provenance["canonical_event"] is not False
                or not _hex(ref, _HEX64) or record["record_id"] != "event:" + ref
                or type(record["stored_text"]) is not str
                or _canonical_utc(provenance["available_utc"]) != _canonical_utc(record["available_utc"])
                or _canonical_utc(record["time_utc"]) > provenance["available_utc"]):
            raise ValueError()
        key = (hashlib.sha256(ref.encode("utf-8")).hexdigest(),
               hashlib.sha256(record["record_id"].encode("utf-8")).hexdigest())
        if key in records:
            raise ValueError()
        records[key] = record
    return records


def _card_records(projection_bytes, expected_sha256, tasks, *, _real_decision=None):
    """Card mode: the index of a v7 card projection that hashes to the caller pin, is the builder's canonical
    serialization and re-derives under ``validate_shared_card_projection``; each record's text is its head-masked
    form (the card's own head replaced once by ``head:unknown``), so a raw head is never accepted or output."""
    from sandbox.mind.step2 import shared_card_projection as cards
    if not hmac.compare_digest(hashlib.sha256(projection_bytes).hexdigest(), expected_sha256):
        raise ValueError()
    projection = json.loads(projection_bytes.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                            parse_constant=_constant)
    if (type(projection) is not dict
            or json.dumps(projection, sort_keys=True, indent=1).encode("utf-8") != projection_bytes
            or projection.get("schema") != cards.SCHEMA):
        raise ValueError()
    cards.validate_shared_card_projection(projection, _real_decision=_real_decision)
    records = {}
    for record in projection["records"]:
        provenance = record["provenance"]
        ref = provenance["event_ref"]
        if (record["kind"] != "event" or provenance["ref_kind"] != REF_KIND or provenance["canonical_event"] is not False
                or not _hex(ref, _HEX64) or record["record_id"] != "card:" + ref
                or type(record["stored_text"]) is not str or record["meta"].get("task_id") not in tasks
                or _canonical_utc(provenance["available_utc"]) != _canonical_utc(record["available_utc"])
                or _canonical_utc(record["time_utc"]) > provenance["available_utc"]):
            raise ValueError()
        data = projection["sources"][provenance["source_event_ref"]]["payload"]["public_knowledge"]
        text = record["stored_text"]
        if "head" in data:
            claimed = " head:" + data["head"] + " "
            if text.count(claimed) != 1:
                raise ValueError()
            text = text.replace(claimed, " " + cards.UNKNOWN_HEAD + " ")
        key = (hashlib.sha256(ref.encode("utf-8")).hexdigest(),
               hashlib.sha256(record["record_id"].encode("utf-8")).hexdigest())
        if key in records:
            raise ValueError()
        records[key] = dict(record, stored_text=text)
        if _real_decision is not None:
            # The verified real route can return the pinned observed head.
            # Compare that exact text first; outward text remains head-masked.
            records[key]['_reply_text'] = record['stored_text']
    return records


def _checked_staged_card_decision(plan, expected_binding, projection_bytes):
    """Bind a route decision to the actual caller-pinned C2 result, not a status claim."""
    from tools import wd_q4_public_pointer_resolver as resolver
    from waggledance.core.bridge_log_reader import BridgeCursor
    limits = {'pointer': 1024*1024, 'policy': 65536, 'roster': 1024*1024,
              'public_policy': 65536, 'card_decision': 262144, 'card_policy': 65536}
    raw = {}
    for key, limit in limits.items():
        value = link._read(link._path(plan[key+'_path'], directory=False), limit)
        digest = hashlib.sha256(value).hexdigest()
        if not hmac.compare_digest(digest, plan[key+'_sha256']) or not hmac.compare_digest(
                digest, expected_binding[key+'_sha256']):
            raise ValueError()
        raw[key] = value
    cursor = plan['end_cursor']
    checked = resolver.resolve_card_snapshot_pointer(plan['root'], pointer_bytes=raw['pointer'],
        expected_pointer_sha256=expected_binding['pointer_sha256'],policy_bytes=raw['policy'],
        policy_sha256=expected_binding['policy_sha256'],roster_bytes=raw['roster'],
        roster_sha256=expected_binding['roster_sha256'],card_decision_bytes=raw['card_decision'],
        card_decision_sha256=expected_binding['card_decision_sha256'],card_policy_bytes=raw['card_policy'],
        source_path=plan['source_path'],generation_path=plan['generation_path'],end_cursor=BridgeCursor(**cursor),
        enabled=True)
    if (checked['status'] != 'VALIDATED_STAGED' or checked['completeness'] != 'UNKNOWN'
            or checked['source_prefix']['sha256'] != expected_binding['source_anchor_sha256']
            or checked['projection_sha256'] != expected_binding['projection_sha256']
            or not hmac.compare_digest(hashlib.sha256(projection_bytes).hexdigest(), checked['projection_sha256'])):
        raise ValueError()
    decision = link._json(raw['card_decision'])
    if decision.get('synthetic') is True:
        return None
    if decision.get('synthetic') is not False:
        raise ValueError()
    return decision


def _text(value):
    if (type(value) is not str or _TEXT.fullmatch(value) is None or "@" in value
            or _HEX_RUN.search(value) is not None or _UUID.search(value) is not None):
        raise ValueError()
    return value


def _resolved(item, records, when, seen):
    """The projection record a child result names, or ValueError; the output text is the RECORD's, never the item's."""
    key = (item["ref_sha256"], item["doc_id_sha256"])
    record = records.get(key)
    if (record is None or key in seen or type(item["stored_text"]) is not str
            or not hmac.compare_digest(item["stored_text"].encode("utf-8"),
                                       record.get("_reply_text", record["stored_text"]).encode("utf-8"))
            or not sp.visible_at(record, when, guard_band_s=VISIBILITY_GUARD_SECONDS)):
        raise ValueError()
    seen.add(key)
    return {"ref_sha256": key[0], "doc_id_sha256": key[1], "stored_text": _text(record["stored_text"])}


def _answer(lane, value, expected_binding, records, when):
    """Closed projection of a seam-validated reply; the caller's own pins and projection decide, never the child.
    Returns ``(answer, None)`` or ``(None, reason)``; it raises nothing itself."""
    if value["status"] == "REFUSED":
        return {"schema": SCHEMA, "lane": lane, "status": "REFUSED", "reason": "runtime_refused",
                "result_count": 0, "results": [], "binding": None, "trust": dict(_TRUST)}, None
    binding = value["binding"]
    staged = set(expected_binding) == STAGED_BINDING_KEYS
    reply_pins = set(expected_binding) - ({"public_policy_sha256"} if staged else set())
    if (type(binding) is not dict or reply_pins - set(binding)
            or (staged and (value.get("schema") != "wd.q4-card-snapshot-query.v1"
                            or value.get("completeness") != "UNKNOWN"
                            or binding.get("source_mode") != "card-snapshot-v1"))
            or ("card_decision_sha256" in binding) != ("card_decision_sha256" in expected_binding)
            or any(not hmac.compare_digest(binding[key], expected_binding[key]) for key in reply_pins)):
        return None, "binding_refused"
    results = value["results"]
    if type(results) is not list or len(results) > MAX_RESULTS:
        return None, "result_refused"
    seen, clean = set(), []
    for item in results:
        try:
            resolved = _resolved(item, records, when, seen)
        except Exception:  # noqa: BLE001 - any failure of one item is one fixed refusal, raised outside the handler
            resolved = None
        if resolved is None:
            return None, "result_refused"
        clean.append(resolved)
    return {"schema": SCHEMA, "lane": lane, "status": value["status"], "reason": "", "result_count": len(clean),
            "results": clean, "binding": {key: expected_binding[key] for key in sorted(expected_binding)},
            "trust": dict(_TRUST)}, None


def _query_public_impl(*, lane, situation, time, bundle_root, expected_commit, expected_manifest_sha256,
                       expected_wrapper_sha256, public_plan_path, expected_public_plan_sha256, expected_binding,
                       projection_bytes=None, policy_bytes=None, roster_bytes=None, transport=None, enabled=False):
    """One bounded public Q4 question for one declared lane over an injected transport; OFF by default.

    Order: switch, lane, transport presence, input syntax (nothing read yet), canonical query bytes in memory, the
    caller-pinned policy and roster parsed by the Lead adapter (``pins_refused``), the caller-pinned projection
    verified, revalidated and indexed (``projection_refused``), the pinned plan read through the seam reader and
    cross-compared with every caller pin it carries (``binding_refused``; still no transport call), then
    ``wd_q4_lane_link.invoke_public`` (binds bundle, manifest, wrapper and plan, calls the transport once, validates
    the reply against the plan pins), then the caller's own ``expected_binding`` (eight digests) must equal the
    reply binding and every result must resolve to one visible record of the verified projection with identical
    text (``result_refused``); the returned text is the record's. Timeout and unconfirmed cleanup are reconstructed
    without the originating exception or message; an attestation cleanup exception retains its lease. Caller-frame
    locals and arbitrary objects held by a lease cannot be scrubbed by this function.
    """
    if enabled is not True:
        raise LaneQueryRefused("disabled")
    if type(lane) is not str or lane not in LANES:
        raise LaneQueryRefused("invalid_lane")
    if not callable(transport):
        raise LaneQueryRefused("transport_unavailable")
    prepared = None
    try:
        prepared = _inputs(situation, time, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
                           expected_public_plan_sha256, expected_binding, bundle_root, public_plan_path,
                           projection_bytes, policy_bytes, roster_bytes)
    except Exception:  # noqa: BLE001 - one fixed refusal, raised outside the handler
        prepared = None
    if prepared is None:
        raise LaneQueryRefused("invalid_input")
    query, when = prepared
    pinned = None
    try:
        pinned = adapter.strict_pins(policy_bytes, expected_binding["policy_sha256"], roster_bytes,
                                     expected_binding["roster_sha256"])
    except Exception:  # noqa: BLE001
        pinned = None
    if pinned is None:
        raise LaneQueryRefused("pins_refused")
    tasks, roster = pinned
    records = None
    try:
        if "card_decision_sha256" in expected_binding:
            projection = link._json(projection_bytes)
            real_decision = None
            if projection.get('synthetic') is False:
                if set(expected_binding) != STAGED_BINDING_KEYS:
                    raise ValueError()
                _, plan_raw, early_pins = link._public_plan(public_plan_path, expected_public_plan_sha256)
                if (not early_pins or any(key not in expected_binding or
                        not hmac.compare_digest(value, expected_binding[key]) for key, value in early_pins.items())):
                    raise ValueError()
                real_decision = _checked_staged_card_decision(link._json(plan_raw), expected_binding, projection_bytes)
                if real_decision is None:
                    raise ValueError()
            records = _card_records(projection_bytes, expected_binding["projection_sha256"], tasks,
                                    _real_decision=real_decision)
        else:
            records = _records(projection_bytes, expected_binding["projection_sha256"], tasks, roster)
    except Exception:  # noqa: BLE001
        records = None
    if records is None:
        raise LaneQueryRefused("projection_refused")
    plan_pins = None
    try:
        plan_pins = link._public_plan(public_plan_path, expected_public_plan_sha256)[2]
    except Exception:  # noqa: BLE001 - unreadable, unpinned or malformed plan
        plan_pins = None
    if (type(plan_pins) is not dict or not plan_pins
            or ("card_decision_sha256" in plan_pins) != ("card_decision_sha256" in expected_binding)
            or ("card_policy_sha256" in plan_pins) != ("card_policy_sha256" in expected_binding)
            or any(key not in expected_binding or type(plan_pins[key]) is not str
                   or not hmac.compare_digest(plan_pins[key], expected_binding[key]) for key in plan_pins)):
        raise LaneQueryRefused("binding_refused")
    value, reason, critical, lease, prior = None, None, None, None, None
    try:
        value = link.invoke_public(lane=lane, bundle_root=bundle_root, expected_commit=expected_commit,
                                   expected_manifest_sha256=expected_manifest_sha256,
                                   expected_wrapper_sha256=expected_wrapper_sha256,
                                   public_plan_path=public_plan_path,
                                   expected_public_plan_sha256=expected_public_plan_sha256,
                                   query_bytes=query, transport=transport, enabled=True)
    except link.LaneLinkRefused as refusal:
        reason = refusal.args[0] if refusal.args and refusal.args[0] in _SEAM_REASONS else "transport_refused"
    except AttestationCleanupUnknown as refusal:
        critical, lease = "attestation", refusal.lease
    except TransportCleanupUnknown as refusal:
        critical = "transport"
        allowed = {"timeout", "refused", "interrupt", "exit", "none", "other", "prior_unknown"}
        prior = refusal.prior_outcome if type(refusal.prior_outcome) is str and refusal.prior_outcome in allowed else "other"
    except TimeoutError:
        critical = "timeout"
    except Exception:  # noqa: BLE001 - a transport or parser failure is one fixed refusal, never chained
        reason = "transport_refused"
    if critical is not None:
        # Do not leave the question, source bytes or private paths in this traceback frame.
        del situation, query, transport, projection_bytes, policy_bytes, roster_bytes, records, bundle_root, public_plan_path
        del expected_commit, expected_manifest_sha256, expected_wrapper_sha256, expected_public_plan_sha256
        del expected_binding, prepared, pinned, tasks, roster, plan_pins, value, reason
        if critical == "attestation":
            raise AttestationCleanupUnknown(lease)
        if critical == "transport":
            raise TransportCleanupUnknown(prior)
        raise TimeoutError("secure_transport_timeout")
    if reason is not None:
        raise LaneQueryRefused(reason)
    answer = None
    try:
        answer, reason = _answer(lane, value, expected_binding, records, when)
    except Exception:  # noqa: BLE001 - a malformed seam value is one fixed refusal
        answer, reason = None, "result_refused"
    if reason is not None or answer is None:
        raise LaneQueryRefused(reason or "result_refused")
    return answer


def query_public(*, lane, situation, time, bundle_root, expected_commit, expected_manifest_sha256,
                 expected_wrapper_sha256, public_plan_path, expected_public_plan_sha256, expected_binding,
                 projection_bytes=None, policy_bytes=None, roster_bytes=None, transport=None, enabled=False):
    """Public facade that removes its own sensitive inputs on refusal.

    Refusals and critical transport exceptions are reconstructed outside their
    handlers, so the implementation's traceback is not attached. Other caller
    frames and arbitrary data contained in an attestation lease are outside
    this function's control and must not be treated as scrubbed.
    """
    failure, reason, lease, prior = None, None, None, None
    try:
        return _query_public_impl(
            lane=lane, situation=situation, time=time, bundle_root=bundle_root, expected_commit=expected_commit,
            expected_manifest_sha256=expected_manifest_sha256, expected_wrapper_sha256=expected_wrapper_sha256,
            public_plan_path=public_plan_path, expected_public_plan_sha256=expected_public_plan_sha256,
            expected_binding=expected_binding, projection_bytes=projection_bytes, policy_bytes=policy_bytes,
            roster_bytes=roster_bytes, transport=transport, enabled=enabled)
    except AttestationCleanupUnknown as exc:
        failure, lease = "attestation", exc.lease
    except TransportCleanupUnknown as exc:
        failure = "transport"
        allowed = {"timeout", "refused", "interrupt", "exit", "none", "other", "prior_unknown"}
        prior = exc.prior_outcome if type(exc.prior_outcome) is str and exc.prior_outcome in allowed else "other"
    except TimeoutError:
        failure = "timeout"
    except LaneQueryRefused as exc:
        failure = "refused"
        allowed = _SEAM_REASONS | {"invalid_input", "pins_refused", "projection_refused", "result_refused"}
        reason = exc.args[0] if exc.args and type(exc.args[0]) is str and exc.args[0] in allowed else "transport_refused"
    except Exception:
        failure, reason = "refused", "transport_refused"
    del lane, situation, time, bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256
    del public_plan_path, expected_public_plan_sha256, expected_binding, projection_bytes, policy_bytes, roster_bytes
    del transport, enabled
    if failure == "attestation":
        raise AttestationCleanupUnknown(lease)
    if failure == "transport":
        raise TransportCleanupUnknown(prior)
    if failure == "timeout":
        raise TimeoutError("secure_transport_timeout")
    raise LaneQueryRefused(reason)
