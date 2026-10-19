"""AUTHOR synthetic controller tests. Never import Mind, goldset or a model.

Controller child creation and positive startup context are synthetic. Tests
install a caller-local sys view; no host/global sys flags or options change.
Runtime refusals are tested with explicitly invalid flags. The real toy child
asserts its OWN flags unmocked. One bounded stdlib-only toy subprocess
checks startup policy; it never runs the M1 worker or imports measured code,
installed packages, models or private data. This is not OS containment or M1.
"""
import copy
import os
import py_compile
import contextlib
import datetime as dt
import hashlib
import importlib.util
import importlib.machinery
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
import uuid

HERE = Path(__file__).absolute().parent
AUDIT = Path.cwd() / ".codex-audit/m1-caller-pytest-portability-20261019"
CANDIDATE = HERE / "m1_phase_caller.py"
if CANDIDATE.exists():
    spec = importlib.util.spec_from_file_location("phase_caller_candidate", CANDIDATE)
    caller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(caller)
else:
    caller = None
FROZEN_PINS = dict(caller.PINS) if caller else {}
FROZEN_NATIVE_RUNNER = "def native_runner(c, design_sha):\n    root = Path(c[\"root\"])\n    comp = module_from(c[\"helpers\"][\"comparator\"][\"path\"], \"bm25_comparator\")\n    blind = module_from(c[\"helpers\"][\"blind\"][\"path\"], \"m1_blind_runner\")\n    need(sys.modules[\"bm25_comparator\"] is comp, \"pins\")\n    v21 = source_module(c, \"sandbox/mind/remember/remember_v21.py\", \"m1_p2_v21\")\n    items = [blind.BlindCase(row[\"id\"], row[\"situation\"], row[\"time\"],\n                            file_hash(root / \"caseset/snapshots\" / (\"snap-\" + row[\"id\"] + \".json\")))\n             for row in cases(c)]\n    prepared = load_json(root / \"PREPARED.json\")\n    factory = blind.prepared_native_factory(v21, c[\"binding\"], prepared)\n    return blind.BlindRunner(c[\"binding\"], factory, blind.FileJournal(root / \"journal\", c[\"budget_bytes\"]),\n                             design_sha), items\n"


class SyntheticStartupFlags:
    def __init__(self, original):
        self.original = original
        self.isolated = 1
        self.no_site = 1
        self.dont_write_bytecode = 1

    def __getattr__(self, name):
        return getattr(self.original, name)


class SyntheticStartupSys:
    """Only caller.sys is replaced; delegate non-startup attributes unchanged."""
    def __init__(self, original, prefix):
        self.original = original
        self.flags = SyntheticStartupFlags(original.flags)
        self.pycache_prefix = prefix
        self.dont_write_bytecode = True
        self._xoptions = dict(original._xoptions, pycache_prefix=prefix)

    def __getattr__(self, name):
        return getattr(self.original, name)


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
        self.package_root = self.folder / "toy-packages"
        self.package_root.mkdir()
        self.toy = self.package_root / "startup_toy.py"
        self.toy.write_bytes(b"VALUE = 'FRESH'\n")
        self.config["runtime_files"][str(self.toy)] = caller.file_hash(self.toy)
        self.config["startup"] = {
            "schema": "wd.m1-p2-startup.v1",
            "package_roots": [{"path": str(self.package_root),
                               "runtime_subset_sha256": caller.digest(
                                   {str(self.toy): caller.file_hash(self.toy)})}],
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
        with self.startup_context("parent", go["phase"] if go else "history"):
            return caller.execute(self.design, self.design_sha, go,
                                  expected_go_sha=caller.digest(go),
                                  launcher=worker or Mock())

    @contextlib.contextmanager
    def startup_context(self, role, phase):
        prefix = str(self.root / ".phases" / phase / (role + "-pycache"))
        with patch.object(caller, "sys", SyntheticStartupSys(caller.sys, prefix)):
            yield

    def save_design(self):
        self.design.write_bytes(body(self.config))
        self.design_sha = caller.file_hash(self.design)

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
            with self.startup_context("worker", "history"), patch.object(caller, "dispatch", side_effect=dispatch):
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


    def test_MUTANT_WORKER_SITE_STARTUP_FLAGS_all_phases(self):
        for phase in caller.PHASES:
            with self.subTest(phase=phase):
                args = caller.phase_argv(self.design, self.design_sha, self.folder / "GO.json", "a"*64, phase)
                self.assertEqual(args[1:5], ["-I", "-B", "-S", "-X"])
                self.assertEqual(args[5], "pycache_prefix=" + str(
                    self.root / ".phases" / phase / "worker-pycache"))

    def test_MUTANT_PARENT_STARTUP_UNSPECIFIED_exact_argv(self):
        args = caller.parent_argv(self.design, self.design_sha, self.folder / "GO.json", "a"*64, "history")
        self.assertEqual(args[:6], [sys.executable, "-I", "-B", "-S", "-X",
                         "pycache_prefix=" + str(self.root / ".phases/history/parent-pycache")])
        self.assertEqual(args[6:8], [str(CANDIDATE), "execute"])

    def test_MUTANT_UNPINNED_PACKAGE_ROOT_matrix(self):
        good = copy.deepcopy(self.config["startup"])
        for roots in [[], [{"path": "relative", "runtime_subset_sha256": "a"*64}],
                      [{"path": str(self.package_root), "runtime_subset_sha256": "a"*64}],
                      good["package_roots"] * 2,
                      [{"path": str(self.folder), "runtime_subset_sha256": "a"*64}]]:
            with self.subTest(roots=roots):
                self.config["startup"]["package_roots"] = roots
                self.save_design()
                with self.assertRaises(caller.Refused):
                    caller.validate_design(self.design, self.design_sha)
        self.config["startup"] = good

    def test_MUTANT_PACKAGE_ROOT_CONFIG_not_optional(self):
        for change in ({}, {"schema": "wrong", "package_roots": []}):
            self.config["startup"] = change
            self.save_design()
            with self.subTest(change=change), self.assertRaises(caller.Refused):
                caller.validate_design(self.design, self.design_sha)

    def test_MUTANT_PARENT_PYCACHE_IDENTITY_rejected_before_latch(self):
        go = self.go()
        with patch.object(caller.sys, "pycache_prefix", str(self.folder / "foreign")):
            with self.assertRaises(caller.Refused):
                caller.execute(self.design, self.design_sha, go, caller.digest(go), launcher=Mock())
        self.assertFalse(self.root.exists())

    def test_MUTANT_STARTUP_POLICY_NOT_BOUND_receipt_and_new_empty_caches(self):
        def fake(argv, config, stage):
            for role in ("parent", "worker"):
                prefix = stage / (role + "-pycache")
                self.assertTrue(prefix.is_dir())
                self.assertEqual(list(prefix.iterdir()), [])
            receipt = caller.load_json(stage / "STARTUP.json")
            self.assertEqual(receipt["startup_sha256"], caller.digest(self.config["startup"]))
            self.assertEqual(receipt["design_sha256"], self.design_sha)
            self.success(argv, config, stage)
        self.invoke(worker=fake)

    def test_MUTANT_PREEXISTING_PYCACHE_never_adopted_or_deleted(self):
        self.root.mkdir()
        stage = self.root / ".phases/history"
        stage.mkdir(parents=True)
        prefix = stage / "worker-pycache"
        prefix.mkdir()
        sentinel = prefix / "retained.bin"
        sentinel.write_bytes(b"RETAINED")
        with self.assertRaises(caller.Refused):
            caller.reserve_startup(self.config, stage, self.design_sha)
        self.assertEqual(sentinel.read_bytes(), b"RETAINED")

    def test_MUTANT_ADDSITEDIR_OR_PYTHONPATH_NOT_ALLOWED(self):
        self.config["environment"]["PYTHONPATH"] = str(self.package_root)
        self.save_design()
        with self.assertRaises(caller.Refused):
            caller.validate_design(self.design, self.design_sha)
        self.config["environment"].pop("PYTHONPATH")
        before = sys.path[:]
        try:
            caller.activate_package_roots(self.config)
            self.assertEqual(sys.path[:len(before)], before)
            self.assertEqual(sys.path[len(before):], [str(self.package_root)])
            caller.activate_package_roots(self.config)
            self.assertEqual(sys.path.count(str(self.package_root)), 1)
        finally:
            sys.path[:] = before

    def test_MUTANT_ROOT_DRIFT_after_design_validation_refuses(self):
        self.toy.write_bytes(b"VALUE = 'DRIFT'\n")
        with self.assertRaises(caller.Refused):
            caller.activate_package_roots(self.config)

    def test_MUTANT_PTH_EXECUTION_AND_STALE_SOURCE_PYC_toy_subprocess(self):
        # A deliberately timestamp-valid stale TOY pyc, never measured Source.
        stale = b"VALUE = 'STALE'\n"
        fresh = b"VALUE = 'FRESH'\n"
        self.assertEqual(len(stale), len(fresh))
        self.toy.write_bytes(stale)
        os.utime(self.toy, (1700000000, 1700000000))
        cache = Path(importlib.util.cache_from_source(str(self.toy)))
        py_compile.compile(str(self.toy), doraise=True)
        stale_hash = caller.file_hash(cache)
        self.toy.write_bytes(fresh)
        os.utime(self.toy, (1700000000, 1700000000))
        # Prove the retained pyc's payload is stale, without a second child.
        stale_mod = importlib.machinery.SourcelessFileLoader("stale_toy_evidence", str(cache)).load_module()
        self.assertEqual(stale_mod.VALUE, "STALE")
        marker = self.folder / "PTH_MUST_NOT_EXECUTE.txt"
        pth = self.package_root / "evil.pth"
        pth.write_text("import pathlib; pathlib.Path(" + repr(str(marker)) + ").write_text('BAD')\n")
        (self.package_root / "sitecustomize.py").write_text(
            "raise RuntimeError('MUTANT_GLOBAL_SITE_STARTUP')\n")
        injected = self.folder / "unverified-PYTHONPATH"
        injected.mkdir()
        (injected / "startup_toy.py").write_text("VALUE = 'INJECTED'\n")
        # The .pth bytes are present; pin the toy root's explicitly selected files.
        selected = {str(p): caller.file_hash(p) for p in
                    (self.toy, pth, self.package_root / "sitecustomize.py", cache)}
        self.config["runtime_files"] = dict(selected)
        self.config["startup"]["package_roots"][0]["runtime_subset_sha256"] = caller.digest(selected)
        self.save_design()
        args = caller.phase_argv(self.design, self.design_sha, self.folder / "GO.json", "a"*64, "history")
        # RED stops HERE on the old argv: never launch an unsafe global-site child.
        self.assertIn("-S", args)
        self.assertIn("-X", args)
        self.root.mkdir()
        stage = self.root / ".phases/history"
        stage.mkdir(parents=True)
        caller.reserve_startup(self.config, stage, self.design_sha)
        code = (
            "import json,runpy,sys; "
            "m=runpy.run_path(sys.argv[1],run_name='startup_toy_caller'); "
            "c=json.load(open(sys.argv[2])); m['activate_package_roots'](c); "
            "import startup_toy; "
            "print(json.dumps({'value':startup_toy.VALUE,'cached':startup_toy.__cached__,"
            "'no_site':sys.flags.no_site,'isolated':sys.flags.isolated,"
            "'bytecode_disabled':sys.dont_write_bytecode,'site_loaded':'site' in sys.modules}))")
        toy_argv = args[:args.index(str(CANDIDATE))] + ["-c", code, str(CANDIDATE), str(self.design)]
        env = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"),
               "PYTHONPATH": str(injected), "PYTHONSTARTUP": str(pth)}
        child = subprocess.run(toy_argv, cwd=Path.cwd(), env=env, capture_output=True,
                               timeout=20, check=False, stdin=subprocess.DEVNULL)
        (self.folder / "TOY_ARGV.json").write_bytes(body(toy_argv))
        (self.folder / "TOY_STDOUT.log").write_bytes(child.stdout)
        (self.folder / "TOY_STDERR.log").write_bytes(child.stderr)
        self.assertEqual(child.returncode, 0, "stdlib toy subprocess failed; inspect retained log")
        result = json.loads(child.stdout)
        self.assertEqual(result["value"], "FRESH")
        self.assertEqual(result["no_site"], 1)
        self.assertEqual(result["isolated"], 1)
        self.assertTrue(result["bytecode_disabled"])
        self.assertFalse(result["site_loaded"])
        self.assertTrue(Path(result["cached"]).is_relative_to(stage / "worker-pycache"))
        self.assertFalse(marker.exists())
        self.assertEqual(caller.file_hash(cache), stale_hash)
        self.assertEqual(list((stage / "worker-pycache").iterdir()), [])



    def test_MUTANT_WORKER_STARTUP_CONTEXT_checked_before_dispatch(self):
        before = sys.path[:]
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), before))
        def fake(argv, config, stage):
            go = caller.load_json(stage / "GO.json")
            dispatch = Mock()
            # invoke() has parent prefix, deliberately wrong for the worker.
            with patch.object(caller, "dispatch", dispatch):
                with self.assertRaises(caller.Refused):
                    caller.worker(self.design, self.design_sha, go, caller.digest(go), "history")
            dispatch.assert_not_called()
            self.assertFalse((stage / "WORKER_CONSUMED.json").exists())
            self.success(argv, config, stage)
        self.invoke(worker=fake)

    def test_MUTANT_STARTUP_CHECK_IN_NATIVE_RUNNER_no_unrelated_dispatch_edit(self):
        import ast
        frozen = FROZEN_NATIVE_RUNNER
        actual = next(node for node in ast.parse(CANDIDATE.read_text()).body
                      if isinstance(node, ast.FunctionDef) and node.name == "native_runner")
        expected = ast.parse(frozen).body[0]
        self.assertEqual(ast.dump(actual, include_attributes=False),
                         ast.dump(expected, include_attributes=False))



    def test_MUTANT_FIXTURE_PATCHES_GLOBAL_SYS_instead_of_caller_local(self):
        original_caller_sys = caller.sys
        flags, prefix = sys.flags, sys.pycache_prefix
        options, bytecode = dict(sys._xoptions), sys.dont_write_bytecode
        with self.startup_context("parent", "history"):
            self.assertIsNot(caller.sys, original_caller_sys)
            self.assertEqual(caller.sys.flags.no_site, 1)
            self.assertEqual(caller.sys.flags.isolated, 1)
            self.assertTrue(caller.sys.dont_write_bytecode)
            self.assertIs(sys.flags, flags)
            self.assertEqual(sys.pycache_prefix, prefix)
            self.assertEqual(sys._xoptions, options)
            self.assertEqual(sys.dont_write_bytecode, bytecode)
        self.assertIs(caller.sys, original_caller_sys)
        self.assertIs(sys.flags, flags)
        self.assertEqual(sys.pycache_prefix, prefix)
        self.assertEqual(sys._xoptions, options)

    def test_MUTANT_RUNTIME_FLAGS_WEAKENED_fixture_must_not_disable_guard(self):
        for target, field, bad in [("flags", "isolated", 0), ("flags", "no_site", 0),
                                    ("flags", "dont_write_bytecode", 0),
                                    ("sys", "dont_write_bytecode", False),
                                    ("sys", "pycache_prefix", "FOREIGN")]:
            with self.subTest(target=target, field=field), self.startup_context("parent", "history"):
                obj = caller.sys.flags if target == "flags" else caller.sys
                go = self.go()
                child = Mock()
                with patch.object(obj, field, bad), self.assertRaises(caller.Refused):
                    caller.execute(self.design, self.design_sha, go, caller.digest(go), launcher=child)
                child.assert_not_called()
                self.assertFalse(self.root.exists())


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PhaseCaller)
    # Evidence write-new; no raw synthetic traceback needs to enter tool output.
    AUDIT.mkdir(parents=True, exist_ok=True)
    log = AUDIT / ("TEST-" + uuid.uuid4().hex + ".log")
    with log.open("x", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    print(json.dumps({"tests": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "status": "PASS" if result.wasSuccessful() else "FAIL",
                      "log": str(log), "evidence_class": "AUTHOR_SYNTHETIC_CONTROLLER_PLUS_STDLIB_TOY_NOT_M1_NATIVE"}))
    sys.exit(0 if result.wasSuccessful() else 1)
