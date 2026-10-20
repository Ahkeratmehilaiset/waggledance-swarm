"""Dormant, supplied-byte M1-P3 case/commitment glue. Standard library only.

Source authors must never invoke this module on private study inputs. Authorized
data-side construction receives immutable capture manifest/seal/blobs, already
verified recorder incidents and capture-adapter evidence, and an externally
frozen GOLD-BLIND privacy/applicability pass. No transcript or live-memory reads,
history reconstruction, ranking, grading, CLI, file publication or import-time
nonce generation occur here. This is the NEW strict prospective recipe, not the
old DAAE helper's historical reconstruction.

Bindings.from_bytes consumes a hash-bound case adapter capsule plus the exact
LOCKED protocol bytes. The capsule itself must be pinned in the final executable
manifest; supplying bytes/hashes is not study authorization. CaseInput carries
exact incident/evidence bytes and their independently supplied expected hashes.
The upstream collector must have validated the exact native session/UUID/tool
record through prospective_capture.extract_action_evidence; hashing arbitrary
caller JSON does not authenticate it. This module verifies those bindings, not
native-record authenticity. Opaque action_ref is NEVER interpreted as a UUID.
Bridge reporter session labels are distinct from native transcript UUIDs: the
original LOCKED protocol must contain reporter_sources, an exact-cover list of
{reporter: {agent, agent_uuid, session_id}, native_session_id} mappings.
Native action precision is pinned to 1,000us independently of the baseline's
1,000,000us bound and recorder's one-second interval. The exact native instant
and raw record hash are retained; the entire native interval must fit inside
the recorder's same floorsecond interval. No guessed clock or normalization.

construct_cases returns immutable PRIVATE bytes: cases.pre.json, cases.json,
commitments.json, strict blind JSONL, one fixed snapshot, and aggregate counts.
Only blind_jsonl has executor fields (id/situation/snapshot_sha256). Snapshot
entries are {basename: {sha256, mtime}}, with mtime the conservative baseline
seal-availability bound, never a historical lesson-write timestamp. The same
fixed snapshot applies to every case. Bodies remain in supplied capture blobs.

No writer/runner is wired. Final commands must still enforce collection close,
one construction/arm/grader invocation, pinned runtime/RNG and role identities,
fresh separate nomination/arm/score trees, and post-arm release of score bytes.
Reuse the capture sibling's safe writer for any later blind publication. Tree
separation is procedural on a shared filesystem, not OS confidentiality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import hashlib
import json
import random
import re
import secrets
import uuid

from sandbox.mind.measurement_tools import prospective_capture as capture


MIN_N, CAP_KEPT, SEED = 40, 60, 20261019
MEASURED_SOURCE = "6cf81dbbefe2b0e19a4c8306100beb30784ac4e4"
BM25_SHA256 = "a1667a27da015dcaed0f97960bbede3281dab3540018106d2458879db213579a"
GRADER_SHA256 = "074b443657daf72797b66994fd6afa5f2f4dd9384c26d3b955e429f451740117"
EVIDENCE_SOURCE_SHA256 = "4203b3215c446020b57940b69deccbf3694af4b936af468685a439867a300b12"
GRADER_REASONS = frozenset({
    "near_miss", "mark_ambiguous", "instance_time_unstated", "instance_time_ambiguous",
    "no_prior_lesson", "multiple_golds", "duplicate_incident", "anchor_not_found",
    "anchor_ambiguous", "gold_impossible_date", "gold_unreconstructable", "gold_same_day",
    "gold_not_available", "situation_empty", "leak", "empty_applicable_corpus",
    "anchor_disputed", "situation_guarded", "gold_engine_invisible", "duplicate_gold", "not_sampled",
})
COLLECTOR_REASONS = frozenset({"schema", "size", "time", "duplicate"})
_ERRORS = frozenset({"schema", "binding", "baseline", "identity", "clock", "prerequisite",
                     "duplicate", "privacy_pass", "nonce", "budget"})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.md\Z", re.I | re.ASCII)
_AGENT = re.compile(r"[a-z][a-z0-9-]{0,39}\Z")
_UUID = re.compile(r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\Z")
_SESSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}\Z")
BUDGET_BYTES = 64 * 1024 * 1024


class NotReady(Exception):
    """Fatal STOP_NOT_READY, with closed sanitized reasons only."""
    def __init__(self, reason):
        self.reason = reason if type(reason) is str and reason in _ERRORS else "schema"
        super().__init__("STOP_NOT_READY:" + self.reason)


def _need(condition, reason="schema"):
    if not condition:
        raise NotReady(reason)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _hex(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _id(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _keys(value, keys):
    _need(type(value) is dict and set(value) == set(keys))


def _canon(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise NotReady("schema") from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result)
        result[key] = value
    return result


def _json(data, expected=None):
    _need(type(data) is bytes and 0 < len(data) <= BUDGET_BYTES, "budget")
    if expected is not None:
        _need(_hex(expected) and _sha(data) == expected, "binding")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(NotReady("schema")))
    except (UnicodeError, ValueError, RecursionError):
        raise NotReady("schema") from None


def _time(value):
    try:
        return capture.parse_utc(value)
    except capture.CaptureError:
        raise NotReady("clock") from None


def _identity(value):
    _keys(value, ("agent", "agent_uuid", "session_id"))
    _need(type(value["agent"]) is str and _AGENT.fullmatch(value["agent"])
          and value["agent"] not in ("fable-5", "claude-rco-1"), "identity")
    # Exact frozen recorder grammar: bridge session_id is a run label, and is
    # never UUID-parsed, normalized or equated with a native transcript UUID.
    _need(type(value["agent_uuid"]) is str and _UUID.fullmatch(value["agent_uuid"])
          and type(value["session_id"]) is str and _SESSION.fullmatch(value["session_id"]), "identity")
    return tuple(value[key] for key in ("agent", "agent_uuid", "session_id"))


def _reporter_sources(lock, reporters):
    sources = lock.get("reporter_sources")
    _need(type(sources) is list and len(sources) == len(reporters), "identity")
    mapping = {}
    for source in sources:
        _keys(source, ("reporter", "native_session_id"))
        reporter = _identity(source["reporter"])
        native = source["native_session_id"]
        _need(reporter in reporters and reporter not in mapping and type(native) is str, "identity")
        try:
            _need(str(uuid.UUID(native)) == native, "identity")
        except (ValueError, AttributeError):
            raise NotReady("identity") from None
        mapping[reporter] = native
    _need(set(mapping) == set(reporters), "identity")
    return mapping


@dataclass(frozen=True, repr=False)
class Bindings:
    """Explicit capsule; no default clocks, reporters, science or source pins."""
    capsule_json: bytes = field(repr=False)
    protocol_json: bytes = field(repr=False)

    @classmethod
    def from_bytes(cls, capsule, *, expected_sha256, protocol, protocol_sha256):
        value = _json(capsule, expected_sha256)
        _keys(value, ("schema", "study_id", "protocol_sha256", "baseline_manifest_sha256",
                      "baseline_seal_sha256", "capture_identity_sha256", "collection_start_utc",
                      "collection_end_utc", "timestamp_resolution_us", "baseline_resolution_us",
                      "max_record_delay_seconds", "allowed_reporters", "privacy_source_sha256",
                      "evidence_source_sha256", "source_commit", "arm_a_version", "arm_b_sha256",
                      "grader_sha256", "minimum_n", "cap_n", "seed"))
        lock = _json(protocol, protocol_sha256)
        _need(type(lock) is dict and lock.get("schema") == "wd.m1-p3-protocol-lock.v1"
              and lock.get("status") == "LOCKED" and lock.get("study_id") == value["study_id"], "binding")
        _need(value["schema"] == "wd.m1-p3-case-bindings.v1" and _id(value["study_id"])
              and value["protocol_sha256"] == protocol_sha256, "binding")
        for key in ("baseline_manifest_sha256", "baseline_seal_sha256", "capture_identity_sha256", "privacy_source_sha256"):
            _need(_hex(value[key]), "binding")
        _need((value["source_commit"], value["arm_a_version"], value["arm_b_sha256"],
               value["grader_sha256"], value["evidence_source_sha256"])
              == (MEASURED_SOURCE, "v21a", BM25_SHA256, GRADER_SHA256, EVIDENCE_SOURCE_SHA256), "binding")
        for key, expected in (("minimum_n", MIN_N), ("cap_n", CAP_KEPT), ("seed", SEED),
                              ("timestamp_resolution_us", 1000), ("baseline_resolution_us", 1_000_000)):
            _need(type(value[key]) is int and value[key] == expected, "binding")
        _need(type(value["max_record_delay_seconds"]) is int
              and 1 <= value["max_record_delay_seconds"] <= 86400, "clock")
        _need(_time(value["collection_start_utc"]) < _time(value["collection_end_utc"]), "clock")
        _need(type(value["allowed_reporters"]) is list and 1 <= len(value["allowed_reporters"]) <= 32, "identity")
        reporters = [_identity(item) for item in value["allowed_reporters"]]
        _need(len(set(reporters)) == len(reporters), "identity")
        _reporter_sources(lock, reporters)
        return cls(bytes(capsule), bytes(protocol))


def _bound(bindings):
    _need(type(bindings) is Bindings, "binding")
    # Revalidate public dataclass construction rather than trusting its type.
    value = _json(bindings.capsule_json)
    checked = Bindings.from_bytes(bindings.capsule_json, expected_sha256=_sha(bindings.capsule_json),
                                 protocol=bindings.protocol_json, protocol_sha256=value.get("protocol_sha256") if type(value) is dict else None)
    value = _json(checked.capsule_json)
    value["_native_sessions"] = _reporter_sources(_json(checked.protocol_json),
                                                 [_identity(item) for item in value["allowed_reporters"]])
    return value


@dataclass(frozen=True, repr=False)
class Baseline:
    manifest_json: bytes = field(repr=False)
    seal_json: bytes = field(repr=False)
    blobs: tuple[tuple[str, bytes], ...] = field(repr=False)

    @classmethod
    def from_bytes(cls, manifest, seal, blobs, *, bindings):
        b = _bound(bindings)
        m = _json(manifest, b["baseline_manifest_sha256"])
        receipt = _json(seal, b["baseline_seal_sha256"])
        _keys(m, ("schema", "identity", "started_utc", "sealed_utc", "files"))
        _keys(receipt, ("schema", "status", "manifest_sha256", "sealed_utc"))
        _need(m["schema"] == "wd.m1-p3-baseline.v1" and receipt["schema"] == "wd.m1-p3-baseline-seal.v1"
              and receipt["status"] == "SEALED" and receipt["manifest_sha256"] == b["baseline_manifest_sha256"], "baseline")
        identity = m["identity"]
        _keys(identity, ("schema", "status", "study_id", "capture_id", "source_dir", "destination",
                         "preregistration_sha256", "protocol_sha256"))
        _need(_sha(_canon(identity)) == b["capture_identity_sha256"]
              and identity["schema"] == "wd.m1-p3-capture-authorization.v1"
              and identity["status"] == "LOCKED_CAPTURE_GO"
              and identity["study_id"] == b["study_id"] and identity["protocol_sha256"] == b["protocol_sha256"], "binding")
        try:
            latest = _time(receipt["sealed_utc"]) + timedelta(microseconds=b["baseline_resolution_us"])
        except OverflowError:
            raise NotReady("clock") from None
        _need(_time(m["started_utc"]) <= _time(m["sealed_utc"]) <= _time(receipt["sealed_utc"])
              and latest < _time(b["collection_start_utc"]), "clock")
        _need(type(m["files"]) is list and 1 <= len(m["files"]) <= 1024, "baseline")
        _need(type(blobs) is dict, "baseline")
        names, hashes, refs, total = [], set(), set(), 0
        for row in m["files"]:
            _keys(row, ("name", "sha256", "bytes"))
            name, digest = row["name"], row["sha256"]
            _need(type(name) is str and _NAME.fullmatch(name) and name.casefold() != "memory.md", "baseline")
            _need(_hex(digest) and type(row["bytes"]) is int and 0 <= row["bytes"] <= BUDGET_BYTES, "baseline")
            body = blobs.get(digest)
            _need(type(body) is bytes and len(body) == row["bytes"] and _sha(body) == digest, "binding")
            # No replacement decoding for corrupt gold text or guessed encoding.
            try:
                body.decode("utf-8")
            except UnicodeError:
                raise NotReady("baseline") from None
            ref = lesson_ref(name)
            _need(ref not in refs, "binding")
            refs.add(ref)
            names.append(name)
            hashes.add(digest)
            total += len(body)
        _need(total + len(manifest) + len(seal) <= BUDGET_BYTES, "budget")
        _need(names == sorted(names, key=lambda n: (n.casefold(), n))
              and len(set(n.casefold() for n in names)) == len(names) and set(blobs) == hashes, "baseline")
        return cls(bytes(manifest), bytes(seal), tuple(sorted(blobs.items())))


def lesson_ref(basename):
    """Exact case-sensitive ASCII basename SHA256 prefix; collision is fatal."""
    _need(type(basename) is str and _NAME.fullmatch(basename) and basename.casefold() != "memory.md", "baseline")
    return _sha(basename.encode("ascii"))[:32]


def fixed_snapshot(baseline, *, bindings):
    """Pure immutable snapshot bytes; conservative baseline availability only."""
    _need(type(baseline) is Baseline, "baseline")
    checked = Baseline.from_bytes(baseline.manifest_json, baseline.seal_json, dict(baseline.blobs), bindings=bindings)
    b = _bound(bindings)
    receipt = _json(checked.seal_json)
    availability = (_time(receipt["sealed_utc"]) + timedelta(microseconds=b["baseline_resolution_us"])).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return _canon({row["name"]: {"sha256": row["sha256"], "mtime": availability}
                   for row in _json(checked.manifest_json)["files"]})


@dataclass(frozen=True, repr=False)
class CaseInput:
    incident_json: bytes = field(repr=False)
    incident_sha256: str = field(repr=False)
    evidence_json: bytes = field(repr=False)
    evidence_sha256: str = field(repr=False)


@dataclass(frozen=True, repr=False)
class CaseArtifacts:
    cases_pre_json: bytes = field(repr=False)
    cases_json: bytes = field(repr=False)
    commitments_json: bytes = field(repr=False)
    blind_jsonl: bytes = field(repr=False)
    snapshot_json: bytes = field(repr=False)
    denominators_json: bytes = field(repr=False)


def leak_reasons(text, gold_name, gold_bytes):
    """DAAE-compatible mechanical refusals; never return matched private text."""
    _need(type(text) is str and type(gold_bytes) is bytes)
    lesson_ref(gold_name)
    try:
        gold = gold_bytes.decode("utf-8")
    except UnicodeError:
        raise NotReady("baseline") from None
    low = text.casefold()
    names = {gold_name.casefold(), gold_name[:-3].casefold()}
    front = re.search(r"(?m)^name:\s*(.+?)\s*$", gold)
    if front:
        names.add(front.group(1).strip().strip("'\"").casefold())
    reasons = []
    if any(name and name in low for name in names):
        reasons.append("gold_name")
    if "[[" in text:
        reasons.append("link")
    if "recurred" in low:
        reasons.append("recurred_word")
    mine, theirs = (re.findall(r"\w+", value.casefold()) for value in (text, gold))
    if {tuple(mine[i:i + 8]) for i in range(len(mine) - 7)} & {tuple(theirs[i:i + 8]) for i in range(len(theirs) - 7)}:
        reasons.append("token_window")
    return tuple(reasons)


def _entry(entry, b, snapshot):
    _need(type(entry) is CaseInput)
    incident = _json(entry.incident_json, entry.incident_sha256)
    evidence = _json(entry.evidence_json, entry.evidence_sha256)
    _keys(incident, ("schema", "study_id", "protocol_sha256", "baseline_sha256", "incident_id",
                     "incident_kind", "reporter", "action_time_utc", "recorded_at_utc", "action_ref",
                     "action_time_resolution_seconds", "action_sha256", "action_receipt_sha256",
                     "lesson_ref", "baseline_blob_sha256"))
    _keys(evidence, ("schema", "session_id", "uuid", "tool_use_id", "record_sha256", "action_utc",
                     "action_latest_utc", "timestamp_resolution_us", "supplied_jsonl_sha256",
                     "preaction_record_sha256", "situation", "situation_sha256"))
    _need(incident["schema"] == "wd.prospective-incident.v1" and incident["study_id"] == b["study_id"]
          and incident["protocol_sha256"] == b["protocol_sha256"]
          and incident["baseline_sha256"] == b["baseline_manifest_sha256"], "binding")
    _need(_id(incident["incident_id"]) and _id(incident["action_ref"]) and _id(incident["lesson_ref"]))
    _need(incident["incident_kind"] in ("recurred", "near_miss"))
    reporter = _identity(incident["reporter"])
    _need(reporter in b["_native_sessions"], "identity")
    _need(evidence["schema"] == "wd.m1-p3-claude-evidence.v1"
          and evidence["session_id"] == b["_native_sessions"][reporter], "identity")
    try:
        _need(str(uuid.UUID(evidence["uuid"])) == evidence["uuid"]
              and str(uuid.UUID(evidence["session_id"])) == evidence["session_id"], "identity")
    except (ValueError, TypeError, AttributeError):
        raise NotReady("identity") from None
    _need(type(evidence["tool_use_id"]) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", evidence["tool_use_id"]), "identity")
    for key in ("action_sha256", "action_receipt_sha256", "baseline_blob_sha256"):
        _need(_hex(incident[key]), "binding")
    for key in ("record_sha256", "supplied_jsonl_sha256", "situation_sha256"):
        _need(_hex(evidence[key]), "binding")
    _need(evidence["record_sha256"] == incident["action_sha256"], "binding")
    _need(type(evidence["preaction_record_sha256"]) is list
          and all(_hex(d) for d in evidence["preaction_record_sha256"]), "binding")
    text = evidence["situation"]
    _need(type(text) is str and len(text) <= 600 and _sha(text.encode("utf-8")) == evidence["situation_sha256"], "binding")
    _need(type(incident["action_time_resolution_seconds"]) is int and incident["action_time_resolution_seconds"] == 1
          and type(evidence["timestamp_resolution_us"]) is int
          and evidence["timestamp_resolution_us"] == b["timestamp_resolution_us"], "clock")
    when = _time(evidence["action_utc"])
    digits = len(evidence["action_utc"].split(".")[1][:-1]) if "." in evidence["action_utc"] else 0
    _need(10 ** (6 - digits) <= b["timestamp_resolution_us"], "clock")
    action_second, recorded = _time(incident["action_time_utc"]), _time(incident["recorded_at_utc"])
    _need(action_second.strftime("%Y-%m-%dT%H:%M:%SZ") == incident["action_time_utc"]
          and recorded.strftime("%Y-%m-%dT%H:%M:%SZ") == incident["recorded_at_utc"], "clock")
    native_delta = timedelta(microseconds=b["timestamp_resolution_us"])
    recorder_delta = timedelta(seconds=incident["action_time_resolution_seconds"])
    native_latest = _time(evidence["action_latest_utc"])
    recorder_latest = action_second + recorder_delta
    _need(native_latest == when + native_delta
          and action_second == when.replace(microsecond=0)
          and action_second <= when < native_latest <= recorder_latest, "clock")
    # Preserve the recorder's full conservative interval, even when the native
    # source provides finer precision. Never replace it with a narrower native
    # interval to admit a baseline/start/close/delay boundary the recorder refuses.
    available = _time(next(iter(snapshot.values()))["mtime"])
    _need(available + timedelta(seconds=1) < action_second
          and _time(b["collection_start_utc"]) + recorder_delta <= action_second
          and recorder_latest <= recorded < _time(b["collection_end_utc"])
          and recorder_latest < _time(b["collection_end_utc"])
          and (recorded - action_second).total_seconds() <= b["max_record_delay_seconds"], "clock")
    by_ref = {lesson_ref(name): name for name in snapshot}
    _need(incident["lesson_ref"] in by_ref, "binding")
    gold = by_ref[incident["lesson_ref"]]
    _need(snapshot[gold]["sha256"] == incident["baseline_blob_sha256"], "binding")
    # A captured uppercase extension cannot be silently relabeled for a grader
    # that requires exact lower-case .md; this is an integration prerequisite.
    _need(gold.endswith(".md"), "baseline")
    return incident, evidence, gold, when


def commitment(case):
    """Exact frozen scorer UTF-8 id|UTCsecond|gold|situationhash|snapshothash|nonce."""
    _need(type(case) is dict and _id(case.get("id")) and type(case.get("gold_basename")) is str)
    lesson_ref(case["gold_basename"])
    _need(case["gold_basename"].endswith(".md"))
    stamp = case.get("time")
    _need(_time(stamp).strftime("%Y-%m-%dT%H:%M:%SZ") == stamp, "clock")
    _need(all(_hex(case.get(key)) for key in ("situation_sha256", "memory_snapshot_sha256", "nonce")), "binding")
    return _sha("|".join(case[key] for key in ("id", "time", "gold_basename", "situation_sha256",
                                             "memory_snapshot_sha256", "nonce")).encode("utf-8"))


def construct_cases(bindings, baseline, entries, *, privacy_applicability,
                    privacy_applicability_sha256, collector_counts, _test_nonce_factory=None):
    """One supplied-byte construction. Missing/unknown guard entries are fatal.

    Expected case exclusions are retained in cases and every reason count. The
    external privacy pass must contain exactly all accepted IDs, each bound to
    incident/evidence/situation/fixed-snapshot hashes and the pinned pass source.
    It must run without gold and without arm outputs. No defaults are permitted.
    Collector reason values are a separate enum from the grader's 21 reasons.
    Nonces are generated only here after all validation/filtering/sampling.
    """
    try:
        return _construct_cases(bindings, baseline, entries, privacy_applicability=privacy_applicability,
                                privacy_applicability_sha256=privacy_applicability_sha256,
                                collector_counts=collector_counts, _test_nonce_factory=_test_nonce_factory)
    except (TypeError, KeyError, AttributeError, ValueError, OverflowError, RecursionError):
        # Never propagate malformed caller values or interpreter messages that
        # could quote private text. This is a fatal stop, never a case drop.
        raise NotReady("schema") from None


def _construct_cases(bindings, baseline, entries, *, privacy_applicability,
                     privacy_applicability_sha256, collector_counts, _test_nonce_factory):
    b = _bound(bindings)
    snapshot_json = fixed_snapshot(baseline, bindings=bindings)
    snapshot, snapsha = _json(snapshot_json), _sha(snapshot_json)
    _need(type(entries) in (list, tuple) and len(entries) <= 10000)
    _need(sum(len(e.incident_json) + len(e.evidence_json) for e in entries if type(e) is CaseInput)
          + len(privacy_applicability) <= BUDGET_BYTES, "budget")
    verified = [_entry(e, b, snapshot) for e in entries]
    ids = [v[0]["incident_id"] for v in verified]
    _need(len(set(ids)) == len(ids), "duplicate")
    _need(len({v[0]["action_ref"] for v in verified}) == len(ids), "duplicate")
    # Exact source identities, not opaque receipt tokens, catch duplicate action.
    actions = [(v[1]["session_id"], v[1]["uuid"], v[1]["tool_use_id"]) for v in verified]
    _need(len(set(actions)) == len(actions), "duplicate")
    _keys(collector_counts, ("reported", "accepted", "refused_cases", "duplicates", "primary_candidates",
                            "near_miss", "refusals_by_reason"))
    for key in ("reported", "accepted", "refused_cases", "duplicates", "primary_candidates", "near_miss"):
        _need(type(collector_counts[key]) is int and collector_counts[key] >= 0, "prerequisite")
    _keys(collector_counts["refusals_by_reason"], COLLECTOR_REASONS)
    _need(all(type(v) is int and v >= 0 for v in collector_counts["refusals_by_reason"].values()), "prerequisite")
    near = sum(v[0]["incident_kind"] == "near_miss" for v in verified)
    _need(collector_counts["accepted"] == len(ids)
          and collector_counts["reported"] == len(ids) + collector_counts["refused_cases"]
          and collector_counts["refused_cases"] == sum(collector_counts["refusals_by_reason"].values())
          and collector_counts["duplicates"] == collector_counts["refusals_by_reason"]["duplicate"]
          and collector_counts["near_miss"] == near
          and collector_counts["primary_candidates"] == len(ids) - near, "prerequisite")
    privacy = _json(privacy_applicability, privacy_applicability_sha256)
    _keys(privacy, ("schema", "study_id", "protocol_sha256", "baseline_manifest_sha256", "source_sha256", "cases"))
    _need(privacy["schema"] == "wd.m1-p3-privacy-applicability.v1"
          and privacy["study_id"] == b["study_id"] and privacy["protocol_sha256"] == b["protocol_sha256"]
          and privacy["baseline_manifest_sha256"] == b["baseline_manifest_sha256"]
          and privacy["source_sha256"] == b["privacy_source_sha256"], "binding")
    _need(type(privacy["cases"]) is dict and set(privacy["cases"]) == set(ids), "privacy_pass")
    rows, texts, ordered = [], {}, []
    for entry, (incident, evidence, gold, when) in zip(entries, verified):
        cid, text = incident["incident_id"], evidence["situation"]
        guard = privacy["cases"][cid]
        _keys(guard, ("incident_sha256", "evidence_sha256", "situation_sha256", "memory_snapshot_sha256", "guarded", "applicable"))
        _need((guard["incident_sha256"], guard["evidence_sha256"], guard["situation_sha256"], guard["memory_snapshot_sha256"])
              == (entry.incident_sha256, entry.evidence_sha256, evidence["situation_sha256"], snapsha), "binding")
        _need(type(guard["guarded"]) is bool and type(guard["applicable"]) is list, "privacy_pass")
        applicable = guard["applicable"]
        _need(all(type(name) is str and name in snapshot for name in applicable)
              and len(set(applicable)) == len(applicable), "privacy_pass")
        reason = None
        if incident["incident_kind"] == "near_miss":
            reason = "near_miss"
        elif not text.strip():
            reason = "situation_empty"
        elif leak_reasons(text, gold, dict(baseline.blobs)[snapshot[gold]["sha256"]]):
            reason = "leak"
        else:
            try:
                capture.project_blind_case({"id": cid, "situation": text, "snapshot_sha256": snapsha})
            except capture.CaptureError:
                reason = "situation_guarded"
        if reason is None:
            if guard["guarded"]:
                reason = "situation_guarded"
            elif not applicable:
                reason = "empty_applicable_corpus"
            elif gold not in applicable:
                reason = "gold_engine_invisible"
        row = {"id": cid, "time": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "gold_basename": gold,
               "situation_sha256": evidence["situation_sha256"], "memory_snapshot_sha256": snapsha,
               "kept": False, "drop_reason": reason, "reporter": incident["reporter"],
               "action_ref": incident["action_ref"], "incident_sha256": entry.incident_sha256,
               "evidence_sha256": entry.evidence_sha256,
               "native_action_utc": evidence["action_utc"], "native_action_latest_utc": evidence["action_latest_utc"],
               "native_action_record_sha256": evidence["record_sha256"]}
        rows.append(row)
        texts[cid] = text
        ordered.append((when, cid, row))
    rows.sort(key=lambda row: row["id"])
    pre = _canon(rows)
    before_dedupe = sum(row["drop_reason"] is None for row in rows)
    seen = set()
    for _, _, row in sorted(ordered):
        if row["drop_reason"] is None:
            if row["gold_basename"] in seen:
                row["drop_reason"] = "duplicate_gold"
            seen.add(row["gold_basename"])
    eligible = sorted(row["id"] for row in rows if row["drop_reason"] is None)
    sampled = set(random.Random(SEED).sample(eligible, CAP_KEPT)) if len(eligible) > CAP_KEPT else set(eligible)
    commits, blind, nonces = {}, [], set()
    for row in rows:
        if row["drop_reason"] is None and row["id"] not in sampled:
            row["drop_reason"] = "not_sampled"
        row["kept"] = row["drop_reason"] is None
        if row["kept"]:
            try:
                nonce = secrets.token_hex(32) if _test_nonce_factory is None else _test_nonce_factory()
            except Exception:
                raise NotReady("nonce") from None
            _need(_hex(nonce) and nonce not in nonces, "nonce")
            nonces.add(nonce)
            row["nonce"] = nonce
            commits[row["id"]] = commitment(row)
            blind.append(capture.project_blind_case({"id": row["id"], "situation": texts[row["id"]], "snapshot_sha256": snapsha}))
    drops = {reason: sum(row["drop_reason"] == reason for row in rows) for reason in sorted(GRADER_REASONS)}
    counts = {"collector": collector_counts, "case_inputs": len(rows), "eligible_before_gold_dedupe": before_dedupe,
              "eligible_distinct_gold": len(eligible), "kept": len(commits), "dropped": sum(drops.values()),
              "drops_by_grader_reason": drops}
    result = CaseArtifacts(pre, _canon(rows), _canon(commits), b"".join(_canon(row) + b"\n" for row in blind),
                           snapshot_json, _canon(counts))
    _need(sum(len(getattr(result, name)) for name in result.__dataclass_fields__) <= BUDGET_BYTES, "budget")
    return result
