"""Default-off synthetic public-v1 producer primitive (Lead GO lead-mind-public-producer-fable-20261031-v1).

Every case runs the REAL Lead modules (snapshot, adapter, producer, builder, checkpoint) over SYNTHETIC log bytes;
no mock stands in for a verifier. These tests prove the wiring of the primitive, never a live deployment: the source,
pins, policies and encoder are all caller-supplied here as they would be in production, and the primitive reports
publisher identity and source authenticity as UNKNOWN because nothing in this chain verifies them.
"""
import hashlib
import json
import os
from dataclasses import asdict

import numpy as np
import pytest

pytest.importorskip("faiss", reason="integration prerequisite: FAISS is required by the public-v1 builder")

from sandbox.mind.step2 import q4 as q4_mod  # noqa: E402
from sandbox.mind.step2 import shared_bridge_snapshot as snapshot  # noqa: E402
from sandbox.mind.step2 import shared_index_builder as build_mod  # noqa: E402
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint  # noqa: E402
from sandbox.mind.step2 import shared_producer as producer  # noqa: E402
from sandbox.mind.step2 import shared_public_producer as pub  # noqa: E402
from waggledance.core.bridge_log_reader import BridgeCursor, read_bridge_log  # noqa: E402

DIM = 16
TASK = "lead-mind-shared-projection-fable-20261031-v1"
APPROVED = frozenset({TASK, "other-task-1"})
SENTINEL = "zebra-message-body-7f3a"
IDENTITY = {"model": "fake-e5-small", "revision": "a" * 40, "weights_sha256": "b" * 64}
ROSTER = {"codex-lead-1": "aaaa1111-1111-4111-8111-111111111aaa", "codex-tools-1": "bbbb2222-2222-4222-8222-222222222bbb",
          "claude-rco-1": "cccc3333-3333-4333-8333-333333333ccc", "claude-rco-2": "dddd4444-4444-4444-8444-444444444ddd",
          "fable-5": "eeee5555-5555-4555-8555-555555555eee"}
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="checkpoint publication is pinned to fixed Windows volumes")


def sha(raw):
    return hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode("utf-8")).hexdigest()


def pinned(document):
    raw = json.dumps(document, sort_keys=True, indent=1).encode("utf-8")
    return raw, sha(raw)


POLICY_DOC = {"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(APPROVED)}
ROSTER_DOC = {"schema": "wd.q4-roster-snapshot.v1",
              "lanes": [{"agent": agent, "agent_uuid": uuid} for agent, uuid in ROSTER.items() if agent != "codex-tools-1"],
              "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}}
PUBLIC_POLICY_DOC = {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK], "public_types": ["message", "done"]}
POLICY_BYTES, POLICY_SHA = pinned(POLICY_DOC)
ROSTER_BYTES, ROSTER_SHA = pinned(ROSTER_DOC)
PUBLIC_POLICY_BYTES, PUBLIC_POLICY_SHA = pinned(PUBLIC_POLICY_DOC)
PUBLIC_MODE = {"status_mode": "public-v1", "public_policy_sha256": PUBLIC_POLICY_SHA}


class FakeEncoder:
    """Deterministic hashed bag of words (synthetic; not a model)."""

    def vector(self, text):
        out = np.zeros(DIM, dtype=np.float32)
        for word in q4_mod.tokens(text):
            out[int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = np.linalg.norm(out)
        return (out / norm if norm else np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)).astype("<f4")

    def __call__(self, texts):
        return np.vstack([self.vector(text) for text in texts])


def event(**changes):
    base = {"ts_utc": "2026-10-31T12:00:30Z", "agent": "codex-lead-1", "type": "message", "status": "milestone",
            "task_id": TASK, "ci": "green", "message": SENTINEL, "payload": {"detail": SENTINEL},
            "paths": ["C:/" + SENTINEL], "run_id": SENTINEL, "to": "fable-5"}
    base.update(changes)
    base.setdefault("agent_uuid", ROSTER.get(base["agent"]))
    return base


def public_event(**changes):
    fields = {"payload": {"public_status": True}, "to": "", "message": "", "paths": [], "run_id": "run-public-synthetic"}
    fields.update(changes)
    base = event(**fields)
    if "ci" not in changes:
        base.pop("ci")
    return base


PUBLIC_EVENTS = [public_event(),
                 public_event(type="done", status="done", agent="codex-tools-1", ts_utc="2026-10-31T12:05:00Z"),
                 public_event(status="info", agent="fable-5", ts_utc="2026-10-31T12:10:00Z")]
PRIVATE_EVENTS = [event(ts_utc="2026-10-31T12:12:00Z"),
                  event(type="done", status="blocked", agent="codex-tools-1", ts_utc="2026-10-31T12:13:00Z")]
MIXED_EVENTS = PUBLIC_EVENTS + PRIVATE_EVENTS + [event(agent="claude-rco-1", ts_utc="2026-10-31T12:14:00Z"),
                                                 event(task_id="feature/pr-branch-task", ts_utc="2026-10-31T12:15:00Z")]
EMPTY_EVENTS = PRIVATE_EVENTS + [event(agent="claude-rco-1", ts_utc="2026-10-31T12:25:00Z")]
LATER_PUBLIC_EVENTS = [public_event(status="done", ts_utc="2026-10-31T12:30:00Z"),
                       public_event(type="done", status="info", agent="fable-5", ts_utc="2026-10-31T12:35:00Z"),
                       event(ts_utc="2026-10-31T12:36:00Z")]


def _log(folder, events, *, start=None):
    """Synthetic bridge-log bytes: create (genesis) or append (continuation); returns ``(log, generation_path, start)``."""
    folder.mkdir(exist_ok=True)
    log, generation_path = folder / "events.jsonl", folder / "events.generation.json"
    payload = b"".join(json.dumps(row, sort_keys=True).encode("utf-8") + b"\n" for row in events)
    if start is None:
        log.write_bytes(payload)
        generation_path.write_text(json.dumps({"generation": "gen-synthetic"}), encoding="utf-8")
        probe = read_bridge_log(log, generation_path=generation_path, max_bytes=1 << 20, max_rows=100)
        assert probe.candidate_cursor is not None, probe.reason
        start = BridgeCursor(offset=0, file_identity=probe.candidate_cursor.file_identity, generation="gen-synthetic")
    else:
        with open(log, "ab") as handle:
            handle.write(payload)
    return log, generation_path, start


def _anchor(log, generation_path, start, *, status_mode="public-v1", public_policy_sha256=PUBLIC_POLICY_SHA):
    document = {"schema": "wd.q4-source-anchor.v1", "source_path": str(log), "generation_path": str(generation_path),
                "cursor_start": asdict(start), "policy_sha256": POLICY_SHA, "roster_sha256": ROSTER_SHA,
                "status_mode": status_mode, "public_policy_sha256": public_policy_sha256}
    raw = json.dumps(document, sort_keys=True).encode("utf-8")
    return raw, sha(raw)


def _config(log, generation_path, start, *, anchor=None, public_policy=(PUBLIC_POLICY_BYTES, PUBLIC_POLICY_SHA), **source_changes):
    anchor_bytes, anchor_sha256 = anchor or _anchor(log, generation_path, start)
    fields = {"source": log, "generation_path": generation_path, "cursor": start, "anchor_bytes": anchor_bytes,
              "anchor_sha256": anchor_sha256, "policy_bytes": POLICY_BYTES, "policy_sha256": POLICY_SHA,
              "roster_bytes": ROSTER_BYTES, "roster_sha256": ROSTER_SHA, "max_bytes": 1 << 20, "max_rows": 100}
    fields.update(source_changes)
    source = producer.SourceConfig(**fields)
    return producer.PublicSourceConfig(source=source, public_policy_bytes=public_policy[0], public_policy_sha256=public_policy[1])


def _pins(config, **changes):
    fields = {"policy_sha256": config.source.policy_sha256, "roster_sha256": config.source.roster_sha256,
              "public_policy_sha256": config.public_policy_sha256, "anchor_sha256": config.source.anchor_sha256}
    fields.update(changes)
    return pub.CallerPins(**fields)


def _request(config, root, generation, *, pins="derive", predecessor=None):
    return pub.PublicPublishRequest(config=config, pins=_pins(config) if pins == "derive" else pins, root=root,
                                    generation=generation, encode=FakeEncoder(), identity=IDENTITY, dim=DIM,
                                    predecessor=predecessor)


def _publish(request):
    return pub.publish_public_generation(enabled=True, request=request)


def _refused(request, token):
    with pytest.raises(pub.PublicProducerRefused) as info:
        _publish(request)
    assert str(info.value) == token and token in pub.REFUSALS


def _public_read(root, pointer_sha256):
    return checkpoint.read_verified_pointer(root, APPROVED, expected_pointer_sha256=pointer_sha256, policy_bytes=POLICY_BYTES,
                                            policy_sha256=POLICY_SHA, roster_bytes=ROSTER_BYTES, roster_sha256=ROSTER_SHA,
                                            expected_mode="public-v1", expected_public_policy_sha256=PUBLIC_POLICY_SHA)


def _legacy_read(root, pointer_sha256):
    return checkpoint.read_verified_pointer(root, APPROVED, expected_pointer_sha256=pointer_sha256, policy_bytes=POLICY_BYTES,
                                            policy_sha256=POLICY_SHA, roster_bytes=ROSTER_BYTES, roster_sha256=ROSTER_SHA)


def _files(root):
    return [path for path in root.rglob("*") if path.is_file()]


def _text(paths):
    return b"".join(path.read_bytes() for path in paths)


def test_primitive_is_off_by_default_and_its_surface_is_closed():
    assert pub.publish_public_generation() == {"status": "OFF"}
    assert pub.publish_public_generation(enabled=False, request="anything") == {"status": "OFF"}
    for enabled in (1, "true", None, [True]):
        with pytest.raises(pub.PublicProducerRefused, match="request_invalid"):
            pub.publish_public_generation(enabled=enabled)
    with pytest.raises(pub.PublicProducerRefused, match="request_invalid"):
        pub.publish_public_generation(enabled=True)
    with pytest.raises(pub.PublicProducerRefused, match="request_invalid"):
        pub.publish_public_generation(enabled=True, request={"config": None})
    assert issubclass(pub.PublicProducerRefused, ValueError)
    assert pub.STATUSES == {"OFF", "PUBLISHED_GENESIS", "PUBLISHED_HISTORY", "EMPTY_ADVANCED"}
    assert pub.REFUSALS == {"request_invalid", "pins_absent", "pins_mismatch", "prepare_refused", "empty_genesis",
                            "build_refused", "checkpoint_refused"}
    assert pub.REPORT_KEYS == {"status", "generation", "pointer_sha256", "artifacts", "records", "source_mode",
                               "publisher_identity", "source_authenticity", "authenticated", "pins_bound"}
    assert pub.STATUS_MODE == "public-v1" and pub.UNKNOWN == "UNKNOWN"
    assert not hasattr(pub, "publish_prepared")  # the Lead stub is not re-exported; ONE primitive only


def test_absent_or_mismatched_caller_pins_refuse_before_anything_is_written(tmp_path):
    log, generation_path, start = _log(tmp_path / "log", MIXED_EVENTS)
    config = _config(log, generation_path, start)
    root = tmp_path / "mem"
    root.mkdir()
    _refused(_request(config, root, "gen-a", pins=None), "pins_absent")
    _refused(_request(config, root, "gen-a", pins={"policy_sha256": POLICY_SHA}), "pins_mismatch")  # not the pin record
    for key in ("policy_sha256", "roster_sha256", "public_policy_sha256", "anchor_sha256"):
        _refused(_request(config, root, "gen-a", pins=_pins(config, **{key: "0" * 64})), "pins_mismatch")  # a pin disagrees
        _refused(_request(config, root, "gen-a", pins=_pins(config, **{key: POLICY_SHA.upper()})), "pins_mismatch")  # not a digest
    # the configuration names the digest the caller pinned, but its bytes are not those bytes: refused, never re-derived
    tampered = _config(log, generation_path, start, policy_bytes=POLICY_BYTES + b" ")
    _refused(_request(tampered, root, "gen-a", pins=_pins(config)), "pins_mismatch")
    swapped = _config(log, generation_path, start, public_policy=(PUBLIC_POLICY_BYTES + b"\n", PUBLIC_POLICY_SHA))
    _refused(_request(swapped, root, "gen-a", pins=_pins(config)), "pins_mismatch")
    assert _files(root) == []  # no generation, no pointer, no lock


def test_invalid_mode_or_request_inputs_refuse_and_leave_the_root_untouched(tmp_path):
    log, generation_path, start = _log(tmp_path / "log", MIXED_EVENTS)
    root = tmp_path / "mem"
    root.mkdir()
    legacy_anchor = _anchor(log, generation_path, start, status_mode="legacy", public_policy_sha256=None)
    _refused(_request(_config(log, generation_path, start, anchor=legacy_anchor), root, "gen-a"), "prepare_refused")
    other_policy = pinned({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(APPROVED)})
    _refused(_request(_config(log, generation_path, start, public_policy=other_policy), root, "gen-a"), "prepare_refused")
    foreign_anchor = _anchor(log, generation_path, start, public_policy_sha256="0" * 64)
    _refused(_request(_config(log, generation_path, start, anchor=foreign_anchor), root, "gen-a"), "prepare_refused")
    config = _config(log, generation_path, start)
    for generation in ("", "Gen-A", "gen a", "../gen-a", "g" * 65):
        _refused(_request(config, root, generation), "request_invalid")
    bad_predecessor = pub.Predecessor(pointer_sha256="not-a-digest", memory_dir=root / "gen-a", memory_sha256="0" * 64,
                                      projection_sha256="0" * 64, build_receipt_sha256="0" * 64)
    _refused(_request(config, root, "gen-b", predecessor=bad_predecessor), "request_invalid")
    _refused(_request(config, root, "gen-b", predecessor={"pointer_sha256": "0" * 64}), "request_invalid")
    _refused(pub.PublicPublishRequest(config=config.source, pins=_pins(config), root=root, generation="gen-a",
                                      encode=FakeEncoder(), identity=IDENTITY, dim=DIM), "request_invalid")  # legacy config
    _refused(pub.PublicPublishRequest(config=config, pins=_pins(config), root=root, generation="gen-a",
                                      encode=FakeEncoder(), identity=IDENTITY, dim=DIM, timeout=0), "request_invalid")
    assert _files(root) == []


@WINDOWS_ONLY
def test_synthetic_public_a_then_empty_advance_then_b_through_the_primitive_with_stale_pointer_refused(tmp_path):
    root = tmp_path / "mem"
    root.mkdir()
    log, generation_path, start = _log(tmp_path / "log", MIXED_EVENTS)
    config_a = _config(log, generation_path, start)
    report_a = _publish(_request(config_a, root, "gen-a"))
    assert set(report_a) == pub.REPORT_KEYS and report_a["status"] == "PUBLISHED_GENESIS"
    assert report_a["generation"] == "gen-a" and report_a["records"] == 3 and report_a["source_mode"] == PUBLIC_MODE
    assert report_a["publisher_identity"] == report_a["source_authenticity"] == "UNKNOWN"
    assert report_a["authenticated"] is False and report_a["pins_bound"] is True
    p0 = report_a["pointer_sha256"]
    assert p0 == sha((root / checkpoint.POINTER).read_bytes())
    verified_a = _public_read(root, p0)
    assert verified_a["generation"] == "gen-a" and verified_a["artifacts"] == report_a["artifacts"]
    assert verified_a["anchor_sha256"] == config_a.source.anchor_sha256
    assert set(report_a["artifacts"]) == {"descriptor_sha256", "memory_sha256", "projection_sha256", "receipt_sha256"}
    with pytest.raises(checkpoint.CheckpointRefused):  # the public series never resolves as legacy
        _legacy_read(root, p0)
    memory_a = json.loads((root / "gen-a" / "memory.json").read_bytes())
    assert memory_a["source_mode"] == PUBLIC_MODE
    predecessor_a = pub.Predecessor(pointer_sha256=p0, memory_dir=root / "gen-a",
                                    memory_sha256=report_a["artifacts"]["memory_sha256"],
                                    projection_sha256=report_a["artifacts"]["projection_sha256"],
                                    build_receipt_sha256=report_a["artifacts"]["receipt_sha256"])
    # a second genesis on the same root is refused by the checkpoint (a pointer already exists); nothing is deleted
    _refused(_request(config_a, root, "gen-a2"), "checkpoint_refused")
    assert (root / "gen-a2").is_dir() and sha((root / checkpoint.POINTER).read_bytes()) == p0  # retained unpublished
    # strict EMPTY public delta (targeted-private sentinel rows + a review-lane row): a pointer advance, never a generation
    log, generation_path, resume_empty = _log(tmp_path / "log", EMPTY_EVENTS, start=verified_cursor(config_a, root, p0))
    config_empty = _config(log, generation_path, resume_empty)
    fresh_root = tmp_path / "fresh"
    fresh_root.mkdir()
    _refused(_request(config_empty, fresh_root, "gen-x"), "empty_genesis")
    assert _files(fresh_root) == []
    report_empty = _publish(_request(config_empty, root, "gen-x", predecessor=predecessor_a))
    assert report_empty["status"] == "EMPTY_ADVANCED" and report_empty["generation"] is None
    assert report_empty["artifacts"] is None and report_empty["records"] == 0
    assert report_empty["publisher_identity"] == report_empty["source_authenticity"] == "UNKNOWN"
    p1 = report_empty["pointer_sha256"]
    assert p1 != p0 and p1 == sha((root / checkpoint.POINTER).read_bytes())
    assert not (root / "gen-x").exists()
    # a stale predecessor pointer (p0 after the advance) refuses: the builder watermark proof fails, the pointer stays
    log, generation_path, resume_b = _log(tmp_path / "log", LATER_PUBLIC_EVENTS, start=verified_cursor(config_empty, root, p1))
    config_b = _config(log, generation_path, resume_b)
    _refused(_request(config_b, root, "gen-b", predecessor=predecessor_a), "build_refused")
    assert not (root / "gen-b").exists() and sha((root / checkpoint.POINTER).read_bytes()) == p1
    predecessor_p1 = pub.Predecessor(pointer_sha256=p1, memory_dir=root / "gen-a",
                                     memory_sha256=report_a["artifacts"]["memory_sha256"],
                                     projection_sha256=report_a["artifacts"]["projection_sha256"],
                                     build_receipt_sha256=report_a["artifacts"]["receipt_sha256"])
    wrong_artifacts = pub.Predecessor(pointer_sha256=p1, memory_dir=root / "gen-a", memory_sha256="0" * 64,
                                      projection_sha256=report_a["artifacts"]["projection_sha256"],
                                      build_receipt_sha256=report_a["artifacts"]["receipt_sha256"])
    _refused(_request(config_b, root, "gen-b", predecessor=wrong_artifacts), "build_refused")
    report_b = _publish(_request(config_b, root, "gen-b", predecessor=predecessor_p1))
    assert report_b["status"] == "PUBLISHED_HISTORY" and report_b["generation"] == "gen-b" and report_b["records"] == 5
    p2 = report_b["pointer_sha256"]
    assert p2 != p1 and p2 == sha((root / checkpoint.POINTER).read_bytes())
    verified_b = _public_read(root, p2)
    assert verified_b["generation"] == "gen-b" and verified_b["artifacts"] == report_b["artifacts"]
    assert verified_b["anchor_sha256"] == config_b.source.anchor_sha256
    receipt_b = json.loads((root / "gen-b" / "build_receipt.json").read_bytes())
    assert receipt_b["source_mode"] == PUBLIC_MODE and receipt_b["history"]["continuity_route"] == "verified_watermark"
    assert receipt_b["history"]["watermark"]["pointer_sha256"] == p1
    with pytest.raises(checkpoint.CheckpointRefused):
        _legacy_read(root, p2)
    with pytest.raises(checkpoint.CheckpointRefused):  # superseded digests no longer resolve
        _public_read(root, p1)
    # a replay of B against the current pointer refuses (p1 is no longer the pointer); nothing is deleted
    _refused(_request(config_b, root, "gen-c", predecessor=predecessor_p1), "build_refused")
    assert sha((root / checkpoint.POINTER).read_bytes()) == p2
    # non-disclosure: the public generation output never carries the sentinel, cursor tokens, the file identity,
    # the anchor bytes or the policy bytes; the private pointer files carry the identity by design and stay outside
    public_text = _text(_files(root / "gen-a") + _files(root / "gen-b") + _files(root / "gen-a2"))
    assert SENTINEL.encode() not in public_text and b"bc1." not in public_text
    assert start.file_identity.encode() not in public_text
    for raw in (config_a.source.anchor_bytes, config_empty.source.anchor_bytes, config_b.source.anchor_bytes,
                PUBLIC_POLICY_BYTES, POLICY_BYTES, ROSTER_BYTES):
        assert raw not in public_text
    private_files = [path for path in _files(root) if path.relative_to(root).parts[0] not in ("gen-a", "gen-a2", "gen-b")]
    private_names = {path.relative_to(root).parts[0] for path in private_files}
    assert {checkpoint.POINTER, "q4-pointer-" + p0 + ".json", "q4-pointer-" + p1 + ".json"} <= private_names
    assert all(name in (checkpoint.POINTER, checkpoint.LOCK) or name.startswith(("q4-pointer-", "q4-delta-"))
               for name in private_names)
    assert (root / checkpoint.LOCK).read_bytes() == b""
    private_text = _text(private_files)
    assert SENTINEL.encode() not in private_text and PUBLIC_POLICY_BYTES not in private_text
    assert not any((root / name / checkpoint.POINTER).exists() for name in ("gen-a", "gen-b"))
    assert sorted(path.name for path in (root / "gen-a").iterdir() if path.is_file()) == \
        sorted(path.name for path in (root / "gen-b").iterdir() if path.is_file())


def verified_cursor(config, root, pointer_sha256):
    """The continuation cursor is the caller's: here it is re-derived from the configuration's own candidate position
    and checked against the verified pointer, which carries it as ``cursor_end_sha256`` after a generation commit and
    as ``watermark_sha256`` after an empty advance (the generation cursor end stays at the last published build)."""
    prepared = producer.prepare_public_delta(enabled=True, config=config)
    end = prepared.result["candidate_cursor"]
    verified = _public_read(root, pointer_sha256)
    assert sha(snapshot._token(end)) in (verified["cursor_end_sha256"], verified["watermark_sha256"])
    return end
