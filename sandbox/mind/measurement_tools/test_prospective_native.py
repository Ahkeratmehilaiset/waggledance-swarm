"""Synthetic injected native-worker tests; never import the measured SUT/model."""
import importlib.util
import contextlib
import copy
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import types
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location("prospective_native", Path(__file__).with_name("prospective_native.py"))
native = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = native
SPEC.loader.exec_module(native)


def test_dormant_phase_set():
    assert native.PHASES == ("prepare", "prepass", "arms")


AUDIT = Path("C:/Python/project2/.codex-audit/m1-p3-prospective-20261020/native-author")


def tree_hash(root):
    return native.digest([[p.relative_to(root).as_posix(), p.stat().st_size, native.sha(p.read_bytes())]
                          for p in sorted(root.rglob("*")) if p.is_file()])


class FakeHost(native.Host):
    """Explicit test-only fake identity/runtime. Does not qualify any real host."""
    def __init__(self):
        self.reads, self.events = [], []
        self.rid, self.elevated, self.reject_modules = 8192, False, False
        self.fail_write = None
        self.write_drift = False
    def now(self):
        return dt.datetime(2035, 4, 6, 6, 2, tzinfo=dt.timezone.utc)
    def read(self, ref):
        self.reads.append(ref["path"])
        return super().read(ref)
    def private_read(self, ref):
        self.reads.append(ref["path"])
        return super().private_read(ref)
    def startup(self, c, phase):
        root = self.path(c["root"])
        assert AUDIT in root.parents
        owner = native.json_bytes(self.read(c["owner"]))
        native.need(owner["study_id"] == c["study_id"], "binding")
    def identity(self):
        self.events.append("identity")
        return {"integrity_rid": self.rid, "elevated": self.elevated,
                "python": {"sha256": native.PYTHON_SHA256}, "pid": 1234, "test_only": True}
    def private_stdio(self, c, phase):
        self.events.append("synthetic_private_stdio")
    def check_budget(self, root, extra=0):
        total = 0
        for p in root.rglob("*"):
            native.plain(p)
            if p.is_file():
                total += p.stat().st_size
        native.need(total + extra <= native.BUDGET, "budget")
        return total
    def pin_all(self, c):
        self.events.append("synthetic_pins")
        return {}
    def observe(self, c, runtime):
        self.events.append("synthetic_observation")
        return {"membership_count": 1, "rows": [{"name": "synthetic.fileless", "status": "ACTUAL_FILELESS_MODULE",
                                                 "dynamic_provenance": "UNKNOWN_NOT_OS_ISOLATION"}],
                "rejections": [{"name": "synthetic.bad", "status": "UNKNOWN"}] if self.reject_modules else [],
                "DLL_closure": "UNKNOWN", "synthetic_test_only": True}
    def watchdog(self, root, stage, expires):
        self.events.append("synthetic_watchdog")
        return threading.Event()
    def write(self, root, path, body, *, terminal=False):
        if self.fail_write == path.name:
            raise OSError("PRIVATE_SYNTHETIC_EXCEPTION")
        result = super().write(root, path, body, terminal=terminal)
        if self.write_drift and path.name == "PREPARED.json":
            path.write_bytes(b"damaged synthetic output")
        return result


class FakeJournal:
    def __init__(self, root, host):
        self.root, self.host = root, host
    def completed(self, phase):
        return json.loads((self.root / phase / "RESULT.json").read_bytes())
    def publish(self, phase, result):
        folder = self.root / phase
        folder.mkdir(parents=True, exist_ok=False)
        self.host.write(self.root.parents[1], folder / "INTENT.json", native.canon({"phase": phase}))
        for name, key in (("guard.json", "guarded"), ("applicable.json", "applicable")) if phase == "prepass" else (("arm_A.json", "arm_A"), ("arm_B.json", "arm_B")):
            self.host.write(self.root.parents[1], folder / name, native.canon(result[key]))
        self.host.write(self.root.parents[1], folder / "RESULT.json", native.canon(result))


class FakeRunner:
    def __init__(self, c, prepared, design, journal, backend):
        self.c, self.prepared, self.design, self.journal, self.backend = c, prepared, design, journal, backend
    def prepass(self, cases):
        self.backend.safe_prepass = copy.deepcopy(cases)
        result = {"schema": "wd.m1-prepass.v1", "design_sha256": self.design,
                  "binding_sha256": native.digest(self.c["binding"]), "input_sha256": native.digest(cases),
                  "guarded": [], "applicable": {}, "case_receipts": {}}
        for case in cases:
            result["applicable"][case["id"]] = ["synthetic_lesson.md"]
            result["case_receipts"][case["id"]] = {"snapshot_sha256": case["snapshot_sha256"],
                "memory_sha256": self.prepared[case["id"]]["memory_sha256"], "documents_sha256": "d" * 64}
        self.journal.publish("prepass", result)
        return result
    def arms(self, cases, prepass, commitments):
        self.backend.safe_arms = copy.deepcopy(cases)
        self.backend.commits = copy.deepcopy(commitments)
        native.need(prepass["input_sha256"] == native.digest(cases), "binding")
        result = {"schema": "wd.m1-arms.v1", "arm_A": {cid: ["synthetic_lesson.md"] for cid in commitments},
                  "arm_B": {cid: ["synthetic_lesson.md"] for cid in commitments}, "costs": []}
        self.journal.publish("arms", result)
        return result


class FakeBackend:
    """No SUT imports, encoder, runner or model execution in author tests."""
    def __init__(self, host):
        self.host = host
        self.preparations = 0
        self.failure = False
        self.safe_prepass = self.safe_arms = None
    @contextlib.contextmanager
    def context(self, c):
        print("PRIVATE_SYNTHETIC_HELPER_OUTPUT")
        yield
    def prepare(self, c, shared):
        self.preparations += 1
        if self.failure:
            raise ValueError("PRIVATE_SYNTHETIC_NATIVE_FAILURE")
        for name in ("corpus", "vectors", "memory"):
            folder = shared / name
            folder.mkdir()
            self.host.write(Path(c["root"]), folder / "synthetic.bin", b"synthetic prepared bytes")
        return SimpleNamespace(tree_sha256=tree_hash)
    def runner(self, c, prepared, design, values):
        return FakeRunner(c, prepared, design, FakeJournal(Path(c["root"]) / "native/journal", self.host), self), copy.deepcopy(values)


@pytest.fixture
def bundle():
    AUDIT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=AUDIT) as name:
        root = Path(name)
        baseline, blind = root / "baseline", root / "blind"
        baseline.mkdir()
        blind.mkdir()
        authorizer = {"agent": "synthetic-lead", "agent_uuid": "a" * 32, "session_id": "synthetic-run"}
        study = "1" * 32
        def save(path, value, *, body=None):
            content = native.canon(value) if body is None else body
            path.write_bytes(content)
            return {"path": str(path), "sha256": native.sha(content)}
        protocol = save(root / "protocol.json", {"schema": "wd.m1-p3-protocol-lock.v1", "status": "LOCKED",
            "study_id": study, "native_authorizer": authorizer})
        body = b"---\nname: Synthetic label\n---\nInvented fixture lesson only."
        blob = save(baseline / "synthetic-blob", None, body=body)
        manifest = save(baseline / "manifest.json", {"schema": "wd.m1-p3-baseline.v1", "identity": {
            "study_id": study, "protocol_sha256": protocol["sha256"]}, "started_utc": "2035-04-05T05:59:50Z",
            "sealed_utc": "2035-04-05T05:59:52Z", "files": [{"name": "synthetic_lesson.md", "sha256": blob["sha256"], "bytes": len(body)}]})
        seal = save(baseline / "SEALED.json", {"schema": "wd.m1-p3-baseline-seal.v1", "status": "SEALED",
            "manifest_sha256": manifest["sha256"], "sealed_utc": "2035-04-05T05:59:54.123456Z"})
        snapshot = save(blind / "snapshot.json", {"synthetic_lesson.md": {"sha256": blob["sha256"], "mtime": "2035-04-05T05:59:55.123456Z"}})
        values = [{"id": f"{i + 1:032x}", "situation": "Inspect synthetic service settings", "time": "2035-04-05T06:01:00Z",
                   "snapshot_sha256": snapshot["sha256"]} for i in range(3)]
        cases = save(blind / "runner_cases.json", values)
        save(blind / "commitments.json", {values[0]["id"]: "c" * 64, values[2]["id"]: "e" * 64})
        owner = save(root / "OWNER.json", {"schema": "wd.m1-p3-study-root.v1", "study_id": study,
                                          "protocol_sha256": protocol["sha256"], "root": str(root)})
        encoder = {"revision": native.CACHE_REVISION, "device": "cpu", "torch_threads": 1, "dim": 384,
                   "prefix_passage": "passage: ", "prefix_query": "query: ", "truncate_chars": 2000,
                   "passage_batch": 32, "query_batch": 1, "normalize": True, "dtype": "float32"}
        source_files = {str(root / "synthetic_source.py"): "f" * 64}
        config = {"schema": "wd.m1-p3-native-manifest.v1", "study_id": study, "protocol": protocol,
            "worker": {"path": str(Path(native.__file__)), "sha256": native.sha(Path(native.__file__).read_bytes())},
            "root": str(root), "owner": owner, "python": {"executable": str(root / "python.exe"), "sha256": native.PYTHON_SHA256,
                "root": str(root / "python"), "manifest_path": str(root / "python-manifest.gz"),
                "manifest_sha256": native.PYTHON_MANIFEST_SHA256, "receipt_path": str(root / "python-receipt.json"),
                "receipt_sha256": native.PYTHON_RECEIPT_SHA256, "root_sha256": native.PYTHON_ROOT_SHA256},
            "source_root": str(root / "synthetic_source"), "source_files": source_files,
            "package_roots": [str(root / "synthetic_packages")], "cache_root": str(root / "synthetic_cache"),
            "cache_files": {str(root / "synthetic_weights"): "9" * 64},
            "environment": dict(native.ENV, USERPROFILE=str(root), HF_HOME=str(root), SystemRoot="C:/Windows"),
            "binding": {"source_commit": native.MEASURED_COMMIT, "source_files": source_files, "encoder": encoder,
                        "rule": {"guard_band_s": 1.0, "bm25_k1": 1.2, "bm25_b": .75}},
            "helpers": {"runner": {"path": str(root / "runner.py"), "sha256": native.RUNNER_SHA256},
                        "comparator": {"path": str(root / "comparator.py"), "sha256": native.BM25_SHA256}},
            "stdio_paths": {phase: {"stdout": str(root / (phase + ".stdout.log")), "stderr": str(root / (phase + ".stderr.log"))}
                            for phase in native.PHASES},
            "inputs": {"runner_cases": cases, "snapshot": snapshot, "baseline_manifest": manifest, "baseline_seal": seal,
                       "baseline_blobs": {blob["sha256"]: blob["path"]}, "commitments_path": str(blind / "commitments.json")},
            "corpus_as_of_utc": "2035-04-05T05:59:58Z", "collection_start_utc": "2035-04-05T06:00:00Z",
            "collection_end_utc": "2035-04-06T06:00:00Z"}
        host = FakeHost()
        backend = FakeBackend(host)
        yield {"root": root, "config": config, "host": host, "backend": backend, "authorizer": authorizer,
               "save": save, "values": values}


def invoke(bundle, phase="prepare", *, config=None, go_patch=None, execute=True):
    c = bundle["config"] if config is None else config
    body = native.canon(c)
    prior = {name: native.sha((bundle["root"] / "native" / name / "RESULT.json").read_bytes())
             for name in native.PHASES[:native.PHASES.index(phase)]}
    go = {"schema": "wd.m1-p3-native-go.v1", "status": "NATIVE_PHASE_GO", "study_id": c["study_id"],
          "protocol_sha256": c["protocol"]["sha256"], "manifest_sha256": native.sha(body), "phase": phase,
          "authorizer": bundle["authorizer"], "event_ref": "b" * 64,
          "issued_at_utc": "2035-04-06T06:01:00Z", "expires_at_utc": "2035-04-06T06:25:00Z",
          "prior_receipts": prior, "commitments_sha256": native.sha(Path(c["inputs"]["commitments_path"]).read_bytes()) if phase == "arms" else None}
    go.update(go_patch or {})
    go_bytes = native.canon(go)
    return native.execute(body, native.sha(body), go_bytes, native.sha(go_bytes), phase=phase, execute=execute,
                          _test_host=bundle["host"], _test_backend=bundle["backend"])


def test_explicit_execute_required_before_any_read(bundle):
    result = invoke(bundle, execute=False)
    assert result == {"status": "NOT_READY", "reason": "dormant", "phase": "prepare",
                      "invocation_consumed": False, "actual_data_opened": False}
    assert bundle["host"].reads == [] and not (bundle["root"] / "native").exists()


def test_shared_prepare_prepass_arms_use_exact_same_full_safe_input(bundle, capsys):
    first = invoke(bundle)
    assert first["status"] == "COMPLETE"
    assert bundle["backend"].preparations == 1
    prepared = json.loads((bundle["root"] / "native/PREPARED.json").read_bytes())
    assert set(prepared) == {v["id"] for v in bundle["values"]}
    assert len({v["memory_dir"] for v in prepared.values()}) == 1
    assert len({v["memory_sha256"] for v in prepared.values()}) == 1
    assert all(v["binding_sha256"] == native.digest(bundle["config"]["binding"]) for v in prepared.values())
    lesson = bundle["root"] / "prepared/shared/lessons/synthetic_lesson.md"
    assert dt.datetime.fromtimestamp(lesson.stat().st_mtime, dt.timezone.utc).isoformat(timespec="microseconds") == "2035-04-05T05:59:55.123456+00:00"
    assert invoke(bundle, "prepass")["status"] == "COMPLETE"
    assert invoke(bundle, "arms")["status"] == "COMPLETE"
    assert bundle["backend"].safe_prepass == bundle["backend"].safe_arms == bundle["values"]
    assert set(bundle["backend"].commits) < {r["id"] for r in bundle["values"]}
    manifest = json.loads((bundle["root"] / "native/RUN_MANIFEST.json").read_bytes())
    assert manifest["scorer_unlocked"] is False and manifest["DLL_closure"] == "UNKNOWN"
    assert set(manifest["arm_files"]) == {"arm_A.json", "arm_B.json", "RESULT.json"}
    assert capsys.readouterr() == ("", "")
    for path in (bundle["root"] / "native").rglob("*.json"):
        assert b"PRIVATE_SYNTHETIC_HELPER_OUTPUT" not in path.read_bytes()
    assert not any(Path(path).name in ("cases.pre.json", "cases.json") or "nomination" in path
                   for path in bundle["host"].reads)


def test_runner_design_binds_original_protocol_independently_of_native_manifest(bundle):
    assert invoke(bundle)["status"] == "COMPLETE"
    assert invoke(bundle, "prepass")["status"] == "COMPLETE"
    root = bundle["root"]
    original_protocol_sha = bundle["config"]["protocol"]["sha256"]
    native_manifest_sha = native.sha(native.canon(bundle["config"]))
    assert original_protocol_sha != native_manifest_sha
    prepass = json.loads((root / "native/journal/prepass/RESULT.json").read_bytes())
    outer = json.loads((root / "native/prepass/RESULT.json").read_bytes())
    assert prepass["design_sha256"] == original_protocol_sha
    assert outer["manifest_sha256"] == native_manifest_sha


@pytest.mark.parametrize("rid,elevated", [(12288, False), (8192, True), (None, False)])
def test_actual_medium_required_before_private_inputs_or_backend(bundle, rid, elevated):
    bundle["host"].rid, bundle["host"].elevated = rid, elevated
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and result["reason"] == "medium"
    assert result["invocation_consumed"] is True and result["actual_data_opened"] is False
    assert bundle["backend"].preparations == 0
    assert not any("runner_cases" in p or "baseline" in p or "snapshot" in p for p in bundle["host"].reads)
    assert (bundle["root"] / "native/HALTED.json").is_file()


def test_phase_is_consumed_and_no_retry_or_repair(bundle):
    bundle["backend"].failure = True
    first = invoke(bundle)
    assert first["status"] == "NOT_READY" and first["invocation_consumed"] is True
    assert first["actual_data_opened"] is True
    bundle["backend"].failure = False
    second = invoke(bundle)
    assert second["status"] == "NOT_READY" and bundle["backend"].preparations == 1
    assert "PRIVATE_SYNTHETIC" not in json.dumps(first)


@pytest.mark.parametrize("patch", [{"schema": "wd.m1-p2-phase-go.v1"}, {"status": "GO"},
    {"protocol_sha256": "0" * 64}, {"manifest_sha256": "0" * 64}, {"study_id": "f" * 32},
    {"expires_at_utc": "2035-04-06T06:01:30Z"}, {"expires_at_utc": "2035-04-06T07:00:00Z"},
    {"issued_at_utc": "2035-04-05T06:00:00Z"}, {"unknown": True},
    {"authorizer": {"agent": "not-the-declared-agent"}}])
def test_new_exact_go_required_and_no_old_p2_authority(bundle, patch):
    result = invoke(bundle, go_patch=patch)
    assert result["status"] == "NOT_READY" and not result["invocation_consumed"]
    assert bundle["backend"].preparations == 0


@pytest.mark.parametrize("group,key,value", [
    ("python", "sha256", "0" * 64), ("python", "root_sha256", "0" * 64),
    ("python", "manifest_sha256", "0" * 64), ("python", "receipt_sha256", "0" * 64),
    ("binding", "source_commit", "0" * 40), ("binding", "source_files", {}),
    ("environment", "HF_HUB_OFFLINE", "0"), ("environment", "PYTHONNOUSERSITE", "0"),
])
def test_scientific_and_runtime_pins_are_closed(bundle, group, key, value):
    config = copy.deepcopy(bundle["config"])
    config[group][key] = value
    result = invoke(bundle, config=config)
    assert result["status"] == "NOT_READY" and not result["invocation_consumed"]


def test_module_rejection_halts_before_any_private_input(bundle):
    bundle["host"].reject_modules = True
    result = invoke(bundle)
    assert result["reason"] == "modules" and not result["actual_data_opened"]
    assert bundle["backend"].preparations == 0


def test_module_observations_preserve_unknown_fileless_provenance(bundle):
    assert invoke(bundle)["status"] == "COMPLETE"
    path = bundle["root"] / "native/prepare/MODULES_BEFORE_NATIVE.json.gz"
    observed = json.loads(gzip.decompress(path.read_bytes()))
    assert observed["rows"][0]["dynamic_provenance"] == "UNKNOWN_NOT_OS_ISOLATION"
    assert observed["DLL_closure"] == "UNKNOWN"


@pytest.mark.parametrize("field", ["snapshot", "runner_cases", "baseline_manifest", "baseline_seal"])
def test_hash_drift_in_private_input_is_fatal_and_consumed(bundle, field):
    path = Path(bundle["config"]["inputs"][field]["path"])
    path.write_bytes(path.read_bytes() + b" ")
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and result["invocation_consumed"] and result["actual_data_opened"]
    assert bundle["backend"].preparations == 0


def test_prepared_manifest_cannot_change_between_prepare_and_prepass(bundle):
    assert invoke(bundle)["status"] == "COMPLETE"
    path = bundle["root"] / "native/PREPARED.json"
    path.write_bytes(path.read_bytes() + b" ")
    result = invoke(bundle, "prepass")
    assert result["status"] == "NOT_READY" and result["reason"] in ("binding", "prior")
    assert bundle["backend"].safe_prepass is None


def test_unknown_commitment_id_is_refused_before_arm_invocation(bundle):
    assert invoke(bundle)["status"] == "COMPLETE"
    assert invoke(bundle, "prepass")["status"] == "COMPLETE"
    Path(bundle["config"]["inputs"]["commitments_path"]).write_bytes(native.canon({"f" * 32: "d" * 64}))
    result = invoke(bundle, "arms")
    assert result["status"] == "NOT_READY" and bundle["backend"].safe_arms is None


def test_empty_safe_prepare_is_explicit_and_does_not_encode(bundle):
    bundle["config"]["inputs"]["runner_cases"] = bundle["save"](Path(bundle["config"]["inputs"]["runner_cases"]["path"]), [])
    result = invoke(bundle)
    assert result["status"] == "COMPLETE" and result["SAFE_cases"] == 0
    assert bundle["backend"].preparations == 0
    phase = json.loads((bundle["root"] / "native/prepare/RESULT.json").read_bytes())
    assert phase["outputs"]["native_prepare_executed"] is False


def test_hash_sink_never_retains_bodies():
    sink = native.HashSink()
    assert sink.write("PRIVATE_SYNTHETIC_BODY") == 22
    assert sink.receipt() == {"sha256": native.sha(b"PRIVATE_SYNTHETIC_BODY"), "bytes": 22, "bodies_retained": False}
    assert "PRIVATE_SYNTHETIC_BODY" not in repr(sink.__dict__)


def test_cli_without_execute_reads_no_files_and_prints_fixed_json(monkeypatch, capsys):
    monkeypatch.setattr(native.Host, "read", lambda *a: pytest.fail("disabled CLI read a file"))
    code = native.main(["--phase", "prepare", "--manifest", "PRIVATE_PATH", "--manifest-sha256", "0" * 64,
                        "--go", "PRIVATE_PATH", "--go-sha256", "0" * 64])
    assert code == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["reason"] == "dormant" and "PRIVATE_PATH" not in output.out


def test_failed_write_leaves_consumed_intent_and_fixed_status(bundle):
    bundle["host"].fail_write = "PREPARED.json"
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and result["reason"] == "native_failed"
    assert (bundle["root"] / "native/prepare/INTENT.json").is_file()
    assert "PRIVATE_SYNTHETIC_EXCEPTION" not in json.dumps(result)


def test_write_readback_drift_is_fatal_not_a_successful_prepared_receipt(bundle):
    bundle["host"].write_drift = True
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and result["reason"] == "write"
    assert not (bundle["root"] / "native/prepare/RESULT.json").exists()


def test_private_path_failure_before_open_does_not_claim_data_opened(bundle):
    bundle["config"]["inputs"]["runner_cases"]["path"] = "C:/outside-the-study/synthetic.json"
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and result["invocation_consumed"] is True
    assert result["actual_data_opened"] is False


def test_nomination_or_score_root_is_never_opened_as_runner_input(bundle):
    score = bundle["root"] / "score"
    score.mkdir()
    private = score / "cases.json"
    private.write_bytes(b'[{"gold_basename":"synthetic_private.md","nonce":"private"}]')
    bundle["config"]["inputs"]["runner_cases"] = {"path": str(private), "sha256": native.sha(private.read_bytes())}
    result = invoke(bundle)
    assert result["reason"] == "path" and result["actual_data_opened"] is False
    assert str(private) not in bundle["host"].reads


def test_stdio_route_failure_refuses_before_private_data_or_native_import(bundle):
    def reject(*args):
        raise native.NotReady("startup")
    bundle["host"].private_stdio = reject
    result = invoke(bundle)
    assert result["reason"] == "startup" and result["invocation_consumed"] is True
    assert result["actual_data_opened"] is False and bundle["backend"].preparations == 0


def test_real_observer_records_actual_synthetic_file_and_unknown_fileless(bundle, monkeypatch):
    config = bundle["config"]
    path = bundle["root"] / "synthetic_module.py"
    path.write_bytes(b"SYNTHETIC_VALUE = 1\n")
    config["source_files"][str(path)] = native.sha(path.read_bytes())
    file_module = types.ModuleType("synthetic_file")
    file_module.__file__ = str(path)
    file_module.__spec__ = importlib.machinery.ModuleSpec("synthetic_file", None, origin=str(path))
    fileless = types.ModuleType("synthetic_fileless")
    builtin = types.ModuleType("synthetic_builtin")
    builtin.__spec__ = importlib.machinery.ModuleSpec("synthetic_builtin", None, origin="built-in")
    modules = {"synthetic_file": file_module, "synthetic_fileless": fileless, "synthetic_builtin": builtin}
    with monkeypatch.context() as patch:
        patch.setattr(native.sys, "modules", modules)
        observed = native.Host().observe(config, {})
    assert observed["membership_count"] == 3 and observed["rejections"] == []
    rows = {row["name"]: row for row in observed["rows"]}
    assert rows["synthetic_file"]["status"] == "ACTUAL_FILE_HASH"
    assert rows["synthetic_file"]["sha256"] == config["source_files"][str(path)]
    assert rows["synthetic_fileless"]["dynamic_provenance"] == "UNKNOWN_NOT_OS_ISOLATION"
    assert rows["synthetic_builtin"]["status"] == "BUILTIN"


def test_real_observer_hash_mismatch_is_not_expected_membership_substitution(bundle, monkeypatch):
    config = bundle["config"]
    path = bundle["root"] / "synthetic_module.py"
    path.write_bytes(b"SYNTHETIC_VALUE = 2\n")
    config["source_files"][str(path)] = "0" * 64
    module = types.ModuleType("synthetic_wrong_hash")
    module.__file__ = str(path)
    module.__spec__ = importlib.machinery.ModuleSpec("synthetic_wrong_hash", None, origin=str(path))
    with monkeypatch.context() as patch:
        patch.setattr(native.sys, "modules", {"synthetic_wrong_hash": module})
        observed = native.Host().observe(config, {})
    assert observed["rejections"] == [{"name": "synthetic_wrong_hash", "status": "MODULE_HASH_MISMATCH"}]
    assert observed["expected_membership_substitution"] is False


@pytest.mark.parametrize("transformed", [False, True])
def test_native_prepare_uses_verified_original_apis_and_refuses_transform_before_encoding(bundle, monkeypatch, transformed):
    """Run only our API glue; all SUT functions are explicitly injected fakes."""
    config = copy.deepcopy(bundle["config"])
    snapshot = json.loads(Path(config["inputs"]["snapshot"]["path"]).read_bytes())
    config["_snapshot"] = snapshot
    calls = []
    config["_observe"] = lambda stage: calls.append(stage)
    provenance = {"file": "synthetic_lesson.md", "source_sha256": snapshot["synthetic_lesson.md"]["sha256"],
                  "used_sha256": snapshot["synthetic_lesson.md"]["sha256"], "mtime_utc": snapshot["synthetic_lesson.md"]["mtime"],
                  "transform": {"kind": "not_allowed"} if transformed else None}
    corpus = SimpleNamespace(parse_utc=native.utc,
        build=lambda *args: calls.append(("corpus", args)),
        read_corpus=lambda path: ({"cutoff_utc": config["corpus_as_of_utc"]}, [{"kind": "lesson", "provenance": provenance}]))
    encode = SimpleNamespace(HF_CACHE=Path(config["cache_root"]), build=lambda *args: calls.append(("encode", args)))
    memory = SimpleNamespace(build=lambda *args: calls.append(("memory", args)) or {"encoder": config["binding"]["encoder"]})
    backend = native.NativeBackend()
    modules = {"corpus": corpus, "encode": encode, "memory": memory}
    monkeypatch.setattr(backend, "source", lambda c, relative, name: modules[name])
    sentinel = object()
    monkeypatch.setattr(backend, "blind", lambda c: sentinel)
    shared = bundle["root"] / "prepared/shared"
    if transformed:
        with pytest.raises(native.NotReady, match="snapshot"):
            backend.prepare(config, shared)
        assert not any(type(call) is tuple and call[0] in ("encode", "memory") for call in calls)
    else:
        assert backend.prepare(config, shared) is sentinel
        assert calls[0] == "POST_IMPORT"
        assert calls[1] == ("corpus", (shared / "lessons", [], native.utc(config["corpus_as_of_utc"]), shared / "corpus"))
        assert calls[2] == ("encode", (shared / "corpus", shared / "vectors"))
        assert calls[3] == ("memory", (shared / "corpus", shared / "vectors", shared / "memory"))
