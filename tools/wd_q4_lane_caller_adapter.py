"""Default-off per-lane caller for the existing bound Q4 session chain: declared-consistent, never authenticated.

One call of ``tools.wd_q4_lane_session_client.query_session`` (the Python layer of the existing
bound-session chain) for the lane named by a caller-independent identity document, after the
identity, the installed shared descriptor and the sixteen pinned chain inputs have been checked
for mutual consistency. The lane is never an argument of this function: it comes from the identity
document, which must name the current process and must agree with the deployment-pinned roster.

What this module proves and what it does not:

* ``lane_binding = "DECLARED_CONSISTENT"`` means only that the identity document hashes to the
  separately supplied pin, names this process id, names a lane that is in the pinned roster with
  the roster's uuid, declares the worktree that the pinned fleet document records for that lane
  (string equality on path parts, ``.`` and ``..`` parts refused, no filesystem resolution:
  junctions and reparse points are NOT resolved here; a fleet lane without a string worktree
  fails closed), declares the cwd that ``os.getcwd()`` reports for this process (path-part
  equality) under that worktree, carries a 64-hex owner-token digest (presence and shape only: no
  independently published digest exists today to compare it with), and pins the same descriptor
  whose pins equal the chain inputs.
* Anchors are caller-provided: the fleet document, the shared descriptor and every pin arrive as
  bytes plus a sha256 from the caller. The module checks their mutual consistency only; it cannot
  verify that they are the installed deployment artifacts, and it never reads them from disk.
* ``lane_authentication`` is always ``"UNKNOWN"``. All lanes run as one Windows user; a malicious
  same-user process can present a consistent identity document. Nothing here protects against it.
* The answer is the existing chain's validated document, re-checked here for the declared lane,
  the descriptor's expected binding and a closed shape; any other output is discarded as REFUSED.
* ``cleanup = "chain_confirmed"`` is reported only when the chain returned a document (validated or
  refused by content): the fixed transport returns only after its cleanup block drained the Job within
  its budget, otherwise it raises a cleanup-unknown error. A plain ``TimeoutError`` or a generic
  refusal (``inner_status = "timeout"`` / ``"session_refused"``) carries no positive cleanup signal:
  it may originate before any launch, after the transport returned, or from a statement of the lower
  layers that lies outside their protected cleanup operations (review 2026-11-06: such a statement
  can skip termination and the cleanup-unknown mapping, and unexpected exceptions are mapped to an
  ordinary refusal). This caller cannot tell those origins apart, so both are reported
  ``status = "UNKNOWN"``, ``cleanup = "unknown"`` and leave the process quarantine set, exactly like
  an explicit cleanup-unknown error. There is no outer whole-call watchdog here. The process
  quarantine is pessimistic: it is set before every chain call and cleared only after a chain
  document was returned (validated or refused by content), so a timeout, a generic refusal, an
  interruption anywhere in the call, a cleanup-unknown error from any stage or any other
  BaseException leaves it set; calls are serialized by a process lock (a concurrent call refuses
  ``caller_busy``). There is no persistent cross-process marker.
* Path comparison folds ASCII letters only and refuses empty path parts; see ``_parts``. The fleet worktree
  lookup covers ``lanes`` and the ``tools_supervisor`` entry (the tools lane lives there in the real manifest).
* A cleanup-unknown error raised before the chain call (roster or descriptor stage) is reported as
  ``CallerCleanupUnknown("caller_cleanup_unknown")`` (a ``RuntimeError``, never a ``CallerRefused``), with the
  quarantine set and an attestation lease retained in ``_HOLD``; a CLI maps it to its cleanup-unknown exit code.
  No persistent cross-process proof is claimed for either.
* Default off reads no argument. Enabled, it reads no file, no environment and no clock, starts no
  process by itself and writes nothing; the chain below starts the pinned fixed peer.
* No query text, token, path or exception detail is placed in the result or in any exception.
  Pre-check exceptions are swallowed and released before the fixed ``CallerRefused`` is raised
  (no ``__cause__`` / ``__context__``), so their frame locals (identity and descriptor bytes) do
  not travel with it; while a pre-check exception is in flight its traceback frame locals are
  visible to the interpreter as usual, and traceback frame locals of the chain are outside this
  boundary.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import threading

from tools import wd_q4_lane_session_client as session
from tools import wd_q4_guard_completion as completion
from tools import wd_q4_roster_from_fleet as roster
from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
from tools.wd_q4_secure_transport import TransportCleanupUnknown

SCHEMA = "wd.q4-lane-caller-result.v1"
IDENTITY_SCHEMA = "wd.q4-lane-caller-identity.v1"
DESCRIPTOR_SCHEMA = "wd.q4-shared-session-config.v2"
MAX_DOCUMENT_BYTES = 65536
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
# The launcher's fixed own-task startup situation (same pattern as sandbox/mind/step2/q4.py STARTUP_OWN_TASK):
# it may name only the lane this process has declared consistent; any other lane token refuses before the chain.
_STARTUP_OWN_TASK = re.compile(r"(?P<lane>[a-z0-9][a-z0-9-]{0,63}) startup read: own current task\b")
_IDENTITY_KEYS = frozenset({"schema", "agent", "agent_uuid", "worktree", "cwd", "owner_token_sha256", "caller_pid",
                            "run_id", "descriptor_sha256"})
_INPUT_KEYS = frozenset({"fleet_bytes", "expected_fleet_sha256", "binding_bytes", "expected_binding_sha256",
                         "bundle_root", "public_plan_path", "executable", "expected_interpreter_sha256",
                         "interpreter_max_bytes", "fixed_plan_bytes", "expected_fixed_plan_sha256",
                         "fixed_bundle_bytes", "runtime_manifest_bytes", "cwd", "timeout_seconds",
                         "max_output_bytes"})
_DESCRIPTOR_KEYS = frozenset({"schema", "fleet_sha256", "lanes", "bundle_root", "public_plan", "interpreter", "cwd",
                              "binding_path", "fixed_plan_path", "snapshot_path", "runtime_manifest_path",
                              "interpreter_sha256", "binding_sha256", "fixed_plan_sha256", "snapshot_sha256",
                              "runtime_manifest_sha256", "interpreter_max_bytes", "timeout_seconds",
                              "max_output_bytes", "outer", "expected_binding"})
_BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                           "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256"})
# optional card route decision pin; when the descriptor has it, the answer binding must carry the same value
_CARD_BINDING_KEYS = _BINDING_KEYS | {"card_decision_sha256"}
_STAGED_BINDING_KEYS = _CARD_BINDING_KEYS | {"card_policy_sha256"}
_CONTENT_BINDING_KEYS = frozenset({"encoder_sha256", "policy_sha256", "roster_sha256",
                                   "content_decision_sha256", "descriptor_sha256", "memory_sha256",
                                   "projection_sha256"})
_ANSWER_KEYS = frozenset({"schema", "lane", "status", "reason", "result_count", "results", "binding", "trust"})
_ANSWER_SCHEMA = "wd.q4-lane-answer.v1"
_ITEM_KEYS = frozenset({"ref_sha256", "doc_id_sha256", "stored_text"})
_TRUST = {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN",
          "projection_public_attestation": "UNKNOWN", "transport": "caller_injected", "projection": "caller_pinned"}
_MAX_RESULTS = 100
_VALIDATED = frozenset({"ANSWER", "ABSTAIN", "DEGRADED"})
_RESULT_KEYS = ("schema", "status", "lane", "lane_binding", "lane_authentication", "cleanup", "inner_status",
                "result_count", "answer", "answer_sha256", "identity_sha256", "descriptor_sha256", "call_id")

_QUARANTINED = False  # Pessimistic: set before every chain call, cleared only after a confirmed outcome.
_process_cwd = os.getcwd  # The actual process cwd the identity document must declare (seam for tests only).
_LOCK = threading.Lock()  # One chain call at a time in this process; a concurrent call refuses caller_busy.
_HOLD = []  # Attestation leases retained after an unconfirmed close, for explicit handling only.


class CallerRefused(ValueError):
    """Fixed refusal: ``caller_refused``, ``caller_quarantined`` or ``caller_busy``; no input, path or detail inside."""


class CallerCleanupUnknown(RuntimeError):
    """Pre-check cleanup UNKNOWN: an attestation or transport cleanup-unknown error was raised before the chain
    call (roster or descriptor stage). Fixed message ``caller_cleanup_unknown``, no attributes, no handle, lease,
    query or token inside; the process quarantine is set and an attestation lease, if any, is retained in
    ``_HOLD``. Deliberately not a ``CallerRefused`` so a CLI can map it to its cleanup-unknown exit code."""


def _refuse():
    raise ValueError()


def _unique(pairs):
    document = {}
    for key, value in pairs:
        if type(key) is not str or key in document:
            _refuse()
        document[key] = value
    return document


def _constant(_value):
    _refuse()


def _parse(raw, expected_sha256, keys, schema):
    if (type(raw) is not bytes or not 0 < len(raw) <= MAX_DOCUMENT_BYTES or type(expected_sha256) is not str
            or _HEX64.fullmatch(expected_sha256) is None
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)):
        _refuse()
    document = json.loads(raw.decode("ascii", "strict"), object_pairs_hook=_unique, parse_constant=_constant)
    if type(document) is not dict or frozenset(document) != keys or document["schema"] != schema:
        _refuse()
    return document


_ASCII_FOLD = {ord(letter): ord(letter.lower()) for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}


def _parts(text):
    # Only ASCII letters fold: str.casefold maps KELVIN SIGN, LONG S or SHARP S onto ASCII letters and
    # would equate distinct NTFS directories. Empty parts (UNC prefix, doubled separator, drive-less
    # root) are refused rather than dropped; one trailing separator is tolerated.
    if type(text) is not str or not text or any(ord(c) < 32 or ord(c) == 127 for c in text):
        _refuse()
    parts = tuple(part.translate(_ASCII_FOLD) for part in text.replace("/", "\\").rstrip("\\").split("\\"))
    if any(part in ("", ".", "..") for part in parts):
        _refuse()
    return parts


def _fleet_worktree(fleet_bytes, agent):
    """The worktree the pinned fleet document records for ``agent`` (path parts); fails closed when absent.

    The lookup covers the four projection lanes under ``lanes`` and the tools lane under ``tools_supervisor``
    (where the real manifest keeps ``codex-tools-1`` with its worktree). Exactly one entry across both may name
    the agent, and it must carry a string worktree; a missing or non-dict ``tools_supervisor``, a missing,
    non-string or empty worktree, or an agent listed twice is a refusal.
    """
    document = json.loads(fleet_bytes.decode("utf-8", "strict"), object_pairs_hook=_unique, parse_constant=_constant)
    lanes = document.get("lanes") if type(document) is dict else None
    supervisor = document.get("tools_supervisor") if type(document) is dict else None
    if type(lanes) is not list or type(supervisor) is not dict:
        _refuse()
    entries = [entry for entry in [*lanes, supervisor] if type(entry) is dict and entry.get("agent") == agent]
    if len(entries) != 1 or type(entries[0].get("worktree")) is not str:
        _refuse()
    return _parts(entries[0]["worktree"])


def _consistent_identity(identity, descriptor_sha256, fleet_bytes, expected_fleet_sha256):
    """Declared consistency of the identity document with this process, the pinned fleet and the descriptor pin."""
    if (identity["agent_uuid"] is None or type(identity["agent_uuid"]) is not str
            or _UUID.fullmatch(identity["agent_uuid"]) is None
            or type(identity["caller_pid"]) is not int or identity["caller_pid"] != os.getpid()
            or type(identity["owner_token_sha256"]) is not str or _HEX64.fullmatch(identity["owner_token_sha256"]) is None
            or type(identity["run_id"]) is not str or not identity["run_id"]
            or type(identity["descriptor_sha256"]) is not str
            or not hmac.compare_digest(identity["descriptor_sha256"], descriptor_sha256)
            or type(identity["agent"]) is not str):
        _refuse()
    worktree, cwd = _parts(identity["worktree"]), _parts(identity["cwd"])
    if not worktree or cwd[:len(worktree)] != worktree or cwd != _parts(_process_cwd()):
        _refuse()
    snapshot, _digest = roster.build_roster_snapshot(fleet_bytes, expected_fleet_sha256, enabled=True)
    members = json.loads(snapshot.decode("ascii"))
    entries = [*members["lanes"], members["tools_supervisor"]]
    expected = [entry["agent_uuid"] for entry in entries if entry["agent"] == identity["agent"]]
    if len(expected) != 1 or not hmac.compare_digest(expected[0], identity["agent_uuid"]):
        _refuse()
    if worktree != _fleet_worktree(fleet_bytes, identity["agent"]):
        _refuse()
    return identity["agent"]


def _consistent_descriptor(descriptor, agent, inputs):
    """The installed descriptor pins and paths must equal the chain inputs the caller supplies."""
    lanes = descriptor["lanes"]
    if type(lanes) is not list or not all(type(lane) is str for lane in lanes) or agent not in lanes:
        _refuse()
    binding = descriptor["expected_binding"]
    if (type(binding) is not dict or frozenset(binding) not in (_BINDING_KEYS, _CARD_BINDING_KEYS, _STAGED_BINDING_KEYS, _CONTENT_BINDING_KEYS)
            or not all(type(value) is str and _HEX64.fullmatch(value) for value in binding.values())
            or type(descriptor["outer"]) is not dict):
        _refuse()
    pins = (("fleet_sha256", inputs["expected_fleet_sha256"]),
            ("binding_sha256", inputs["expected_binding_sha256"]),
            ("fixed_plan_sha256", inputs["expected_fixed_plan_sha256"]),
            ("interpreter_sha256", inputs["expected_interpreter_sha256"]),
            ("snapshot_sha256", hashlib.sha256(inputs["fixed_bundle_bytes"]).hexdigest()),
            ("runtime_manifest_sha256", hashlib.sha256(inputs["runtime_manifest_bytes"]).hexdigest()))
    for key, expected in pins:
        value = descriptor[key]
        if (type(value) is not str or type(expected) is not str or _HEX64.fullmatch(value) is None
                or not hmac.compare_digest(value, expected)):
            _refuse()
    for key, expected in (("bundle_root", inputs["bundle_root"]), ("public_plan", inputs["public_plan_path"]),
                          ("interpreter", inputs["executable"]), ("cwd", inputs["cwd"])):
        if type(descriptor[key]) is not str or type(expected) is not str or descriptor[key] != expected:
            _refuse()
    for key in ("interpreter_max_bytes", "timeout_seconds", "max_output_bytes"):
        value, expected = descriptor[key], inputs[key]
        if type(value) is not int or type(expected) is not int or value != expected:
            _refuse()
    return {key: binding[key] for key in sorted(binding)}


def _validated(answer, agent, expected_binding):
    """``(inner_status, answer, result_count, answer_sha256)`` for a closed chain answer of the declared lane; else ``None``.

    Every field is re-checked against the closed shape the existing chain produces (schema, fixed reason, the three
    UNKNOWN trust fields, resolved items of exactly three string fields); the canonical bytes are computed here so
    that nothing is accepted that cannot be hashed.
    """
    if (type(answer) is not dict or frozenset(answer) != _ANSWER_KEYS or answer["schema"] != _ANSWER_SCHEMA
            or type(answer["status"]) is not str or type(answer["lane"]) is not str or answer["lane"] != agent
            or type(answer["reason"]) is not str or type(answer["trust"]) is not dict or answer["trust"] != _TRUST
            or any(type(value) is not str for value in answer["trust"].values())
            or type(answer["results"]) is not list or len(answer["results"]) > _MAX_RESULTS
            or type(answer["result_count"]) is not int or answer["result_count"] != len(answer["results"])):
        return None
    if answer["status"] == "REFUSED":
        if answer["results"] or answer["binding"] is not None or answer["reason"] != "runtime_refused":
            return None
        return "REFUSED", None, 0, None
    binding = answer["binding"]
    if (answer["status"] not in _VALIDATED or answer["reason"] != "" or type(binding) is not dict
            or frozenset(binding) != frozenset(expected_binding)
            or any(type(binding[key]) is not str or not hmac.compare_digest(binding[key], expected_binding[key])
                   for key in expected_binding)):
        return None
    for item in answer["results"]:
        if (type(item) is not dict or frozenset(item) != _ITEM_KEYS
                or any(type(value) is not str for value in item.values())
                or _HEX64.fullmatch(item["ref_sha256"]) is None or _HEX64.fullmatch(item["doc_id_sha256"]) is None):
            return None
    try:
        canonical = json.dumps(answer, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                               allow_nan=False).encode("ascii")
    except (TypeError, ValueError):
        return None
    return answer["status"], answer, answer["result_count"], hashlib.sha256(canonical).hexdigest()


class _AdapterOwner:
    """Per-call cleanup callback adapter, not a guard or native witness.

    The trusted integrator must supply callbacks for the distinct call-bound
    adapter ticket, never a transport or attestation ticket. Completion is
    offered only after the adapter's own clean path and its lock release;
    it proves neither native child cleanup nor identity. UNKNOWN is monotonic
    at the registry, and any callback failure or interruption must prevent
    witness use. The enclosing caller must abort in a finally block if this
    helper is interrupted, and must not consume a witness until the entire
    chain and every owner's callbacks finish. Private Python objects are a
    cooperative convention, not protection from same-user tampering.
    """

    def __init__(self, *, on_begin, on_complete, on_unknown):
        if not all(callable(value) for value in (on_begin, on_complete, on_unknown)):
            raise CallerRefused("caller_refused")
        self._on_begin, self._on_complete, self._on_unknown = on_begin, on_complete, on_unknown
        self._started = self._ready = self._done = self._uncertain = False

    def _begin(self):
        if self._started or self._done or self._uncertain:
            raise CallerRefused("caller_refused")
        self._started = True
        try:
            self._on_begin()
            if self._uncertain:
                raise CallerCleanupUnknown("caller_cleanup_unknown")
        except BaseException:
            self._unknown()
            raise

    def _body_clean(self):
        if not self._started or self._ready or self._done or self._uncertain:
            raise CallerRefused("caller_refused")
        self._ready = True

    def _after_release(self):
        if not self._started or not self._ready or self._done or self._uncertain:
            self._unknown()
            return
        try:
            self._on_complete()
            if self._uncertain:
                raise CallerCleanupUnknown("caller_cleanup_unknown")
            self._done = True
        except BaseException:
            self._unknown()
            raise

    def _unknown(self):
        if not self._uncertain:
            self._uncertain = True
            self._on_unknown()


def call_lane_session(*, enabled=False, identity_bytes=None, expected_identity_sha256=None, descriptor_bytes=None,
                      expected_descriptor_sha256=None, situation=None, time=None, inputs=None, _owner=None,
                      _owner_guard=None, _owner_context=None):
    """Call the existing chain; the outer caller must abort any unconsumed context."""
    if enabled is False and _owner is None:
        return {"status": "OFF"}
    if _owner_guard is not None or _owner_context is not None:
        accepted = False
        try:
            if (type(_owner_guard) is not completion._Guard or _owner_context is None or _owner is not None
                    or _owner_guard._accept_owner_context(_owner_context) is not _owner_context):
                _refuse()
            ticket = _owner_context._ticket("adapter")
            if _owner_context._accept_ticket("adapter", ticket) is not ticket:
                _refuse()
            _owner = _AdapterOwner(on_begin=ticket._begin, on_complete=ticket._complete, on_unknown=ticket._unknown)
            accepted = True
        except BaseException as error:
            if not isinstance(error, Exception):
                del identity_bytes, descriptor_bytes, situation, time, inputs
                raise
        if not accepted:
            del identity_bytes, descriptor_bytes, situation, time, inputs
            raise CallerRefused("caller_refused")
    if _owner is None:
        if enabled is False:
            return {"status": "OFF"}
        if not _LOCK.acquire(blocking=False):
            raise CallerRefused("caller_busy")
        try:
            if _QUARANTINED:
                raise CallerRefused("caller_quarantined")
            return _call_locked(enabled, identity_bytes, expected_identity_sha256, descriptor_bytes,
                                expected_descriptor_sha256, situation, time, inputs)
        finally:
            _LOCK.release()
    if type(_owner) is not _AdapterOwner:
        del identity_bytes, descriptor_bytes, situation, time, inputs
        raise CallerRefused("caller_refused")
    try:
        _owner._begin()
    except BaseException:
        del identity_bytes, descriptor_bytes, situation, time, inputs
        _owner._unknown()
        raise
    if enabled is False:
        del identity_bytes, descriptor_bytes, situation, time, inputs
        _owner._unknown()
        return {"status": "OFF"}
    try:
        acquired = _LOCK.acquire(blocking=False)
    except BaseException:
        del identity_bytes, descriptor_bytes, situation, time, inputs
        _owner._unknown()
        raise
    if not acquired:
        del identity_bytes, descriptor_bytes, situation, time, inputs
        _owner._unknown()
        raise CallerRefused("caller_busy")
    returned = False
    try:
        if _QUARANTINED:
            raise CallerRefused("caller_quarantined")
        result = _call_locked(enabled, identity_bytes, expected_identity_sha256, descriptor_bytes,
                              expected_descriptor_sha256, situation, time, inputs, _owner=_owner,
                              _owner_guard=_owner_guard, _owner_context=_owner_context)
        returned = True
    finally:
        try:
            _LOCK.release()
        except BaseException:
            del identity_bytes, descriptor_bytes, situation, time, inputs
            _owner._unknown()
            raise
        del identity_bytes, descriptor_bytes, situation, time, inputs
        if returned:
            try:
                _owner._after_release()
            except BaseException:
                result = None
                raise
        else:
            _owner._unknown()
    return result


def _call_locked(enabled, identity_bytes, expected_identity_sha256, descriptor_bytes, expected_descriptor_sha256,
                 situation, time, inputs, _owner=None, _owner_guard=None, _owner_context=None):
    global _QUARANTINED
    agent = binding = identity = descriptor = chain_inputs = None
    unknown_cleanup = False
    try:
        if (enabled is not True or type(inputs) is not dict or frozenset(inputs) != _INPUT_KEYS
                or type(inputs["fixed_bundle_bytes"]) is not bytes or type(inputs["runtime_manifest_bytes"]) is not bytes):
            _refuse()
        identity = _parse(identity_bytes, expected_identity_sha256, _IDENTITY_KEYS, IDENTITY_SCHEMA)
        descriptor = _parse(descriptor_bytes, expected_descriptor_sha256, _DESCRIPTOR_KEYS, DESCRIPTOR_SCHEMA)
        agent = _consistent_identity(identity, expected_descriptor_sha256, inputs["fleet_bytes"],
                                     inputs["expected_fleet_sha256"])
        binding = _consistent_descriptor(descriptor, agent, inputs)
        if (_owner_guard is not None and
                _owner_guard._binding[1:] != (agent, expected_identity_sha256, expected_descriptor_sha256)):
            _refuse()
        named = _STARTUP_OWN_TASK.match(situation) if type(situation) is str else None
        if named is not None and named.group("lane") != agent:
            _refuse()
        chain_inputs = dict(inputs)
    except AttestationCleanupUnknown as exc:
        agent, unknown_cleanup = None, True
        _HOLD.append(exc.lease)
    except TransportCleanupUnknown:
        agent, unknown_cleanup = None, True
    except Exception:
        agent = None
    finally:
        identity = descriptor = None
    if agent is None:
        del identity_bytes, descriptor_bytes, situation, time, inputs, chain_inputs
        if unknown_cleanup:
            _QUARANTINED = True
            raise CallerCleanupUnknown("caller_cleanup_unknown") from None
        raise CallerRefused("caller_refused")
    result = {"schema": SCHEMA, "status": "REFUSED", "lane": agent, "lane_binding": "DECLARED_CONSISTENT",
              "lane_authentication": "UNKNOWN", "cleanup": "chain_confirmed", "inner_status": "session_refused",
              "result_count": 0, "answer": None, "answer_sha256": None, "identity_sha256": expected_identity_sha256,
              "descriptor_sha256": expected_descriptor_sha256, "call_id": secrets.token_hex(16)}
    signal = None
    confirmed = False
    owner_eligible = False
    _QUARANTINED = True  # pessimistic: only a confirmed outcome below clears it
    try:
        if _owner_guard is None:
            answer = session.query_session(lane=agent, situation=situation, time=time, **chain_inputs, enabled=True)
        else:
            answer = session.query_session(lane=agent, situation=situation, time=time, **chain_inputs, enabled=True,
                                           _owner_guard=_owner_guard, _owner_context=_owner_context)
        validated = _validated(answer, agent, binding)
        if validated is None:
            result["inner_status"] = "output_refused"
        else:
            result["inner_status"], result["answer"], result["result_count"], result["answer_sha256"] = validated
            if result["answer"] is not None:
                result["status"] = "VALIDATED"
                owner_eligible = True
        confirmed = True
    except AttestationCleanupUnknown as exc:
        _HOLD.append(exc.lease)
        result["status"], result["cleanup"], result["inner_status"] = "UNKNOWN", "unknown", "attestation_cleanup_unknown"
    except TransportCleanupUnknown:
        result["status"], result["cleanup"], result["inner_status"] = "UNKNOWN", "unknown", "transport_cleanup_unknown"
    except TimeoutError:
        result["status"], result["cleanup"], result["inner_status"] = "UNKNOWN", "unknown", "timeout"
    except KeyboardInterrupt:
        signal = KeyboardInterrupt()
    except SystemExit as exc:
        signal = SystemExit(exc.code if exc.code is None or type(exc.code) is int else 1)
    except Exception:
        result["status"], result["cleanup"], result["inner_status"] = "UNKNOWN", "unknown", "session_refused"
    finally:
        answer = validated = None
        del situation, time, inputs, chain_inputs, identity_bytes, descriptor_bytes
    if signal is not None:
        raise signal
    if confirmed:
        _QUARANTINED = False
    if owner_eligible and _owner is not None:
        _owner._body_clean()
    return {key: result[key] for key in _RESULT_KEYS}
