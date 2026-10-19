"""AUTHOR synthetic controller tests. Never import Mind, goldset or a model.

All child creation is mocked. Fixture files stay in this owned audit folder.
This is not a native process, OS-isolation or actual-measurement test.
"""
import copy
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
import uuid

HERE = Path(__file__).absolute().parent
AUDIT = Path.cwd() / ".codex-audit/m1-p2-phase-caller-tools-20261019"
CANDIDATE = HERE / "m1_phase_caller.py"
if CANDIDATE.exists():
    spec = importlib.util.spec_from_file_location("phase_caller_candidate", CANDIDATE)
    caller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(caller)
else:
    caller = None
FROZEN_PINS = dict(caller.PINS) if caller else {}


def body(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


class PhaseCaller(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(caller, "MUTANT_PHASE_CALLER_ABSENT")
        self.folder = AUDIT / "fixtures" / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.root = self.folder / "new-p2"
        self.source = self.folder / "source"
        self.source.mkdir()
        self.source_file = self.source / "synthetic.py"
        self.source_file.write_bytes(b"# synthetic; never imported\n")
        self.sha = caller.file_hash(self.source_file)
        self.config = {
            "schema": caller.DESIGN_SCHEMA, "run_id": "SYNTHETIC-ONE-P2",
            "source_commit": caller.MEASURED_COMMIT, "source_root": str(self.source),
            "cwd": str(Path.cwd()), "root": str(self.root), "python": sys.executable,
            "python_sha256": caller.file_hash(sys.executable),
            "caller_sha256": caller.file_hash(CANDIDATE),
            "source_files": {str(self.source_file): self.sha},
            "cache_files": {str(self.source_file): self.sha},
            "runtime_files": {str(self.source_file): self.sha},
            "helpers": {key: {"path": str(self.source_file), "sha256": self.sha}
                        for key in caller.PINS},
            "memory_dir": str(self.folder / "never-open-memory"),
            "transcripts_root": str(self.folder / "never-open-transcripts"),
            "binding": {"source_commit": caller.MEASURED_COMMIT,
                        "source_files": {str(self.source_file): self.sha},
                        "encoder": {"revision": caller.CACHE_REVISION},
                        "rule": {"guard_band_s": 1.0, "bm25_k1": 1.2, "bm25_b": .75}},
            "environment": dict(caller.REQUIRED_ENV), "timeout_s": 30,
            "budget_bytes": 1024 * 1024, "case_limit": 1000,
        }
        self.design = self.folder / "DESIGN.json"
        self.design.write_bytes(body(self.config))
        self.design_sha = caller.file_hash(self.design)
        self.pins = patch.object(caller, "PINS", {key: self.sha for key in caller.PINS})
        self.pins.start()
        self.addCleanup(self.pins.stop)
        self.free = patch.object(caller, "free_bytes", return_value=10 * 1024**3)
        self.free.start()
        self.addCleanup(self.free.stop)

    def go(self, phase="history", **changes):
        go = {"schema": caller.GO_SCHEMA, "agent": "codex-lead-1",
              "agent_uuid": caller.LEAD_UUID, "task_id": caller.TASK,
              "session_id": caller.LEAD_SESSION, "event_ref": "1" * 64,
              "design_sha256": self.design_sha, "run_id": self.config["run_id"],
              "phase": phase, "execute": True, "prior_receipts": {},
              "issued_at_utc": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat(),
              "expires_at_utc": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30)).isoformat()}
        go.update(changes)
        return go

    def invoke(self, go=None, worker=None):
        go = self.go() if go is None else go
        return caller.execute(self.design, self.design_sha, go,
                              expected_go_sha=caller.digest(go),
                              launcher=worker or Mock())

    def success(self, argv, config, stage):
        # No subprocess: simulate the two exact declared history files only.
        self.assertTrue((self.root / ".phases/history/INTENT.json").is_file())
        self.assertFalse((self.root / "ops.jsonl").exists())
        (self.root / "ops.jsonl").write_bytes(b"{}\n")
        (self.root / "history.json").write_bytes(b"{}\n")

    def test_MISSING_GO_refuses_before_root_or_child(self):
        worker = Mock()
        with self.assertRaises(caller.Refused):
            self.invoke({}, worker)
        worker.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_GO_FIELD_MATRIX_no_weak_truthiness(self):
        for key, value in [("agent", "fable-5"), ("agent_uuid", "0" * 36),
                           ("task_id", "foreign"), ("design_sha256", "e" * 64),
                           ("run_id", "other"), ("phase", "grade"),
                           ("execute", "true"), ("execute", 1),
                           ("event_ref", "not-a-ref"), ("session_id", "")]:
            with self.subTest(key=key), self.assertRaises(caller.Refused):
                self.invoke(self.go(**{key: value}))
            self.assertFalse(self.root.exists())

    def test_GO_HASH_rejects_modified_typed_record(self):
        with self.assertRaises(caller.Refused):
            caller.execute(self.design, self.design_sha, self.go(), "0" * 64, launcher=Mock())
        self.assertFalse(self.root.exists())

    def test_EXPIRED_OR_FUTURE_GO_rejects_before_latch(self):
        now = dt.datetime.now(dt.timezone.utc)
        for changes in [
            {"expires_at_utc": (now - dt.timedelta(seconds=1)).isoformat()},
            {"issued_at_utc": (now + dt.timedelta(minutes=1)).isoformat()},
            {"expires_at_utc": "2030-01-01T00:00:00Z"},
            {"expires_at_utc": "2026-10-19"},
        ]:
            with self.subTest(changes=changes), self.assertRaises(caller.Refused):
                self.invoke(self.go(**changes))
            self.assertFalse(self.root.exists())

    def test_NATIVE_ENV_missing_cache_binding_refused_no_import(self):
        with self.assertRaises(caller.Refused):
            caller.validate_native_config(self.config, "prepare")

    def test_LOGS_are_hashed_in_result_not_just_private(self):
        def fake(argv, config, stage):
            (stage / "stdout.log").write_bytes(b"fixed\n")
            (stage / "stderr.log").write_bytes(b"")
            self.success(argv, config, stage)
        result = self.invoke(worker=fake)
        self.assertEqual(result["logs"][".phases/history/stdout.log"],
                         caller.file_hash(self.root / ".phases/history/stdout.log"))

    def test_DESIGN_HASH_rejects_changed_config(self):
        self.design.write_bytes(body(dict(self.config, run_id="other")))
        with self.assertRaises(caller.Refused):
            self.invoke()
        self.assertFalse(self.root.exists())

    def test_VALID_HISTORY_consumed_before_effect_and_hash_receipt(self):
        result = self.invoke(worker=self.success)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(set(result["outputs"]), {"ops.jsonl", "history.json"})
        self.assertEqual(result["outputs"]["ops.jsonl"], caller.path_hash(self.root / "ops.jsonl"))

    def test_REPLAY_consumed_even_after_failure(self):
        worker = Mock(side_effect=RuntimeError("SECRET_SENTINEL"))
        with self.assertRaises(caller.Refused):
            self.invoke(worker=worker)
        with self.assertRaises(caller.Refused):
            self.invoke(worker=worker)
        self.assertEqual(worker.call_count, 1)
        self.assertTrue((self.root / ".phases/history/VOID.json").exists())
        self.assertNotIn("SECRET_SENTINEL", (self.root / ".phases/history/VOID.json").read_text())

    def test_EXISTING_ROOT_not_adopted(self):
        self.root.mkdir()
        with self.assertRaises(caller.Refused):
            self.invoke()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_PRIOR_CHAIN_required_and_rehashed(self):
        self.invoke(worker=self.success)
        receipt = self.root / ".phases/history/RESULT.json"
        go = self.go("candidates", prior_receipts={"history": caller.file_hash(receipt)})
        for changed in [{}, {"history": "e" * 64}]:
            with self.assertRaises(caller.Refused):
                self.invoke(dict(go, prior_receipts=changed))
        (self.root / "ops.jsonl").write_bytes(b"CHANGED")
        with self.assertRaises(caller.Refused):
            self.invoke(go)
        self.assertFalse((self.root / ".phases/candidates").exists())

    def test_MISSING_RECEIPT_fixed_refusal_no_effect(self):
        self.invoke(worker=self.success)
        go = self.go("candidates", prior_receipts={"history": "a" * 64})
        # Simulate a lost/renamed receipt, retain its bytes as evidence.
        receipt = self.root / ".phases/history/RESULT.json"
        receipt.rename(receipt.with_name("RETAINED_RESULT.json"))
        with self.assertRaises(caller.Refused):
            self.invoke(go)
        self.assertFalse((self.root / ".phases/candidates").exists())

    def test_PARTIAL_OUTPUT_never_complete_or_retry(self):
        def partial(*args):
            (self.root / "ops.jsonl").write_bytes(b"partial")
        with self.assertRaises(caller.Refused):
            self.invoke(worker=partial)
        self.assertFalse((self.root / ".phases/history/RESULT.json").exists())
        self.assertTrue((self.root / "ops.jsonl").exists())
        with self.assertRaises(caller.Refused):
            self.invoke()

    def test_TIMEOUT_partial_output_consumed(self):
        def timeout(*args):
            (self.root / "ops.jsonl").write_bytes(b"partial")
            raise caller.Refused("timeout")
        with self.assertRaisesRegex(caller.Refused, "timeout"):
            self.invoke(worker=timeout)
        self.assertTrue((self.root / ".phases/history/VOID.json").exists())

    def test_BUDGET_preflight_before_latch(self):
        with patch.object(caller, "free_bytes", return_value=3 * 1024**3):
            with self.assertRaises(caller.Refused):
                self.invoke()
        self.assertFalse(self.root.exists())

    def test_BUDGET_after_write_invalidates_phase(self):
        with patch.object(caller, "used_bytes", side_effect=[0, 2 * 1024**2]):
            with self.assertRaises(caller.Refused):
                self.invoke(worker=self.success)
        self.assertFalse((self.root / ".phases/history/RESULT.json").exists())

    def test_PINS_no_drift_before_side_effect(self):
        self.source_file.write_bytes(b"changed")
        with self.assertRaises(caller.Refused):
            self.invoke()
        self.assertFalse(self.root.exists())

    def test_RULE_BOOLEAN_not_numeric_equivalence_before_model(self):
        changed = copy.deepcopy(self.config)
        changed["binding"]["rule"]["guard_band_s"] = True
        self.design.write_bytes(body(changed))
        self.design_sha = caller.file_hash(self.design)
        with self.assertRaises(caller.Refused):
            self.invoke(worker=self.success)
        self.assertFalse(self.root.exists())

    def test_GOLD_CANNOT_OPEN_before_both_arms_and_manifest(self):
        reader = Mock()
        for published in [{}, {"arm_A.json": "a" * 64},
                          {"arm_A.json": "a" * 64, "arm_B.json": "b" * 64}]:
            with self.assertRaises(caller.Refused):
                caller.validate_publication(self.root, self.go("grade", arms_publication=published), reader)
        reader.assert_not_called()

    def test_COMPILED_ARGV_includes_every_phase_without_capture_or_extra_comparator(self):
        for stage in caller.PHASES:
            args = caller.phase_argv(self.design, self.design_sha, self.folder / "go.json", "a" * 64, stage)
            self.assertIn(stage, args)
            self.assertNotIn("--capture", args)
            self.assertNotIn("bm25_comparator.py", args)
            self.assertEqual(args[0], sys.executable)

    def test_OWNED_PROCESS_TIMEOUT_kills_and_waits_same_handle(self):
        self.root.mkdir()
        stage = self.root / "stage"
        stage.mkdir()
        child = Mock()
        child.poll.return_value = None
        with patch.object(caller.subprocess, "Popen", return_value=child) as popen:
            with patch.object(caller.time, "monotonic", side_effect=[0, 100, 101]):
                with self.assertRaisesRegex(caller.Refused, "timeout"):
                    caller.launch([sys.executable, "MOCK_ONLY"], self.config, stage)
        child.kill.assert_called_once()
        child.wait.assert_called_once()
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_CLEANUP_FAILURE_not_hidden(self):
        self.root.mkdir()
        stage = self.root / "stage"
        stage.mkdir()
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = OSError("SECRET_SENTINEL")
        with patch.object(caller.subprocess, "Popen", return_value=child):
            with patch.object(caller.time, "monotonic", side_effect=[0, 100, 101]):
                with self.assertRaisesRegex(caller.Refused, "cleanup_failed"):
                    caller.launch([sys.executable, "MOCK_ONLY"], self.config, stage)

    def test_STAGE_DEADLINE_no_child_after_GO_expiry(self):
        self.root.mkdir()
        stage = self.root / "stage"
        stage.mkdir()
        config = dict(self.config, deadline_utc="2020-01-01T00:00:00+00:00")
        with patch.object(caller.subprocess, "Popen") as child:
            with self.assertRaises(caller.Refused):
                caller.launch([sys.executable, "MOCK_ONLY"], config, stage)
        child.assert_not_called()

    def test_PRIVATE_LOGS_open_exclusively_not_wb(self):
        self.root.mkdir()
        stage = self.root / "stage"
        stage.mkdir()
        (stage / "stdout.log").write_bytes(b"retained")
        with patch.object(caller.subprocess, "Popen") as child:
            with self.assertRaises(Exception):
                caller.launch([sys.executable, "MOCK_ONLY"], self.config, stage)
        child.assert_not_called()
        self.assertEqual((stage / "stdout.log").read_bytes(), b"retained")

    def test_REGISTERED_HELPER_PINS_not_a_mock_allowlist(self):
        expected = {
            "goldset": "DAAECD7402FFC052CF7175CE4AECE4A7133ECACBF9024E6F507BCA4865801EA6",
            "grader": "074B443657DAF72797B66994FD6AFA5F2F4DD9384C26D3B955E429F451740117",
            "comparator": "A1667A27DA015DCAED0F97960BBEDE3281DAB3540018106D2458879DB213579A",
            "blind": "2011C5DCA6B5E8F8880A7117911136D446FAF5FA7D87FAF24DBB6C032408B862"}
        self.assertEqual(FROZEN_PINS, {k: v.lower() for k, v in expected.items()})

    def test_PREEXISTING_OUTPUT_never_overwritten_by_frozen_tool(self):
        self.invoke(worker=self.success)
        receipt = self.root / ".phases/history/RESULT.json"
        go = self.go("candidates", prior_receipts={"history": caller.file_hash(receipt)})
        target = self.root / "candidates.jsonl"
        target.write_bytes(b"RETAINED")
        worker = Mock(side_effect=lambda *a: target.write_bytes(b"OVERWRITTEN"))
        with self.assertRaises(caller.Refused):
            self.invoke(go, worker)
        self.assertEqual(target.read_bytes(), b"RETAINED")
        worker.assert_not_called()

    def test_VALID_PRIOR_CHAIN_candidates_once(self):
        self.invoke(worker=self.success)
        receipt = self.root / ".phases/history/RESULT.json"
        go = self.go("candidates", prior_receipts={"history": caller.file_hash(receipt)})
        def candidates(*args):
            (self.root / "candidates.jsonl").write_bytes(b"[]\n")
        result = self.invoke(go, candidates)
        self.assertEqual(result["status"], "COMPLETE")
        with self.assertRaises(caller.Refused):
            self.invoke(go, Mock())

    def test_RELATIVE_OUTPUT_escape_refused(self):
        with self.assertRaises(caller.Refused):
            caller.local(self.root, "../retained.json")

    def test_PUBLICATION_requires_two_arm_files_two_manifests_and_review(self):
        self.root.mkdir()
        (self.root / "journal/arms").mkdir(parents=True)
        names = ["journal/arms/arm_A.json", "journal/arms/arm_B.json", "journal/arms/RESULT.json"]
        for name in names:
            (self.root / name).write_bytes(b"{}\n")
        manifest = {"design_sha256": self.design_sha,
                    "arm_files": {p: caller.file_hash(self.root / p) for p in names[:2]}}
        (self.root / "RUN_MANIFEST.json").write_bytes(body(manifest))
        pub = {p: caller.file_hash(self.root / p) for p in names + ["RUN_MANIFEST.json"]}
        go = self.go("grade", arms_publication=pub, arms_review_event="b" * 64)
        caller.validate_publication(self.root, go)
        (self.root / "journal/arms/arm_B.json").write_bytes(b"CHANGED")
        with self.assertRaises(caller.Refused):
            caller.validate_publication(self.root, go)

    def test_PREPARE_exact_lessons_only_APIs_no_model_calls(self):
        self.root.mkdir()
        (self.root / "caseset/snapshots").mkdir(parents=True)
        case = {"id": "SYNTH-1", "situation": "SYNTHETIC", "time": "2026-10-01T01:01:01Z"}
        (self.root / "caseset/blind.jsonl").write_bytes(body(case))
        (self.root / "caseset/snapshots/snap-SYNTH-1.json").write_bytes(b"{}\n")
        corpus, encode, memory, blind = Mock(), Mock(), Mock(), Mock()
        corpus.parse_utc.return_value = "SYNTHETIC-CUTOFF"
        memory.build.return_value = {"encoder": self.config["binding"]["encoder"]}
        blind.digest.return_value = "b" * 64
        blind.tree_sha256.return_value = "c" * 64
        def load(path, name):
            return blind
        with patch.object(caller, "source_module", side_effect=[corpus, encode, memory]):
            with patch.object(caller, "module_from", side_effect=load) as loader:
                caller.dispatch(self.config, self.design_sha, self.go("prepare"))
        folder = self.root / "prepared/SYNTH-1"
        corpus.build.assert_called_once_with(folder / "lessons", [], "SYNTHETIC-CUTOFF", folder / "corpus")
        encode.build.assert_called_once_with(folder / "corpus", folder / "vectors")
        memory.build.assert_called_once_with(folder / "corpus", folder / "vectors", folder / "memory")
        self.assertEqual([call.args[1] for call in loader.call_args_list], ["bm25_comparator", "m1_blind_runner"])

    def test_WORKER_REPLAY_and_fixed_stdout_private_redirect(self):
        seen = []
        def dispatch(config, design, go):
            print("SYNTHETIC_PRIVATE_SENTINEL")
            self.success(None, config, None)
        def launch(argv, config, stage):
            go = caller.load_json(stage / "GO.json")
            with patch.object(caller, "dispatch", side_effect=dispatch):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    caller.worker(self.design, self.design_sha, go, caller.digest(go), "history")
                seen.append(output.getvalue())
                with self.assertRaises(caller.Refused):
                    caller.worker(self.design, self.design_sha, go, caller.digest(go), "history")
        self.invoke(worker=launch)
        self.assertNotIn("SYNTHETIC_PRIVATE_SENTINEL", "".join(seen))
        self.assertIn("SYNTHETIC_PRIVATE_SENTINEL", (self.root / ".phases/history/helper.stdout.log").read_text())

    def test_DISPATCH_RAW_PHASES_exact_registered_main_arguments(self):
        self.root.mkdir()
        tool = Mock()
        tool.main.return_value = 0
        for phase, needed in [("history", "--status"), ("candidates", "--ops"),
                              ("anchors", "--transcripts-root"), ("build", "--budget-root"),
                              ("commit", "--guard")]:
            with self.subTest(phase=phase), patch.object(caller, "module_from", return_value=tool):
                caller.dispatch(self.config, self.design_sha,
                                self.go(phase, anchor_audit={"disputed": []}))
            args = tool.main.call_args.args[0]
            self.assertEqual(args[0], phase)
            self.assertIn(needed, args)
            self.assertNotIn("--capture", args)

    def test_DISPATCH_ARMS_exact_runner_no_second_comparator_call(self):
        self.root.mkdir()
        (self.root / "journal/arms").mkdir(parents=True)
        (self.root / "caseset").mkdir()
        (self.root / "caseset/commitments.json").write_bytes(b"{}\n")
        runner = Mock()
        runner.arms.return_value = {"arm_A_sha256": "a" * 64, "arm_B_sha256": "b" * 64}
        def arms(*args):
            for suffix in ("arm_A.json", "arm_B.json"):
                (self.root / "journal/arms" / suffix).write_bytes(b"{}\n")
            return {"arm_A_sha256": "a" * 64, "arm_B_sha256": "b" * 64}
        runner.arms.side_effect = arms
        with patch.object(caller, "native_runner", return_value=(runner, ["SYNTHETIC"])):
            with patch.object(caller, "module_from") as loader:
                caller.dispatch(self.config, self.design_sha, self.go("arms"))
        runner.arms.assert_called_once_with(["SYNTHETIC"], runner.journal.completed.return_value, {})
        loader.assert_not_called()
        self.assertTrue((self.root / "RUN_MANIFEST.json").exists())

    def test_DISPATCH_GRADE_publication_guard_before_grader_load(self):
        self.root.mkdir()
        grader = Mock()
        grader.grade.return_value = {"verdict": "INSUFFICIENT_N", "kept": 0}
        with patch.object(caller, "module_from", return_value=grader) as loader:
            with self.assertRaises(caller.Refused):
                caller.dispatch(self.config, self.design_sha, self.go("grade"))
            loader.assert_not_called()
            with patch.object(caller, "validate_publication") as gate:
                caller.dispatch(self.config, self.design_sha, self.go("grade"))
            gate.assert_called_once()
        grader.grade.assert_called_once_with(self.root / "caseset/cases.json", self.root / "caseset/commitments.json",
                                             self.root / "journal/arms/arm_A.json", self.root / "journal/arms/arm_B.json")

    def test_PLAN_only_current_GO_phase_not_fabricated_future_permission(self):
        go = self.go()
        go_path = self.folder / "GO.json"
        go_path.write_bytes(body(go))
        stream = io.StringIO()
        code = caller.main(["plan", "--design", str(self.design), "--design-sha256", self.design_sha,
                            "--go", str(go_path), "--go-sha256", caller.digest(go)], stream)
        self.assertEqual(code, 0)
        self.assertEqual(set(json.loads(stream.getvalue())["phase_argv"]), {"history"})
        self.assertFalse(self.root.exists())

    def test_FIXED_STDOUT_never_exception_or_path(self):
        stream = io.StringIO()
        with patch.object(caller, "execute", side_effect=RuntimeError("SECRET_SENTINEL")):
            with patch.object(caller, "load_json", return_value={}):
                code = caller.main(["execute", "--design", str(self.design), "--design-sha256", self.design_sha,
                                    "--go", str(self.folder / "go.json"), "--go-sha256", "a" * 64], stream)
        self.assertNotEqual(code, 0)
        self.assertNotIn("SECRET_SENTINEL", stream.getvalue())
        self.assertNotIn(str(self.folder), stream.getvalue())


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PhaseCaller)
    # Evidence write-new; no raw synthetic traceback needs to enter tool output.
    AUDIT.mkdir(parents=True, exist_ok=True)
    log = AUDIT / ("TEST-" + uuid.uuid4().hex + ".log")
    with log.open("x", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    print(json.dumps({"tests": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "status": "PASS" if result.wasSuccessful() else "FAIL",
                      "log": str(log), "evidence_class": "AUTHOR_SYNTHETIC_MOCK_ONLY"}))
    sys.exit(0 if result.wasSuccessful() else 1)
