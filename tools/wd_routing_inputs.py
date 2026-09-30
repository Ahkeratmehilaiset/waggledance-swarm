"""F19 W1: a pure input assembler for ``tools.wd_routing_capacity.compose``.

``assemble`` receives documents a caller has ALREADY loaded (one lane-evidence record per worker,
collector rows, pacer windows, durable attempts, the routing policy, an optional signed policy and
shadow weights) and one aware ``now``. It returns compose()'s keyword inputs, the provenance of each
document and an explicit unknown reason for every record it withheld. It reads no clock, file,
environment or provider, and grants no authority: its output is only an input to compose(), whose
own result stays ``authority: "none"``.

What it never does:

* It never synthesizes readiness, a subject, a role, a qualification or a signed policy. A foreign,
  duplicate, malformed, stale or future-dated lane record is withheld whole; a lane whose subject is
  unknown keeps its worker record but gets no subject, so compose() reports ``subject_unknown``.
* It never binds a subject to a provider. A provider-typed subject (the pool-binding receipt's
  ``{"kind", "id"}``) is checked for its grammar only and handed on as a fresh copy; compose() binds it
  to its own provider's row against the signed ``profile_providers``, or refuses it. A text subject
  stays representable, but compose() never binds it to capacity (``subject_unbound``).
* It never supplies capacity, load or single-flight state: lane evidence carrying any of them is
  malformed. Capacity comes only through compose()'s adapter; reservation state is not assembled here.
* It never alters the caller's task (id, revision, input digest, scope): the task is handed on as a
  copy and its digest recorded. Every document is copied through its canonical JSON text, so the
  copy shares nothing with the caller whatever the caller's types do; a document with no exact JSON
  form (NaN, a set, a tuple, a non-string key) is withheld as ``document_not_canonical_json``.
* Grok gets no subject, role or qualification and stays consult-only and unranked. Shadow weights
  are passed through untouched for compose() to keep separate from the ranked order.

Provenance: ``document_digest`` is ``tools.wd_composer_select.digest`` of the parsed document (the
sha256 of its canonical JSON). A parsed document cannot prove the bytes it was read from, so the
path and byte sha256 a caller reports are passed through as caller claims with
``byte_digest_verified: False``. The signed policy is passed through UNVERIFIED
(``signature_verified: False``): the loader and the signature check are not implemented here.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from tools.bridge_pool_binding import HEX64, SESSION_RE, SUBJECT_KEYS, SUBJECT_KINDS, _aware_utc
from tools.wd_composer_select import digest
from tools.wd_task_router import GROK, MEMBERS, WORKER_SCHEMA

SCHEMA = "wd.routing-inputs.v1"
LANE_EVIDENCE_SCHEMA = "wd.routing-lane-evidence.v1"
LANE_REQUIRED = ("schema", "worker", "kind", "profile_id", "subject", "observed_utc")
LANE_OPTIONAL = ("role", "qualification")
DOCUMENTS = ("task", "lanes", "rows", "paced", "signed_policy", "prepared_artifacts", "routing_policy",
             "shadow_weights")
DIGEST_BASIS = "sha256 of the canonical JSON of the parsed document; not the original bytes"
MAX_LANE_EVIDENCE_AGE_SECONDS = 900     # the capacity adapter's own sample bound (wd_capacity_pacing)
MAX_TEXT = 256                          # compose's _label bound for a subject
MAX_PROFILE_TEXT = 128                  # compose's _label bound for a profile_id (wd_routing_capacity._worker)
MAX_PATH_TEXT = 1024
HEX = frozenset("0123456789abcdef")
# What this slice does NOT derive, stated instead of guessed (Lead 19:23:43Z).
CONTRACT_UNKNOWNS = (
    "profile_id is caller-bound: no registry-v2 lookup is made here",
    "subject is caller-bound: deriving it from a wd.pool-binding-decision.v1 is the reader's (W2)",
    "a typed subject is checked for its pool-binding grammar only: it is not a measured current profile or"
    " native ownership, and which provider it binds is compose's check against the signed profile_providers",
    "load and single_flight are not assembled: reservation state is W3's",
    "original bytes and the signed policy's signature are not verified here",
)


def _text(value: Any, limit: int = MAX_TEXT) -> bool:
    """Text compose's _label also admits (exact str, 1..limit characters, no outer whitespace), and printable."""
    return type(value) is str and 0 < len(value) <= limit and value.strip() == value and value.isprintable()


def _typed_subject(value: Any) -> dict | None:
    """A provider-typed subject in the pool-binding receipt grammar ({"kind", "id"}, exact types; an auth_context id
    is 64 lowercase hex, a native_session id matches SESSION_RE) as a fresh dict, or None."""
    if type(value) is not dict or set(value) != SUBJECT_KEYS:
        return None
    kind, ident = value["kind"], value["id"]
    if type(kind) is not str or kind not in SUBJECT_KINDS.values() or type(ident) is not str:
        return None
    pattern = HEX64 if kind == SUBJECT_KINDS["codex"] else SESSION_RE
    return {"kind": kind, "id": ident} if pattern.fullmatch(ident) is not None else None


def _canonical_copy(value: Any) -> tuple[bool, Any]:
    """(True, a fresh copy built from the canonical JSON text), or (False, None) when the value has no exact JSON
    form (NaN, a set, a tuple, a non-string key, anything json cannot encode). Built from the text, the copy shares
    nothing with the caller, whatever the caller's types do on deepcopy (RCO2 W1-F1, W1-F2)."""
    try:
        copied = json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                       allow_nan=False))
        exact = copied == value
    except Exception:  # noqa: BLE001 - a value json cannot encode or compare is not a parsed document
        return False, None
    return (True, copied) if exact is True else (False, None)


def _instant(value: Any) -> datetime | None:
    """ISO text with an explicit offset as aware UTC, or None."""
    if type(value) is not str or len(value) > 64:
        return None
    try:
        return _aware_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


def _source(entry: Any) -> dict | None:
    """A caller's claim about where a document came from, or None when it is malformed."""
    if type(entry) is not dict or set(entry) != {"path", "byte_sha256"}:
        return None
    path, byte_sha256 = entry["path"], entry["byte_sha256"]
    if not _text(path, MAX_PATH_TEXT):
        return None
    if type(byte_sha256) is not str or len(byte_sha256) != 64 or not set(byte_sha256) <= HEX:
        return None
    return {"path": path, "byte_sha256": byte_sha256}


def _lane(record: Any, now: datetime | None) -> tuple[dict | None, Any, list[str]]:
    """(worker record, subject, reasons) for one lane record; a None worker means withheld whole. The subject is
    a text label (representable; compose never binds it to capacity) or a fresh typed {kind, id} dict."""
    if type(record) is not dict or not set(LANE_REQUIRED) <= set(record) \
            or not set(record) <= set(LANE_REQUIRED + LANE_OPTIONAL) or record["schema"] != LANE_EVIDENCE_SCHEMA \
            or not _text(record["worker"]) or not _text(record["kind"]) \
            or not _text(record["profile_id"], MAX_PROFILE_TEXT):
        return None, None, ["lane_evidence_malformed"]
    worker, kind, subject = record["worker"], record["kind"], record["subject"]
    if worker != GROK and worker not in MEMBERS:
        return None, None, ["foreign_worker"]
    if (worker == GROK) != (kind == GROK) or kind not in ("lane", GROK):
        return None, None, ["kind_mismatch"]
    if worker == GROK and subject is not None:
        return None, None, ["grok_subject_refused"]
    if subject is not None and not _text(subject):
        subject = _typed_subject(subject)
        if subject is None:
            return None, None, ["subject_malformed"]
    observed = _instant(record["observed_utc"])
    if observed is None:
        return None, None, ["lane_evidence_malformed"]
    if now is None:
        return None, None, ["evidence_age_unknown"]
    if observed > now:
        return None, None, ["lane_evidence_future"]
    if now - observed > timedelta(seconds=MAX_LANE_EVIDENCE_AGE_SECONDS):
        return None, None, ["lane_evidence_stale"]
    assembled = {"schema": WORKER_SCHEMA, "worker": worker, "kind": kind, "profile_id": record["profile_id"]}
    reasons = [] if subject is not None or worker == GROK else ["subject_unknown"]
    if "role" in record:
        role = record["role"]
        if worker == GROK:
            reasons.append("grok_role_refused")     # consult-only: never a role to rank on (RCO2 W1-F4)
        elif type(role) is dict and role.get("worker") == worker:
            assembled["role"] = role            # the lanes document is already a private copy
        else:
            reasons.append("role_foreign_or_malformed")
    if "qualification" in record:
        receipts = record["qualification"]
        if worker == GROK:
            reasons.append("grok_qualification_refused")
        elif type(receipts) is list and all(type(receipt) is dict for receipt in receipts):
            assembled["qualification"] = receipts
        else:
            reasons.append("qualification_malformed")
    return assembled, subject, reasons


def assemble(task: Any, lanes: Any, rows: Any, paced: Any, prepared_artifacts: Any, routing_policy: Any,
             now: Any, signed_policy: Any = None, shadow_weights: Any = None, sources: Any = None) -> dict:
    """compose()'s keyword inputs (without ``now``) plus provenance and unknown reasons. Pure."""
    reasons: list[str] = []
    current = _aware_utc(now)
    if current is None:
        reasons.append("now_invalid")
    documents = {"task": task, "lanes": lanes, "rows": rows, "paced": paced, "signed_policy": signed_policy,
                 "prepared_artifacts": prepared_artifacts, "routing_policy": routing_policy,
                 "shadow_weights": shadow_weights}
    for name in DOCUMENTS:      # private copies: a later change by the caller cannot reach the inputs
        exact, documents[name] = _canonical_copy(documents[name])
        if not exact:           # not a parsed JSON document; withheld, never passed on shared
            reasons.append("document_not_canonical_json:" + name)
    if documents["signed_policy"] is not None and type(documents["signed_policy"]) is not dict:
        documents["signed_policy"] = None
        reasons.append("signed_policy_malformed")
    claims: dict[str, dict] = {}
    if sources is not None:
        if type(sources) is not dict or not set(sources) <= set(DOCUMENTS):
            reasons.append("sources_malformed")
        else:
            for name in DOCUMENTS:
                if name in sources:
                    claim = _source(sources[name])
                    if claim is None:
                        reasons.append("source_malformed:" + name)
                    else:
                        claims[name] = claim
    provenance = {}
    for name in DOCUMENTS:
        claim = claims.get(name, {})
        provenance[name] = {"document_digest": digest(documents[name]), "digest_basis": DIGEST_BASIS,
                            "caller_path": claim.get("path"), "caller_byte_sha256": claim.get("byte_sha256"),
                            "byte_digest_verified": False}
    provenance["signed_policy"]["signature_verified"] = False
    workers: list[dict] = []
    subjects: dict[str, Any] = {}
    unknown: list[dict] = []
    records = documents["lanes"]
    if type(records) is not list:
        reasons.append("lanes_malformed")
        records = []
    names = [record.get("worker") if type(record) is dict else None for record in records]
    for index, record in enumerate(records):
        name = names[index] if type(names[index]) is str else None
        if name is not None and names.count(name) > 1:
            assembled, subject, withheld = None, None, ["lane_evidence_ambiguous"]
        else:
            assembled, subject, withheld = _lane(record, current)
        if assembled is not None:
            workers.append(assembled)
            if subject is not None:
                subjects[assembled["worker"]] = subject
        if withheld:
            unknown.append({"index": index, "worker": name, "reasons": withheld})
    inputs = {"task": documents["task"], "workers": sorted(workers, key=lambda worker: worker["worker"]),
              "subjects": subjects, "rows": documents["rows"], "paced": documents["paced"],
              "signed_policy": documents["signed_policy"], "prepared_artifacts": documents["prepared_artifacts"],
              "routing_policy": documents["routing_policy"], "shadow_weights": documents["shadow_weights"]}
    return {"schema": SCHEMA, "authority": "none", "execution_allowed": False,
            "now_utc": current.isoformat() if current is not None else None, "inputs": inputs,
            "provenance": provenance, "unknown": unknown, "reasons": reasons,
            "contract_unknowns": list(CONTRACT_UNKNOWNS)}
