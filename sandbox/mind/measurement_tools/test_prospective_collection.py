"""Synthetic-only controller tests; no live study inputs or model imports."""
import importlib.util
import copy
from datetime import datetime, timezone, timedelta
import io
import json
import os
import subprocess
from pathlib import Path
import sys
import unittest
from unittest import mock
import uuid
from contextlib import redirect_stdout, redirect_stderr

spec = importlib.util.spec_from_file_location("prospective_collection", Path(__file__).with_name("prospective_collection.py"))
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

AUDIT = Path(__file__).resolve().parents[3] / ".codex-audit" / "m1-p3-collection-controller-20261020"
REC, CAP = m.load_dependencies()  # Exact supplied code only, never study inputs.
REPORTER = {"agent": "synthetic-agent", "agent_uuid": "11111111-1111-4111-8111-111111111111",
            "session_id": "wd-reboot-synthetic-20350405T060000Z"}
NATIVE_SESSION = "22222222-2222-4222-8222-222222222222"


class FakeHost(REC.Host):
    def __init__(self, root):
        self.root = root
        self.free = m.FREE_RESERVE + m.BUDGET
        self.partial = False
        self.range_partial = False
        self.busy = False
        self.writes = []

    def free_bytes(self, path):
        return self.free

    def validate_destination(self, audit, root):
        if audit != self.root or root.parent != audit:
            raise REC.Refusal("destination")
        self.validate_tree(audit, recurse=False)

    def write_new(self, path, data):
        self.writes.append(path.name)
        if (self.partial and path.name.startswith("event-")) or (self.range_partial and path.name.startswith("source-range-")):
            with path.open("xb") as stream:
                stream.write(data[:19]); stream.flush(); os.fsync(stream.fileno())
            raise OSError("synthetic-private-error-not-for-stdout")
        super().write_new(path, data)

    def lock(self, path):
        if self.busy: raise OSError("synthetic-lock-contention")
        return super().lock(path)


class FakeRuntime:
    def __init__(self, root):
        self.root = root
        self.host = FakeHost(root)
        self.utc = datetime(2035, 4, 5, 6, 1, tzinfo=timezone.utc)
        self.mono = 0.0
        self.reads = []
        self.baseline_reads = 0
        self.on_sleep = None
        self.closed = False

    def now(self): return self.utc
    def monotonic(self): return self.mono

    def sleep(self, seconds):
        if self.on_sleep:
            action = self.on_sleep; self.on_sleep = None; action()
        self.utc += timedelta(seconds=seconds)
        self.mono += seconds

    def validate(self, cfg):
        if Path(cfg["audit_root"]) != self.root: raise m.Stop("paths")
        trees = [Path(cfg[key]) for key in ("control_root", "ledger_root", "inbox", "baseline_root", "blind_root", "score_root")]
        if any(path.parent != self.root for path in trees): raise m.Stop("paths")
        try: CAP._disjoint(trees)
        except CAP.CaptureError: raise m.Stop("paths") from None
        if Path(cfg["stop_signal"]) != Path(cfg["inbox"]) / "STOP.json": raise m.Stop("paths")

    def pin_new(self, path):
        self.host.validate_tree(path, recurse=False)

    def read(self, path, limit):
        self.reads.append(path)
        self.host.validate_tree(path.parent)
        before = path.stat()
        data = self.host.read(path, limit)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise m.Stop("input_changed")
        return data

    def baseline(self, root, expected):
        self.baseline_reads += 1
        data = self.read(root / "manifest.json", m.MAX_INPUT)
        if m.sha(data) != expected: raise m.Stop("baseline")
        return m.parse(data), m.parse(self.read(root / "SEALED.json", m.MAX_INPUT))

    def read_source_range(self, source, start, end):
        path = Path(source["evidence_path"])
        self.reads.append(path)
        self.host.validate_tree(path.parent)
        with path.open("rb",buffering=0) as stream:
            return m.bounded_range(stream,start=start,end=end,expected_identity=source["file_identity"],limit=source["max_range_bytes"])

    def close(self): self.closed = True


class Fixture:
    def __init__(self):
        AUDIT.mkdir(parents=True, exist_ok=True)
        self.root = AUDIT / uuid.uuid4().hex
        self.root.mkdir()
        self.runtime = FakeRuntime(self.root)
        for name in ("inbox", "baseline", "evidence"): (self.root / name).mkdir()
        self.source_path = self.root / "evidence" / ("9" * 32 + ".jsonl")
        self.source_path.write_bytes(b"")
        info = self.source_path.stat()
        self.cfg = {"audit_root": str(self.root),
                    **{key: str(self.root / leaf) for key, leaf in
                       (("control_root", "control"), ("ledger_root", "ledger"), ("inbox", "inbox"),
                        ("baseline_root", "baseline"), ("blind_root", "blind"), ("score_root", "score"))},
                    "stop_signal": str(self.root / "inbox" / "STOP.json"),
                    "collection_start_utc": "2035-04-05T06:00:00Z",
                    "collection_end_utc": "2035-04-05T06:01:02Z",
                    "baseline_resolution_us": 1000000,
                    "max_record_delay_seconds": 120, "max_records": 10000,
                    "allowed_reporters": [copy.deepcopy(REPORTER)], "poll_ms": 500, "max_wall_seconds": 10,
                    "sources": [{"id": "4" * 32, "schema": "wd.m1-p3-claude-jsonl.v1",
                                 "reporter": copy.deepcopy(REPORTER), "native_session_id": NATIVE_SESSION,
                                 "evidence_path": str(self.source_path),
                                 "file_identity": {"device":info.st_dev,"inode":info.st_ino},
                                 "initial_cursor":0,"max_range_bytes":m.MAX_INPUT,"timestamp_resolution_us": 1000}]}
        self.rows = [{"name": "synthetic-lesson.md", "sha256": "5" * 64, "bytes": 9}]
        self.reporter_sources = [{"reporter": copy.deepcopy(REPORTER), "native_session_id": NATIVE_SESSION}]
        self.bind()

    def bind(self, status="LOCKED", go_status="LOCKED_COLLECTION_GO"):
        self.p = {"schema": "wd.m1-p3-protocol-lock.v1", "status": status, "study_id": "3" * 32,
                  "reporter_sources": copy.deepcopy(self.reporter_sources),
                  "collection_controller": self.cfg, "scientific_fields_untouched": {"minimum_n": 40, "cap_n": 60, "seed": 20261019}}
        self.pb = m.canon(self.p); self.ph = m.sha(self.pb)
        self.manifest = {"schema": "wd.m1-p3-baseline.v1", "identity": {"study_id": "3" * 32, "protocol_sha256": self.ph},
                         "started_utc": "2035-04-05T05:59:57.000000Z", "sealed_utc": "2035-04-05T05:59:58.000000Z",
                         "files": self.rows}
        self.mb = m.canon(self.manifest); self.mh = m.sha(self.mb)
        self.seal = {"schema": "wd.m1-p3-baseline-seal.v1", "status": "SEALED", "manifest_sha256": self.mh,
                     "sealed_utc": "2035-04-05T05:59:58.000000Z"}
        (self.root / "baseline" / "manifest.json").write_bytes(self.mb)
        (self.root / "baseline" / "SEALED.json").write_bytes(m.canon(self.seal))
        self.g = {"schema": "wd.m1-p3-collection-go.v1", "status": go_status, "study_id": "3" * 32,
                  "protocol_sha256": self.ph, "baseline_manifest_sha256": self.mh}
        self.gb = m.canon(self.g); self.gh = m.sha(self.gb)

    def nomination(self, *, identity=None, kind="recurred", user="synthetic earlier work", action_utc="2035-04-05T06:00:30.000Z", append=False,
                   record_uuid="77777777-7777-4777-8777-777777777777", include_user=True):
        incident = identity or uuid.uuid4().hex
        user_row = {"type": "user", "uuid": "66666666-6666-4666-8666-666666666666", "sessionId": NATIVE_SESSION,
                    "timestamp": "2035-04-05T06:00:20.000Z", "message": {"role": "user", "content": user}}
        action = {"type": "assistant", "uuid": record_uuid, "sessionId": NATIVE_SESSION,
                  "timestamp": action_utc, "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "synthetic_tool_01",
                  "name": "synthetic", "input": {"description": "synthetic action body excluded from situation"}}]}}
        if append: user_row["uuid"] = str(uuid.uuid5(uuid.UUID(record_uuid),"synthetic-user"))
        prefix = (m.canon(user_row) + b"\n" if include_user else b"") + m.canon(action) + b"\n"
        start = self.source_path.stat().st_size if append else 0
        with self.source_path.open("ab" if append else "wb") as stream: stream.write(prefix)
        n = {"schema": "wd.m1-p3-nomination.v1", "study_id": "3" * 32, "protocol_sha256": self.ph,
             "baseline_manifest_sha256": self.mh, "incident_id": incident, "incident_kind": kind,
             "reporter": copy.deepcopy(REPORTER), "gold_basename": "synthetic-lesson.md", "source_id": "4" * 32,
             "evidence_file": self.source_path.name, "source_range":{"start":start,"end":start+len(prefix)},
             "evidence_prefix_sha256": m.sha(prefix),
             "reference": {"session_id": NATIVE_SESSION, "uuid": action["uuid"], "tool_use_id": "synthetic_tool_01",
                           "record_sha256": m.sha(m.canon(action)), "action_utc": action_utc}}
        self.save(n)
        return n

    def save(self, n):
        path = self.root / "inbox" / (n["incident_id"] + ".json")
        path.write_bytes(m.canon(n)); return path

    def controller(self, **kwargs):
        return m.Controller(self.pb, self.gb, protocol_sha256=self.ph, go_sha256=self.gh,
                            _test_runtime=self.runtime, **kwargs)

    def events(self):
        return [m.parse(path.read_bytes()) for path in sorted((self.root / "control").glob("event-*.json"))]


class ControllerTests(unittest.TestCase):
    def test_verified_lookback_keeps_cursor_monotonic_and_context_available(self):
        # MUTANT_CONTEXT_LOST_AT_CURSOR / MUTANT_UNRECORDED_CURSOR_REWIND.
        f=Fixture(); first=f.nomination(identity="1"*32)
        second=f.nomination(identity="2"*32,append=True,include_user=False,
                            record_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        second["source_range"]["start"]=0
        second["evidence_prefix_sha256"]=m.sha(f.source_path.read_bytes()); f.save(second)
        result=f.controller().run(execute=True)
        self.assertEqual((result["status"],result["counts"]["accepted"]),("CLOSED",2),result)
        proof=f.events()[1]["proof"]["source_range"]
        self.assertEqual(proof["cursor_before"],first["source_range"]["end"])
        self.assertEqual(proof["cursor_after"],second["source_range"]["end"])
        self.assertEqual(proof["overlap_bytes"],first["source_range"]["end"])
        self.assertIsNotNone(proof["lookback_validation"])

    def test_changed_prior_lookback_cannot_be_rebound_by_new_nomination_hash(self):
        # MUTANT_LOOKBACK_CALLER_HASH_TRUSTED.
        f=Fixture(); f.nomination(identity="1"*32)
        def append_and_mutate():
            second=f.nomination(identity="2"*32,append=True,include_user=False,
                                record_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
            data=f.source_path.read_bytes().replace(b"earlier work",b"altered work")
            f.source_path.write_bytes(data)
            second["source_range"]={"start":0,"end":len(data)}
            second["evidence_prefix_sha256"]=m.sha(data); f.save(second)
        f.runtime.on_sleep=append_and_mutate
        result=f.controller().run(execute=True)
        self.assertEqual((result["status"],result["reason"],result["counts"]["accepted"]),
                         ("STOP_NOT_READY","binding",1),result)

    def test_known_opaque_and_excluded_native_blocks_counted_without_payload(self):
        # MUTANT_NATIVE_METADATA_REJECTED / MUTANT_OPAQUE_SKIPPED_SILENTLY.
        f = Fixture(); n = f.nomination()
        user,action = [m.parse(row) for row in f.source_path.read_bytes().splitlines()]
        user.update({"session_id":"synthetic-alias-not-native","origin":"synthetic-hidden-origin","promptId":"synthetic"})
        action.update({"apiBlockIndex":0,"effort":"medium","wireToolInputs":{"synthetic":"hidden-input"}})
        action["message"]["content"][0]["caller"] = {"type":"synthetic"}
        excluded = {"type":"assistant","uuid":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb","sessionId":NATIVE_SESSION,
                    "timestamp":"2035-04-05T06:00:25.000Z","message":{"role":"assistant","content":[
                        {"type":"thinking","thinking":"synthetic-hidden-thinking","signature":"synthetic-hidden-signature"},
                        {"type":"tool_addition","tool":{"synthetic":"hidden-definition"}}]}}
        rows = [{"type":"system","synthetic":"hidden-system"},excluded,user,action]
        prefix = b"".join(m.canon(row)+b"\n" for row in rows)
        f.source_path.write_bytes(prefix)
        n["evidence_prefix_sha256"] = m.sha(prefix); n["source_range"]["end"] = len(prefix)
        n["reference"]["record_sha256"] = m.sha(m.canon(action)); f.save(n)
        result = f.controller().run(execute=True)
        self.assertEqual((result["status"],result["counts"]["accepted"]),("CLOSED",1),result)
        proof = f.events()[0]["proof"]; counts = proof["native_record_dispositions"]
        self.assertEqual((counts["records_total"],counts["opaque_records"],counts["native_records"]),(4,1,3))
        self.assertEqual(counts["excluded_blocks"]["thinking"],1)
        self.assertEqual(counts["excluded_blocks"]["tool_addition"],1)
        self.assertEqual(proof["action_utc"], "2035-04-05T06:00:30.000Z")
        self.assertNotIn("hidden-",json.dumps(proof)); self.assertNotIn("hidden-",json.dumps(result))

    def test_unknown_native_type_is_durable_refusal_not_silently_filtered(self):
        f = Fixture(); n = f.nomination()
        prefix = m.canon({"type":"synthetic-UNKNOWN","synthetic":"never-export"})+b"\n"+f.source_path.read_bytes()
        f.source_path.write_bytes(prefix); n["evidence_prefix_sha256"] = m.sha(prefix)
        n["source_range"]["end"] = len(prefix); f.save(n)
        result = f.controller().run(execute=True)
        self.assertEqual((result["status"],result["counts"]["accepted"],result["counts"]["refused"]),("CLOSED",0,1),result)
        self.assertEqual(f.events()[0]["status"]["reason"],"unsupported_native_record")
        self.assertEqual(f.events()[0]["proof"]["source_range"]["range_sha256"],m.sha(prefix))

    def test_partial_cursor_receipt_stops_before_recorder_and_retains_bytes(self):
        f = Fixture(); f.nomination(); f.runtime.host.range_partial = True; c = f.controller()
        result = c.run(execute=True)
        self.assertEqual((result["status"],result["counts"]["accepted"]),("STOP_NOT_READY",0),result)
        self.assertTrue(list((f.root/"control").glob("source-range-*.json")))
        self.assertEqual(c.source_cursors,{})
        self.assertFalse(list((f.root/"ledger").glob("incident-*.json")))

    def test_fractional_baseline_seal_keeps_full_one_second_upper_bound(self):
        # MUTANT_BASELINE_ROUNDED_DOWN / MUTANT_BASELINE_RESOLUTION_NATIVE.
        f = Fixture(); f.seal["sealed_utc"] = "2035-04-05T05:59:58.500000Z"
        # The original close window cannot fit the full conservative capsule.
        with self.assertRaises(REC.Refusal): m.capsules(f.p,f.g,f.cfg,f.manifest,f.seal,REC,CAP)
        # A separate valid synthetic window still must retain the full bound.
        f.cfg["collection_start_utc"] = "2035-04-05T06:00:02Z"; f.bind()
        f.seal["sealed_utc"] = "2035-04-05T05:59:58.500000Z"
        _,bc,_,_ = m.capsules(f.p,f.g,f.cfg,f.manifest,f.seal,REC,CAP)
        baseline = m.parse(bc)
        self.assertEqual(baseline["sealed_at_utc"], "2035-04-05T05:59:59Z")
        self.assertEqual(baseline["sealed_time_resolution_seconds"],1)

    def test_collection_boundary_literal_precision_is_passed_unchanged(self):
        # MUTANT_WHOLE_SECOND_START_REWRITTEN_AS_MICROSECOND_START.
        f = Fixture(); f.nomination()
        with mock.patch.object(m,"load_dependencies",return_value=(REC,CAP)), mock.patch.object(
                CAP,"extract_action_evidence",wraps=CAP.extract_action_evidence) as extract:
            result = f.controller().run(execute=True)
        self.assertEqual(result["status"],"CLOSED",result)
        self.assertEqual(extract.call_args.kwargs["collection_start_utc"], f.cfg["collection_start_utc"])
        self.assertEqual(extract.call_args.kwargs["collection_end_utc"], f.cfg["collection_end_utc"])

    def test_source_cursor_receipts_distinguish_first_read_and_exact_replay(self):
        # MUTANT_RANGE_REPLAY_FLAG_ALWAYS_TRUE / MUTANT_CURSOR_RESET_WITHOUT_RECEIPT.
        f = Fixture(); n = f.nomination(identity="1"*32); f.nomination(identity="2"*32)
        result = f.controller().run(execute=True)
        self.assertEqual(result["status"], "CLOSED", result)
        proofs = [event["proof"]["source_range"] for event in f.events()]
        self.assertEqual([proof["replay"] for proof in proofs], [False,True])
        self.assertEqual(proofs[0]["receipt_sha256"], proofs[1]["receipt_sha256"])
        self.assertEqual(len(list((f.root/"control").glob("source-range-*.json"))), 1)
        self.assertEqual(proofs[0]["cursor_after"], n["source_range"]["end"])

    def test_source_cursor_advances_contiguously_with_receipt_hash_chain(self):
        f = Fixture(); first = f.nomination(identity="1"*32)
        second = f.nomination(identity="2"*32,append=True,record_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        result = f.controller().run(execute=True)
        self.assertEqual((result["status"],result["counts"]["accepted"]), ("CLOSED",2), result)
        proofs = [event["proof"]["source_range"] for event in f.events()]
        self.assertEqual(proofs[1]["start"],first["source_range"]["end"])
        self.assertEqual(proofs[1]["end"],second["source_range"]["end"])
        self.assertEqual(proofs[1]["previous_range_receipt_sha256"],proofs[0]["receipt_sha256"])

    def test_source_cursor_gap_rotation_truncation_and_partial_line_stop(self):
        for kind in ("gap","rotation","truncated","partial_line"):
            with self.subTest(kind=kind):
                f = Fixture(); n = f.nomination()
                if kind == "gap": n["source_range"]["start"] = 1
                elif kind == "rotation": f.cfg["sources"][0]["file_identity"]["inode"] += 1; f.bind(); n=f.nomination()
                elif kind == "truncated": f.source_path.write_bytes(b"")
                else: n["source_range"]["end"] -= 1
                f.save(n); result = f.controller().run(execute=True)
                self.assertEqual(result["status"],"STOP_NOT_READY",result)
                self.assertEqual(result["counts"]["accepted"],0)

    def test_locked_reporter_sources_map_cannot_be_missing_or_ambiguous(self):
        # MUTANT_REPORTER_MAP_OPTIONAL / DUPLICATE / UNLISTED / EXTRA_FIELD.
        unknown = {**REPORTER, "agent": "unlisted-agent"}
        cases = (None, [], [copy.deepcopy({"reporter": REPORTER, "native_session_id": NATIVE_SESSION})] * 2,
                 [{"reporter": unknown, "native_session_id": NATIVE_SESSION}],
                 [{"reporter": REPORTER, "native_session_id": NATIVE_SESSION, "guess": True}])
        for entries in cases:
            with self.subTest(entries=entries):
                f = Fixture(); f.reporter_sources = entries; f.bind()
                result = f.controller().run(execute=True)
                self.assertEqual(result["status"], "STOP_NOT_READY", result)
                self.assertEqual(f.runtime.baseline_reads, 0)
                self.assertFalse((f.root / "control").exists())

    def test_source_native_session_must_match_original_reporter_map(self):
        # MUTANT_SOURCE_SESSION_TRUSTED / CROSS_REPORTER_SOURCE_FALLBACK.
        for mutate_reporter in (False, True):
            with self.subTest(mutate_reporter=mutate_reporter):
                f = Fixture()
                if mutate_reporter:
                    second = {**REPORTER, "agent": "second-agent"}
                    f.cfg["allowed_reporters"].append(second)
                    f.reporter_sources.append({"reporter": second, "native_session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"})
                    f.cfg["sources"][0]["reporter"] = second
                else:
                    f.reporter_sources[0]["native_session_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
                f.bind(); result = f.controller().run(execute=True)
                self.assertEqual((result["status"], result["reason"]), ("STOP_NOT_READY", "identity"), result)
                self.assertEqual(f.runtime.reads, [])

    def test_native_session_map_requires_canonical_unique_uuid_not_bridge_label(self):
        # MUTANT_NATIVE_UUID_NORMALIZED / NATIVE_UUID_REUSED_ACROSS_REPORTERS.
        for native in (NATIVE_SESSION.replace("-", ""), "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", REPORTER["session_id"]):
            with self.subTest(native=native):
                f = Fixture(); f.reporter_sources[0]["native_session_id"] = native
                f.cfg["sources"][0]["native_session_id"] = native; f.bind()
                result = f.controller().run(execute=True)
                self.assertEqual((result["status"], result["reason"]), ("STOP_NOT_READY", "identity"), result)
                self.assertEqual(f.runtime.reads, [])
        f = Fixture(); second = {**REPORTER, "agent": "second-agent"}
        f.cfg["allowed_reporters"].append(second)
        f.reporter_sources.append({"reporter": second, "native_session_id": NATIVE_SESSION})
        f.bind(); result = f.controller().run(execute=True)
        self.assertEqual((result["status"], result["reason"]), ("STOP_NOT_READY", "identity"), result)

    def test_finite_close_valid_record_and_three_distinct_digests(self):
        f = Fixture(); n = f.nomination(); c = f.controller()
        result = c.run(execute=True)
        self.assertEqual((result["status"], result["reason"]), ("CLOSED", "collection_closed"), result)
        self.assertEqual(result["counts"]["accepted"], 1)
        self.assertEqual(result["counts"]["primary_candidates"], 1)
        event = f.events()[0]; proof = event["proof"]
        self.assertEqual(proof["record_sha256"], n["reference"]["record_sha256"])
        self.assertEqual(proof["supplied_jsonl_sha256"], n["evidence_prefix_sha256"])
        self.assertEqual(len({proof["record_sha256"], proof["supplied_jsonl_sha256"], proof["action_receipt_sha256"]}), 3)
        self.assertNotIn("situation", json.dumps(event))
        self.assertNotIn("synthetic-lesson", json.dumps(result))
        total = sum(p.stat().st_size for root in ("control", "ledger") for p in (f.root / root).iterdir())
        self.assertEqual(result["controlled_output_bytes"], total)
        self.assertTrue(f.runtime.closed)

    def test_near_miss_never_enters_primary_count(self):
        f = Fixture(); f.nomination(kind="near_miss"); result = f.controller().run(execute=True)
        self.assertEqual((result["counts"]["primary_candidates"], result["counts"]["near_miss"]), (0, 1))

    def test_refusal_and_duplicate_are_durable_journal_entries(self):
        f = Fixture()
        first = f.nomination(identity="1" * 32)
        f.nomination(identity="2" * 32)
        invalid = f.nomination(identity="3" * 32); invalid["story"] = "synthetic secret"; f.save(invalid)
        result = f.controller().run(execute=True)
        self.assertEqual(result["status"], "CLOSED", result)
        self.assertEqual(result["counts"], {"reported": 3,"accepted": 1,"primary_candidates": 1,"near_miss": 0,"refused": 2,"duplicates": 1})
        events = f.events()
        self.assertEqual([x["status"]["disposition"] for x in events], ["RECORDED", "REFUSE_CASE", "REFUSE_CASE"])
        self.assertEqual([x["status"]["reason"] for x in events][1:], ["duplicate", "schema"])
        for event in events: self.assertRegex(event["nomination_sha256"], r"^[0-9a-f]{64}$")

    def test_capsules_are_deterministic_bound_to_originals(self):
        f = Fixture()
        first = m.capsules(f.p, f.g, f.cfg, f.manifest, f.seal, REC, CAP)
        second = m.capsules(f.p, f.g, f.cfg, f.manifest, f.seal, REC, CAP)
        self.assertEqual(first[:3], second[:3])
        self.assertEqual((first[3].protocol_sha256, first[3].baseline_sha256), (f.ph, f.mh))
        self.assertNotEqual(first[3].protocol_capsule_sha256, f.ph)

    def test_opaque_lesson_collisions_stop_before_enrollment(self):
        f = Fixture(); f.rows.append({"name":"synthetic-second.md","sha256":"8"*64,"bytes":9}); f.bind()
        with mock.patch.object(m, "lesson_id", return_value="0" * 32):
            result = f.controller().run(execute=True)
        self.assertEqual(result["reason"], "lesson_collision")
        self.assertTrue((f.root / "control" / "intent.json").exists())
        self.assertFalse((f.root / "ledger").exists())

    def test_proposed_or_wrong_hash_go_never_creates_outputs(self):
        for target in ("protocol_proposed", "go_proposed", "hash"):
            f = Fixture()
            if target == "protocol_proposed": f.bind(status="PROPOSED")
            if target == "go_proposed": f.bind(go_status="PROPOSED")
            if target == "hash": f.ph = "0" * 64
            result = f.controller().run(execute=True)
            self.assertEqual(result["status"], "STOP_NOT_READY")
            self.assertFalse((f.root / "control").exists())
            self.assertEqual(f.runtime.baseline_reads, 0)

    def test_fable_and_rco1_nominations_not_authorized(self):
        for actor in ("fable-5", "claude-rco-1"):
            f = Fixture(); f.cfg["allowed_reporters"][0]["agent"] = actor; f.bind()
            self.assertEqual(f.controller().run(execute=True)["reason"], "identity")
            self.assertFalse((f.root / "control").exists())

    def test_invalid_or_unlisted_reporter_stops_and_accepts_no_later_record(self):
        for actor in (dict(REPORTER,session_id=""), dict(REPORTER,agent="other-synthetic")):
            f = Fixture(); n = f.nomination(identity="1"*32); n["reporter"] = actor; f.save(n)
            f.nomination(identity="2"*32)
            result = f.controller().run(execute=True)
            self.assertEqual(result["reason"], "identity")
            self.assertEqual(result["counts"]["accepted"], 0)

    def test_prefix_or_exact_source_record_hash_mismatch_stops(self):
        for field in ("prefix", "record"):
            f = Fixture(); n = f.nomination(identity="1"*32)
            if field == "prefix": n["evidence_prefix_sha256"] = "0"*64
            else: n["reference"]["record_sha256"] = "0"*64
            f.save(n); f.nomination(identity="2"*32)
            result = f.controller().run(execute=True)
            self.assertEqual(result["status"], "STOP_NOT_READY")
            self.assertIn(result["reason"], ("binding", "identity"))
            self.assertEqual(result["counts"]["accepted"], 0)

    def test_unsupported_native_schema_is_explicit_refusal_not_compatibility_filter(self):
        f = Fixture(); f.cfg["sources"][0]["schema"] = "synthetic-unsupported-native"; f.bind(); f.nomination()
        result = f.controller().run(execute=True)
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["counts"]["refused"], 1)
        self.assertEqual(f.events()[0]["status"]["reason"], "unsupported_native_schema")
        self.assertFalse(any(p.suffix == ".jsonl" for p in f.runtime.reads))

    def test_unsupported_resolution_is_explicit_without_guessed_action_time(self):
        f = Fixture(); f.cfg["sources"][0]["timestamp_resolution_us"] = 1000000; f.bind()
        f.nomination(action_utc="2035-04-05T06:00:30.500000Z")
        result = f.controller().run(execute=True)
        self.assertEqual(result["status"], "CLOSED", result)
        self.assertEqual(f.events()[0]["status"]["reason"], "unsupported_resolution")
        self.assertFalse(list((f.root / "ledger").glob("incident-*.json")))

    def test_unknown_native_session_never_invented(self):
        f = Fixture(); n = f.nomination(); n["reference"]["session_id"] = "unknown"; f.save(n)
        self.assertEqual(f.controller().run(execute=True)["reason"], "identity")

    def test_known_outside_window_refusal_is_journaled(self):
        f = Fixture(); f.nomination(action_utc="2035-04-05T06:00:00.000000Z")
        result = f.controller().run(execute=True)
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(f.events()[0]["status"]["reason"], "outside_window")

    def test_path_traversal_and_overlapping_output_trees_stop(self):
        f = Fixture(); n = f.nomination(); n["evidence_file"] = "../escape.jsonl"; f.save(n)
        self.assertEqual(f.controller().run(execute=True)["reason"], "paths")
        f = Fixture(); f.cfg["ledger_root"] = f.cfg["blind_root"]; f.bind()
        self.assertEqual(f.controller().run(execute=True)["status"], "STOP_NOT_READY")
        self.assertFalse((f.root / "control").exists())

    def test_no_old_root_adoption_and_no_same_instance_retry(self):
        f = Fixture(); (f.root / "control").mkdir(); (f.root / "control" / "retained").write_bytes(b"synthetic")
        c = f.controller()
        self.assertEqual(c.run(execute=True)["reason"], "destination_exists")
        self.assertEqual(c.run(execute=True)["reason"], "already_attempted")
        self.assertEqual((f.root / "control" / "retained").read_bytes(), b"synthetic")

    def test_budget_is_shared_monotonic_including_refusals(self):
        f = Fixture()
        for i in range(20):
            n = f.nomination(identity="%032x" % (i+1)); n["story"] = "synthetic"; f.save(n)
        with mock.patch.object(m, "BUDGET", 8192):
            c = f.controller(); result = c.run(execute=True)
        self.assertEqual(result["status"], "STOP_NOT_READY", result)
        self.assertEqual(result["reason"], "budget")
        self.assertLessEqual(c.host.total, 8192)
        self.assertGreater(len(c.host.files), 5)

    def test_disk_floor_is_admission_stop_without_baseline_read(self):
        f = Fixture(); f.runtime.host.free -= 1; c = f.controller()
        self.assertEqual(c.run(execute=True)["reason"], "disk")
        self.assertEqual(f.runtime.baseline_reads, 0)
        self.assertFalse((f.root / "control").exists())
        self.assertEqual(c.run(execute=True)["reason"], "already_attempted")

    def test_partial_journal_write_stops_and_retains_all_bytes(self):
        f = Fixture(); f.nomination(); f.runtime.host.partial = True; c = f.controller()
        result = c.run(execute=True)
        self.assertEqual(result["status"], "STOP_NOT_READY")
        self.assertTrue(list((f.root / "control").glob("event-*.json")))
        self.assertTrue(list((f.root / "ledger").glob("incident-*.json")))
        self.assertEqual(c.run(execute=True)["reason"], "already_attempted")

    def test_corruption_and_unknown_clock_are_sticky_stops(self):
        for kind in ("corruption", "clock"):
            f = Fixture(); f.nomination(); c = f.controller()
            def corrupt():
                if kind == "corruption": (f.root / "control" / "intent.json").write_bytes(b"{}")
                else: f.runtime.utc -= timedelta(seconds=10)
            f.runtime.on_sleep = corrupt
            result = c.run(execute=True)
            self.assertEqual(result["reason"], kind)
            self.assertEqual(c.run(execute=True)["reason"], "already_attempted")

    def test_forward_clock_jump_is_not_a_clean_collection_close(self):
        f = Fixture(); f.nomination()
        f.runtime.on_sleep = lambda: setattr(f.runtime, "utc", f.runtime.utc + timedelta(minutes=5))
        result = f.controller().run(execute=True)
        self.assertEqual((result["status"], result["reason"]), ("STOP_NOT_READY", "clock"))

    def test_control_os_lock_is_held_for_writer_lifetime(self):
        f = Fixture(); observations = []
        code = """import os,sys
f=open(sys.argv[1],'r+b',buffering=0)
try:
 if os.name=='nt':
  import msvcrt; msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
 else:
  import fcntl; fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
except OSError: sys.exit(23)
sys.exit(0)
"""
        def probe():
            child = subprocess.run([sys.executable,"-I","-B","-S","-c",code,str(f.root/"control"/"LOCK")],
                                   capture_output=True,timeout=15,check=False)
            observations.append(child.returncode)
        f.runtime.on_sleep = probe
        self.assertEqual(f.controller().run(execute=True)["status"], "CLOSED")
        self.assertEqual(observations, [23])

    def test_inbox_mutation_after_processing_stops_not_relabels(self):
        f = Fixture(); n = f.nomination(); path = f.root / "inbox" / (n["incident_id"]+".json")
        f.runtime.on_sleep = lambda: path.write_bytes(b"{}")
        self.assertEqual(f.controller().run(execute=True)["reason"], "input_changed")

    def test_writer_lock_contention_consumes_without_resume(self):
        f = Fixture(); f.runtime.host.busy = True; c = f.controller()
        self.assertEqual(c.run(execute=True)["reason"], "lock")
        self.assertTrue((f.root / "control").exists())
        self.assertEqual(f.runtime.host.writes[0], "intent.json")
        self.assertEqual(c.run(execute=True)["reason"], "already_attempted")

    def test_manual_stop_and_fixed_walltime(self):
        f = Fixture(); (f.root / "inbox" / "STOP.json").write_bytes(m.canon({"schema":"wd.m1-p3-manual-stop.v1","study_id":"3"*32,"protocol_sha256":f.ph}))
        result = f.controller().run(execute=True)
        self.assertEqual((result["status"], result["reason"]), ("CLOSED", "manual_stop"))
        self.assertEqual(f.runtime.baseline_reads, 0)
        f = Fixture(); f.cfg["max_wall_seconds"] = 1; f.bind()
        self.assertEqual(f.controller().run(execute=True)["reason"], "walltime")

    def test_baseline_or_seal_corruption_stops_after_consumed_intent(self):
        for file in ("manifest.json", "SEALED.json"):
            f = Fixture(); (f.root / "baseline" / file).write_bytes(b"{}")
            result = f.controller().run(execute=True)
            self.assertEqual(result["status"], "STOP_NOT_READY")
            self.assertTrue((f.root / "control" / "intent.json").exists())
            self.assertFalse((f.root / "ledger").exists())

    def test_cli_is_inert_and_sanitized_without_explicit_complete_go(self):
        for args in ([], ["--help"], ["--protocol","synthetic-private-path"]):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err): result = m.main(args)
            self.assertEqual(result, 2)
            self.assertEqual(err.getvalue(), "")
            self.assertNotIn("synthetic-private", out.getvalue())
            self.assertEqual(json.loads(out.getvalue())["status"], "STOP_NOT_READY")


class AdmissionTests(unittest.TestCase):
    def test_no_execute_is_inert_even_with_missing_bindings(self):
        controller = m.Controller(b"{}", b"{}", protocol_sha256="0" * 64, go_sha256="0" * 64)
        self.assertEqual(controller.run()["reason"], "execute_required")


class RangeIOTests(unittest.TestCase):
    def test_large_source_reads_only_exact_bounded_range(self):
        # MUTANT_WHOLE_TRANSCRIPT_READ / MUTANT_IMPLICIT_CURSOR_RESET.
        path = AUDIT / ("synthetic-large-" + uuid.uuid4().hex + ".jsonl")
        start = 70 * 1024 * 1024; content = b'{"synthetic":"range only"}\n'
        with path.open("xb") as stream:
            stream.seek(start-1); stream.write(b"\n"+content)
        info = path.stat()
        with path.open("rb",buffering=0) as stream:
            data,proof = m.bounded_range(stream,start=start,end=start+len(content),
                expected_identity={"device":info.st_dev,"inode":info.st_ino},limit=1024)
        self.assertEqual(data,content)
        self.assertEqual(proof["read_bytes"],len(content)+1)
        self.assertGreater(proof["source_size"],m.BUDGET)

    def test_partial_boundaries_rotation_overrun_and_oversized_range_stop(self):
        # MUTANT_BOUNDARY_GUESSED / MUTANT_FILE_ROTATION_ACCEPTED.
        path = AUDIT / ("synthetic-small-"+uuid.uuid4().hex+".jsonl")
        path.write_bytes(b'{"synthetic":1}\n'); info=path.stat()
        identity={"device":info.st_dev,"inode":info.st_ino}
        for start,end,ident,limit in ((1,info.st_size,identity,1024),(0,info.st_size-1,identity,1024),
             (0,info.st_size+1,identity,1024),(0,info.st_size,{"device":info.st_dev,"inode":info.st_ino+1},1024),
             (0,info.st_size,identity,1)):
            with self.subTest(start=start,end=end,identity=ident,limit=limit),path.open("rb",buffering=0) as stream:
                with self.assertRaises(m.Stop):
                    m.bounded_range(stream,start=start,end=end,expected_identity=ident,limit=limit)


if __name__ == "__main__":
    unittest.main()
