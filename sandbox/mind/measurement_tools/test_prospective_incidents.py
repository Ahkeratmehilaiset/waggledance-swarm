"""Synthetic recorder tests. No study inputs, models, or real ledger are used."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone, timedelta


SOURCE = Path(__file__).with_name("prospective_incidents.py")
spec = importlib.util.spec_from_file_location("prospective_incidents", SOURCE)
rec = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rec
spec.loader.exec_module(rec)

AUDIT = Path(__file__).resolve().parents[3] / ".codex-audit" / "m1-p3-prospective-recorder-20261020"
START = "2035-04-05T06:00:00Z"
END = "2035-04-06T06:00:00Z"
NOW = "2035-04-05T06:01:00Z"
IDENTITY = {"agent": "synthetic-agent", "agent_uuid": "1" * 32, "session_id": "2" * 32}
ORIGINAL_PROTOCOL = "a" * 64
ORIGINAL_BASELINE = "b" * 64


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def bindings(*args, **kwargs):
    return rec.Bindings.from_bytes(*args, protocol_sha256=kwargs.get("protocol_sha256", ORIGINAL_PROTOCOL),
                                  baseline_sha256=kwargs.get("baseline_sha256", ORIGINAL_BASELINE))


def capsules():
    protocol = {"schema": "wd.prospective-protocol.v1", "study_id": "3" * 32,
                "protocol_sha256": ORIGINAL_PROTOCOL,
                "collection_start_utc": START, "collection_end_utc": END,
                "max_record_delay_seconds": 120, "max_records": 60,
                "allowed_reporters": [copy.deepcopy(IDENTITY)]}
    p = encoded(protocol)
    baseline = {"schema": "wd.prospective-baseline.v1", "study_id": "3" * 32,
                "protocol_sha256": ORIGINAL_PROTOCOL, "baseline_sha256": ORIGINAL_BASELINE,
                "sealed_at_utc": "2035-04-05T05:59:58Z",
                "sealed_time_resolution_seconds": 1,
                "lesson_blobs": {"4" * 32: "5" * 64}}
    return p, encoded(baseline)


class FakeHost(rec.Host):
    """Explicit injection only; real disk IO and OS locks still exercised."""
    def __init__(self):
        self.now = datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        self.free = rec.MIN_FREE_BYTES + rec.BUDGET_BYTES
        self.failure = None

    def utc_now(self):
        return self.now

    def free_bytes(self, root):
        return self.free

    def validate_destination(self, audit, root):
        if audit != AUDIT.resolve() or root.parent != audit or root.name.startswith("."):
            raise rec.Refusal("destination")
        self.validate_tree(audit, recurse=False)

    def write_new(self, path, data):
        if self.failure == "partial" and path.name.startswith("incident-"):
            with path.open("xb") as stream:
                stream.write(data[:17])
                stream.flush()
                os.fsync(stream.fileno())
            raise OSError("synthetic sensitive text must never escape")
        super().write_new(path, data)
        if self.failure == "readback" and path.name.startswith("incident-"):
            path.write_bytes(b"partial")
        if self.failure == "fsync" and path.name.startswith("incident-"):
            raise OSError("synthetic fsync uncertainty")


class RecorderTests(unittest.TestCase):
    def setUp(self):
        AUDIT.mkdir(parents=True, exist_ok=True)
        self.root = AUDIT / uuid.uuid4().hex
        self.host = FakeHost()
        self.p, self.b = capsules()
        self.bindings = bindings(self.p, self.b, digest(self.p), digest(self.b))
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.close()
        # Retain all fixtures, including partial/corrupt bytes, for audit.

    def recorder(self, mode="record", root=None, host=None):
        r = rec.Recorder(AUDIT, root or self.root, self.bindings, mode=mode,
                         _test_host=host or self.host)
        self.recorders.append(r)
        return r

    def record(self, suffix="6", kind="recurred"):
        action = {"schema": "wd.prospective-action.v1", "action_ref": suffix * 32,
                  "reporter": copy.deepcopy(IDENTITY), "action_time_utc": "2035-04-05T06:00:30Z",
                  "action_time_resolution_seconds": 1, "source_record_sha256": "9" * 64}
        action_bytes = encoded(action)
        value = {"schema": "wd.prospective-incident.v1", "study_id": "3" * 32,
                 "protocol_sha256": ORIGINAL_PROTOCOL, "baseline_sha256": ORIGINAL_BASELINE,
                 "incident_id": uuid.uuid4().hex, "incident_kind": kind,
                 "reporter": copy.deepcopy(IDENTITY), "action_time_utc": action["action_time_utc"],
                 "recorded_at_utc": NOW, "action_ref": action["action_ref"],
                 "action_time_resolution_seconds": 1, "action_sha256": "9" * 64,
                 "action_receipt_sha256": digest(action_bytes), "lesson_ref": "4" * 32,
                 "baseline_blob_sha256": "5" * 64}
        return value, action_bytes

    def test_roundtrip_and_no_private_fields_in_receipt(self):
        r = self.recorder()
        self.assertEqual(r.initialize()["status"], "initialized")
        value, action = self.record()
        result = r.enroll(value, action)
        self.assertEqual(result["status"], "recorded")
        self.assertEqual(result["primary_candidates"], 1)
        self.assertEqual(result["near_miss"], 0)
        self.assertRegex(result["record_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(json.loads((self.root / ("incident-" + value["incident_id"] + ".json")).read_bytes()), value)
        for private in ("action_ref", "reporter", "lesson_ref", "baseline_blob_sha256", "path"):
            self.assertNotIn(private, result)

    def test_near_miss_cannot_increase_primary_n(self):
        r = self.recorder(); r.initialize()
        result = r.enroll(*self.record(kind="near_miss"))
        self.assertEqual((result["primary_candidates"], result["near_miss"]), (0, 1))

    def test_duplicate_action_ref_with_changed_id_is_refused(self):
        r = self.recorder(); r.initialize()
        first, action = self.record()
        self.assertEqual(r.enroll(first, action)["status"], "recorded")
        second = dict(first, incident_id=uuid.uuid4().hex, incident_kind="near_miss")
        self.assertEqual(r.enroll(second, action)["reason"], "duplicate")
        self.assertEqual(len(list(self.root.glob("incident-*.json"))), 1)

    def test_duplicate_id_with_different_action_is_refused(self):
        r = self.recorder(); r.initialize()
        first, a = self.record(); r.enroll(first, a)
        second, b = self.record("7"); second["incident_id"] = first["incident_id"]
        self.assertEqual(r.enroll(second, b)["reason"], "duplicate")

    def test_inert_without_explicit_mode(self):
        r = self.recorder(mode=None)
        self.assertEqual(r.initialize()["reason"], "mode")
        self.assertEqual(r.enroll(*self.record())["reason"], "mode")
        self.assertFalse(self.root.exists())

    def test_enroll_requires_successful_initialization_and_close_is_final(self):
        r = self.recorder()
        self.assertEqual(r.enroll(*self.record())["reason"], "not_initialized")
        r.initialize(); r.close()
        self.assertEqual(r.enroll(*self.record())["reason"], "closed")

    def test_no_adoption_or_overwrite(self):
        self.root.mkdir(); (self.root / "existing").write_bytes(b"retained")
        self.assertEqual(self.recorder().initialize()["reason"], "destination_exists")
        self.assertEqual((self.root / "existing").read_bytes(), b"retained")

    def test_binding_hashes_and_json_are_strict(self):
        cases = [(self.p, self.b, "0" * 64, digest(self.b)),
                 (self.p, self.b, digest(self.p), "0" * 64),
                 (self.p, self.b, digest(self.p).upper(), digest(self.b)),
                 (b'{}', self.b, digest(b'{}'), digest(self.b)),
                 (b'{"x":1,"x":2}', self.b, digest(b'{"x":1,"x":2}'), digest(self.b)),
                 (b'{"x":NaN}', self.b, digest(b'{"x":NaN}'), digest(self.b))]
        for args in cases:
            with self.subTest(args_hash=digest(args[0])):
                with self.assertRaises(rec.Refusal):
                    bindings(*args)

    def test_protocol_and_baseline_schema_types_times_and_bounds(self):
        for field, bad in [("schema", "wrong"), ("study_id", "story with spaces"),
                           ("collection_start_utc", "2035-02-30T00:00:00Z"),
                           ("collection_end_utc", START), ("max_records", True),
                           ("max_records", 10001), ("max_record_delay_seconds", 0),
                           ("max_record_delay_seconds", "120"), ("extra", "secret"),
                           ("allowed_reporters", []),
                           ("allowed_reporters", [dict(IDENTITY, agent="fable-5")]),
                           ("allowed_reporters", [dict(IDENTITY, agent="claude-rco-1")]),
                           ("allowed_reporters", [IDENTITY, IDENTITY])]:
            p = json.loads(self.p); p[field] = bad; pb = encoded(p)
            b = json.loads(self.b); bb = encoded(b)
            with self.subTest(field=field):
                with self.assertRaises(rec.Refusal):
                    bindings(pb, bb, digest(pb), digest(bb))
        for field, bad in [("study_id", "7" * 32), ("protocol_sha256", "7" * 64),
                           ("sealed_at_utc", START), ("lesson_blobs", {}),
                           ("sealed_time_resolution_seconds", True),
                           ("sealed_time_resolution_seconds", 0),
                           ("lesson_blobs", {"../secret": "5" * 64}),
                           ("lesson_blobs", {"4" * 32: "Z" * 64}), ("extra", "secret")]:
            b = json.loads(self.b); b[field] = bad; bb = encoded(b)
            with self.subTest(baseline_field=field):
                with self.assertRaises(rec.Refusal):
                    bindings(self.p, bb, digest(self.p), digest(bb))

    def test_record_schema_types_and_exact_bindings(self):
        for field, bad in [("schema", "wrong"), ("study_id", "7" * 32),
                           ("protocol_sha256", "0" * 64), ("baseline_sha256", "0" * 64),
                           ("incident_id", "../escape"), ("incident_id", 1),
                           ("incident_kind", "near_repeat"), ("action_ref", "../escape"),
                           ("action_sha256", "0" * 64), ("action_receipt_sha256", "0" * 64),
                           ("action_time_resolution_seconds", True), ("lesson_ref", "7" * 32),
                           ("baseline_blob_sha256", "0" * 64), ("story", "private")]:
            r = self.recorder(root=AUDIT / uuid.uuid4().hex); r.initialize()
            value, action = self.record(); value[field] = bad
            with self.subTest(field=field):
                self.assertEqual(r.enroll(value, action)["status"], "refused")
        r = self.recorder(root=AUDIT / uuid.uuid4().hex); r.initialize()
        value, action = self.record(); del value["action_time_utc"]
        self.assertEqual(r.enroll(value, action)["status"], "refused")
        self.assertEqual(len(list(self.root.glob("incident-*.json"))), 0)

    def test_reporter_has_exact_opaque_identity_no_free_text(self):
        for bad in [None, {}, dict(IDENTITY, extra="text"), dict(IDENTITY, agent="../secret"),
                    dict(IDENTITY, agent_uuid=True), dict(IDENTITY, session_id="real path")]:
            r = self.recorder(root=AUDIT / uuid.uuid4().hex); r.initialize()
            value, action = self.record(); value["reporter"] = bad
            with self.subTest(bad=bad):
                result = r.enroll(value, action)
                self.assertEqual((result["disposition"], result["reason"]), ("STOP_NOT_READY", "identity"))

    def test_strict_utc_calendar_and_no_implicit_clock(self):
        r = self.recorder(); r.initialize()
        for bad in [None, 1, "", "2035-04-05", "2035-04-05T06:00:30+00:00",
                    "2035-04-05T06:00:30.0Z", "2035-02-30T06:00:30Z", "2035-04-05T06:00:60Z"]:
            value, action = self.record(); value["action_time_utc"] = bad
            with self.subTest(bad=bad):
                self.assertEqual(r.enroll(value, action)["status"], "refused")

    def test_action_window_future_backfill_and_recorded_clock(self):
        r = self.recorder(); r.initialize()
        for action_time, recorded in [(START, NOW), (END, NOW),
                                      ("2035-04-05T06:01:01Z", NOW),
                                      ("2035-04-05T06:00:30Z", "2035-04-05T06:00:29Z"),
                                      ("2035-04-05T06:00:30Z", "2035-04-05T06:01:01Z")]:
            value, a = self.record(); receipt = json.loads(a)
            value["action_time_utc"] = receipt["action_time_utc"] = action_time
            a = encoded(receipt); value["action_receipt_sha256"] = digest(a); value["recorded_at_utc"] = recorded
            self.assertEqual(r.enroll(value, a)["status"], "refused")
        self.host.now += timedelta(minutes=5)
        value, a = self.record(); value["recorded_at_utc"] = "2035-04-05T06:06:00Z"
        self.assertEqual(r.enroll(value, a)["reason"], "time")

    def test_action_receipt_hash_and_tuple_must_match(self):
        for field, bad in [("schema", "wrong"), ("action_ref", "7" * 32),
                           ("reporter", dict(IDENTITY, agent="other-agent")),
                           ("action_time_utc", "2035-04-05T06:00:31Z"),
                           ("source_record_sha256", "0" * 64),
                           ("action_time_resolution_seconds", 2), ("story", "private")]:
            r = self.recorder(root=AUDIT / uuid.uuid4().hex); r.initialize()
            value, a = self.record(); receipt = json.loads(a); receipt[field] = bad
            a = encoded(receipt); value["action_receipt_sha256"] = digest(a)
            with self.subTest(field=field):
                self.assertEqual(r.enroll(value, a)["status"], "refused")

    def test_low_disk_and_size_and_record_bounds(self):
        self.host.free -= 1
        self.assertEqual(self.recorder().initialize()["reason"], "disk")
        self.assertFalse(self.root.exists())
        self.host.free += 1
        r = self.recorder(root=AUDIT / uuid.uuid4().hex); r.initialize()
        self.host.free = 0
        self.assertEqual(r.enroll(*self.record())["reason"], "disk")
        self.host.free = rec.MIN_FREE_BYTES + rec.BUDGET_BYTES
        r = self.recorder(root=AUDIT / uuid.uuid4().hex); r.initialize()
        value, action = self.record(); value["unknown"] = "x" * 65536
        self.assertEqual(r.enroll(value, action)["status"], "refused")
        p = json.loads(self.p); p["max_records"] = 1; self.p = encoded(p)
        self.bindings = bindings(self.p, self.b, digest(self.p), digest(self.b))
        r = self.recorder(root=AUDIT / uuid.uuid4().hex); r.initialize()
        r.enroll(*self.record()); self.assertEqual(r.enroll(*self.record("7"))["reason"], "limit")

    def test_partial_io_readback_and_fsync_uncertainty_halt_and_retain(self):
        for fault in ["partial", "readback", "fsync"]:
            with self.subTest(fault=fault):
                host = FakeHost(); host.failure = fault
                r = self.recorder(root=AUDIT / uuid.uuid4().hex, host=host); r.initialize()
                self.assertEqual(r.enroll(*self.record())["reason"], "uncertain_write")
                self.assertEqual(len(list(r.root.glob("incident-*.json"))), 1)
                host.failure = None
                self.assertEqual(r.enroll(*self.record("7"))["reason"], "halted")
                self.assertTrue((r.root / "HALTED").exists())

    def test_corruption_extra_missing_and_reparse_halt(self):
        for fault in ["mutate", "extra", "missing", "symlink"]:
            with self.subTest(fault=fault):
                r = self.recorder(root=AUDIT / uuid.uuid4().hex); r.initialize()
                value, a = self.record(); r.enroll(value, a)
                path = r.root / ("incident-" + value["incident_id"] + ".json")
                if fault == "mutate": path.write_bytes(b"{}")
                elif fault == "extra": (r.root / "unknown").write_bytes(b"x")
                elif fault == "missing": path.unlink()
                else:
                    # A synthetic lstat rejection exercises the same fail-closed branch.
                    original = r.host.validate_tree
                    r.host.validate_tree = lambda *args, **kwargs: (_ for _ in ()).throw(rec.Refusal("reparse"))
                self.assertEqual(r.enroll(*self.record("7"))["reason"], "corruption")
                self.assertEqual(r.enroll(*self.record("8"))["reason"], "halted")
                if fault == "symlink": r.host.validate_tree = original

    def test_budget_full_is_refused_before_write(self):
        r = self.recorder(); r.initialize()
        r._total_bytes = rec.BUDGET_BYTES  # Controlled aggregate-bound seam, no 64 MiB fixture.
        self.assertEqual(r.enroll(*self.record())["reason"], "budget")
        self.assertFalse(list(self.root.glob("incident-*.json")))

    def test_real_destination_guard_rejects_other_drive_relative_and_traversal(self):
        host = rec.Host()
        for audit, root in [("F:/synthetic/.codex-audit", "F:/synthetic/.codex-audit/run"),
                            ("relative/.codex-audit", "relative/.codex-audit/run"),
                            ("C:/synthetic/.codex-audit", "C:/synthetic/.codex-audit/../escape"),
                            ("C:/synthetic/not-audit", "C:/synthetic/not-audit/run"),
                            ("C:/synthetic/.codex-audit", "C:/synthetic/.codex-audit/ads:stream")]:
            with self.subTest(root=root):
                with self.assertRaises(rec.Refusal): host.validate_destination(Path(audit), Path(root))

    def test_ntfs_guard_is_required_not_fallback(self):
        host = rec.Host()
        host.volume_type = lambda path: "FAT32"
        # Test the filesystem guard independently of the real system's mount layout.
        with self.assertRaises(rec.Refusal): host.require_ntfs(Path("C:/"))

    def test_os_lock_is_exclusive_across_processes(self):
        r = self.recorder(); r.initialize()
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
        child = subprocess.run([sys.executable, "-I", "-B", "-S", "-c", code, str(self.root / "LOCK")],
                               capture_output=True, timeout=15, check=False)
        self.assertEqual(child.returncode, 23, child.stderr)

    def test_lock_contention_is_explicit_and_root_not_adopted(self):
        class Busy(FakeHost):
            def lock(self, path): raise rec.Refusal("lock_busy")
        r = self.recorder(host=Busy())
        self.assertEqual(r.initialize()["reason"], "lock_busy")
        self.assertEqual(r.enroll(*self.record())["reason"], "halted")
        self.assertEqual(self.recorder().initialize()["reason"], "destination_exists")

    def test_expiry_and_naive_clock_refuse_without_fallback(self):
        for now, reason in [(datetime.fromisoformat(END.replace("Z", "+00:00")), "time"),
                            (datetime(2035, 4, 5), "clock")]:
            self.host.now = now
            r = self.recorder(root=AUDIT / uuid.uuid4().hex)
            self.assertEqual(r.initialize()["reason"], reason)
            self.assertFalse(r.root.exists())

    def test_cli_without_mode_or_bindings_is_inert_and_sanitized(self):
        for args in [[], ["--mode", "record"], ["--mode", "other"], ["--help"]]:
            out = io.StringIO(); err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = rec.main(args)
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(out.getvalue())["status"], "refused")
            self.assertEqual(err.getvalue(), "")
        self.assertFalse(self.root.exists())

    def test_closed_refusal_dispositions_and_counts(self):
        r = self.recorder(); r.initialize()
        bad, action = self.record(); bad["story"] = "synthetic-invalid-field"
        refusal = r.enroll(bad, action)
        self.assertEqual((refusal["disposition"], refusal["reason"], refusal["refused_cases"]),
                         ("REFUSE_CASE", "schema", 1))
        good, action = self.record(); self.assertEqual(r.enroll(good, action)["status"], "recorded")
        duplicate = r.enroll(good, action)
        self.assertEqual((duplicate["disposition"], duplicate["duplicates"], duplicate["reported"]),
                         ("REFUSE_CASE", 1, 3))
        self.assertEqual((duplicate["accepted"], duplicate["refused_cases"]), (1, 2))
        bad, action = self.record("7"); bad["action_sha256"] = "0" * 64
        stop = r.enroll(bad, action)
        self.assertEqual((stop["disposition"], stop["reason"]), ("STOP_NOT_READY", "binding"))
        self.assertIsNone(stop["reported"])
        self.assertEqual(r.enroll(*self.record("8"))["reason"], "halted")

    def test_unknown_clock_and_budget_are_sticky_stop_not_ready(self):
        for reason in ("clock", "budget"):
            host = FakeHost()
            r = self.recorder(root=AUDIT / uuid.uuid4().hex, host=host); r.initialize()
            if reason == "clock": host.now = datetime(2035, 4, 5)
            else: r._total_bytes = rec.BUDGET_BYTES
            result = r.enroll(*self.record())
            self.assertEqual((result["disposition"], result["reason"]), ("STOP_NOT_READY", reason))
            host.now = self.host.now
            self.assertEqual(r.enroll(*self.record("7"))["reason"], "halted")
        self.assertEqual(str(rec.Refusal("sensitive arbitrary adapter message")), "io")

    def test_observed_session_labels_and_canonical_uuid_preserved(self):
        reporter = dict(IDENTITY, session_id="wd-reboot-synthetic-20350405T060000Z",
                        agent_uuid="11111111-1111-4111-8111-111111111111")
        p = json.loads(self.p); p["allowed_reporters"] = [reporter]; pb = encoded(p)
        bound = bindings(pb, self.b, digest(pb), digest(self.b))
        self.assertEqual(bound.reporters, ((reporter["agent"], reporter["agent_uuid"], reporter["session_id"]),))
        self.bindings = bound
        r = self.recorder(); r.initialize()
        value, action = self.record()
        receipt = json.loads(action)
        value["reporter"] = receipt["reporter"] = reporter
        action = encoded(receipt); value["action_receipt_sha256"] = digest(action)
        self.assertEqual(r.enroll(value, action)["status"], "recorded")
        for missing in (None, "", "../escape", "label with spaces", "x" * 161):
            p["allowed_reporters"] = [dict(reporter, session_id=missing)]; pb = encoded(p)
            with self.assertRaises(rec.Refusal): bindings(pb, self.b, digest(pb), digest(self.b))

    def test_original_protocol_baseline_digests_are_not_capsule_hashes(self):
        self.assertNotEqual(self.bindings.protocol_sha256, digest(self.p))
        self.assertEqual(self.bindings.protocol_sha256, ORIGINAL_PROTOCOL)
        self.assertEqual(self.bindings.baseline_sha256, ORIGINAL_BASELINE)
        for field in ("protocol_sha256", "baseline_sha256"):
            with self.assertRaises(rec.Refusal):
                bindings(self.p, self.b, digest(self.p), digest(self.b), **{field: "0" * 64})

    def test_explicit_clock_resolution_is_required_and_conservative(self):
        r = self.recorder(); r.initialize()
        for resolution in (None, True, 0, 0.001, 2):
            value, a = self.record(); value["action_time_resolution_seconds"] = resolution
            self.assertEqual(r.enroll(value, a)["disposition"], "REFUSE_CASE")
        b = json.loads(self.b); b["sealed_at_utc"] = "2035-04-05T05:59:59Z"; bb = encoded(b)
        with self.assertRaises(rec.Refusal): bindings(self.p, bb, digest(self.p), digest(bb))


if __name__ == "__main__":
    unittest.main()
