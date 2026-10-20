"""Synthetic supervisor tests. No child, native stage, model, or private input."""
import importlib.util
import copy
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest


SPEC = importlib.util.spec_from_file_location(
    "prospective_supervisor", Path(__file__).with_name("prospective_supervisor.py"))
supervisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(supervisor)


def test_dormant_before_any_host_or_process():
    assert supervisor.launch("unopened", "0" * 64, "unopened", "0" * 64,
                             phase="prepare") == {
        "status": "NOT_READY", "reason": "dormant", "phase": "prepare",
        "invocation_consumed": False, "child_started": False,
        "actual_data_opened": False}


AUDIT = Path("C:/Python/project2/.codex-audit/m1-p3-prospective-20261020/supervisor-author")
api = supervisor.native_api()


class FakeHost(supervisor.Host):
    """No real identity, drive, process, package or model qualification."""
    def __init__(self):
        super().__init__(api)
        self.events, self.reads = [], []
        self.rid, self.elevated = 8192, False
        self.elapsed, self.fail_budget_at = 0, None
        self.fail_write, self.drift_stdio, self.wrong_creation = None, False, False
    def audit_root(self):
        return self.scope
    def identity(self):
        self.events.append("identity")
        return {"pid": 1001, "creation_filetime_100ns": "1234", "integrity_rid": self.rid,
                "elevated": self.elevated, "executable": str(supervisor.PYTHON_PATH),
                "python": {"sha256": api.PYTHON_SHA256}, "ABI_bits": 64, "synthetic_only": True}
    def now(self):
        return dt.datetime(2035, 4, 6, 6, 2, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=self.elapsed)
    def monotonic(self):
        return self.elapsed
    def pause(self):
        self.elapsed += 1
    def read(self, ref):
        self.events.append("read")
        self.reads.append(ref["path"])
        return super().read(ref)
    def preflight(self, c, phase, identity):
        self.events.append("synthetic_preflight")
        root = self.io.path(c["root"])
        assert AUDIT in root.parents
        owner = api.json_bytes(self.read(c["owner"]))
        supervisor.need(owner == {"schema": "wd.m1-p3-study-root.v1", "study_id": c["study_id"],
                                 "protocol_sha256": c["protocol"]["sha256"], "root": c["root"]}, "binding")
        self.budget(root)
    def budget(self, root, extra=0):
        self.events.append("budget")
        if self.fail_budget_at is not None and self.elapsed >= self.fail_budget_at:
            raise supervisor.NotReady("budget")
        return self.account(root, extra)
    def write(self, root, path, body, *, terminal=False):
        self.events.append("write:" + path.name)
        if self.fail_write == path.name:
            raise OSError("PRIVATE_SYNTHETIC_EXCEPTION")
        # Reuse exclusive/fsync/readback writer, replacing only disk qualification.
        previous = self.io.check_budget
        self.io.check_budget = self.budget
        try:
            return super().write(root, path, body, terminal=terminal)
        finally:
            self.io.check_budget = previous
    def exclusive_stdio(self, path):
        self.events.append("stdio:" + path.name)
        return super().exclusive_stdio(path)
    def child_identity(self, child):
        self.events.append("synthetic_child_identity")
        return {"pid": child.pid, "creation_filetime_100ns": "999" if self.wrong_creation else "9876"}
    def stdio_receipt(self, path, original):
        if self.drift_stdio:
            raise supervisor.NotReady("path")
        return super().stdio_receipt(path, original)


class FakeChild:
    def __init__(self, factory, argv, kwargs):
        self.factory, self.argv, self.kwargs = factory, argv, kwargs
        self.pid, self.returncode = 2002, None if factory.linger else factory.code
        self.terminated = self.killed = False
        self.phase = argv[-1]
        root = factory.bundle["root"]
        assert not (root / "native").exists() if self.phase == "prepare" else True
        assert (root / "supervisor" / self.phase / "INTENT.json").exists()
        assert kwargs["stdout"].name != kwargs["stderr"].name
        kwargs["stdout"].write(b"PRIVATE_SYNTHETIC_STDOUT\x00")
        kwargs["stderr"].write(b"PRIVATE_SYNTHETIC_STDERR\xff")
        if factory.publish:
            folder = root / "native" / self.phase
            folder.mkdir(parents=True, exist_ok=False)
            manifest_sha = argv[argv.index("--manifest-sha256") + 1]
            go_sha = argv[argv.index("--go-sha256") + 1]
            result = {"schema": "wd.m1-p3-native-phase.v1", "status": "COMPLETE",
                      "study_id": factory.bundle["config"]["study_id"], "phase": self.phase,
                      "manifest_sha256": manifest_sha, "go_sha256": go_sha,
                      "invocation_consumed": True, "actual_data_opened": True}
            result.update(factory.result_patch)
            folder.joinpath("RESULT.json").write_bytes(api.canon(result))
            folder.joinpath("IDENTITY.json").write_bytes(api.canon({"pid": self.pid,
                "creation_filetime_100ns": "9876", "integrity_rid": 8192, "elevated": False,
                "python": {"sha256": api.PYTHON_SHA256}}))
    def poll(self):
        return self.returncode
    def wait(self, timeout):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("SYNTHETIC_ONLY", timeout)
        return self.returncode
    def terminate(self):
        self.terminated = True
        if not self.factory.resist:
            self.returncode = -15
    def kill(self):
        self.killed, self.returncode = True, -9


class FakePopen:
    def __init__(self, bundle):
        self.bundle, self.calls = bundle, []
        self.code, self.publish, self.linger, self.resist = 0, True, False, False
        self.result_patch = {}
        self.failure = False
        self.after_start = None
    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.failure:
            raise OSError("PRIVATE_SYNTHETIC_POPEN_EXCEPTION")
        self.child = FakeChild(self, argv, kwargs)
        if self.after_start is not None:
            self.after_start(argv)
        return self.child


@pytest.fixture
def bundle():
    AUDIT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=AUDIT) as name:
        audit = Path(name)
        root = audit / "study"
        root.mkdir()
        def save(path, value):
            content = api.canon(value)
            path.write_bytes(content)
            return {"path": str(path), "sha256": api.sha(content)}
        authorizer = {"agent": "synthetic-lead", "agent_uuid": "a" * 32, "session_id": "synthetic-run"}
        study = "1" * 32
        protocol_value = {"schema": "wd.m1-p3-protocol-lock.v1", "status": "LOCKED", "study_id": study,
            "study_root": str(root), "collection_controller": {
                "audit_root": str(audit),
                "control_root": str(audit / "control"), "ledger_root": str(audit / "ledger"),
                "inbox": str(root / "inbox"), "baseline_root": str(root / "baseline"),
                "blind_root": str(root / "blind"), "score_root": str(root / "score")},
            "native_authorizer": authorizer, "native_supervisor": {"path": str(Path(supervisor.__file__).absolute()),
                "sha256": api.fresh(supervisor.__file__)["sha256"]}}
        protocol = save(root / "protocol.json", protocol_value)
        owner = save(root / "OWNER.json", {"schema": "wd.m1-p3-study-root.v1", "study_id": study,
            "protocol_sha256": protocol["sha256"], "root": str(root)})
        source_files = {str(root / "synthetic_source.py"): "f" * 64}
        config = {"schema": "wd.m1-p3-native-manifest.v1", "study_id": study, "protocol": protocol,
            "worker": {"path": str(supervisor.WORKER_PATH), "sha256": supervisor.WORKER_SHA256},
            "root": str(root), "owner": owner, "python": {"executable": str(supervisor.PYTHON_PATH),
                "sha256": api.PYTHON_SHA256, "root": str(root / "python"), "manifest_path": str(root / "python.gz"),
                "manifest_sha256": api.PYTHON_MANIFEST_SHA256, "receipt_path": str(root / "receipt.json"),
                "receipt_sha256": api.PYTHON_RECEIPT_SHA256, "root_sha256": api.PYTHON_ROOT_SHA256},
            "source_root": str(root / "synthetic_source"), "source_files": source_files,
            "package_roots": [str(root / "synthetic_packages")], "cache_root": str(root / "synthetic_cache"),
            "cache_files": {str(root / "synthetic_weights"): "9" * 64},
            "environment": dict(api.ENV, USERPROFILE=str(root), HF_HOME=str(root), SystemRoot="C:/Windows"),
            "binding": {"source_commit": api.MEASURED_COMMIT, "source_files": source_files,
                "encoder": {"revision": api.CACHE_REVISION, "device": "cpu", "torch_threads": 1, "dim": 384,
                    "prefix_passage": "passage: ", "prefix_query": "query: ", "truncate_chars": 2000,
                    "passage_batch": 32, "query_batch": 1, "normalize": True, "dtype": "float32"},
                "rule": {"guard_band_s": 1.0, "bm25_k1": 1.2, "bm25_b": .75}},
            "helpers": {"runner": {"path": str(root / "runner.py"), "sha256": api.RUNNER_SHA256},
                "comparator": {"path": str(root / "comparator.py"), "sha256": api.BM25_SHA256}},
            "stdio_paths": {p: {c: str(root / "private_stdio" / (p + "." + c + ".log"))
                                for c in ("stdout", "stderr")} for p in supervisor.PHASES},
            "inputs": {"runner_cases": {"path": str(root / "unopened-blind.json"), "sha256": "d" * 64},
                       "snapshot": {"path": str(root / "unopened-snapshot.json"), "sha256": "e" * 64},
                       "baseline_manifest": {"path": str(root / "unopened-baseline.json"), "sha256": "f" * 64},
                       "baseline_seal": {"path": str(root / "unopened-seal.json"), "sha256": "7" * 64},
                       "baseline_blobs": {"8" * 64: str(root / "unopened-blob")},
                       "commitments_path": str(root / "unopened-commitments.json")},
            "corpus_as_of_utc": "2035-04-05T05:59:58Z", "collection_start_utc": "2035-04-05T06:00:00Z",
            "collection_end_utc": "2035-04-06T06:00:00Z"}
        data = {"root": root, "config": config, "host": FakeHost(), "save": save,
                "authorizer": authorizer, "protocol_value": protocol_value}
        data["host"].scope = audit
        data["popen"] = FakePopen(data)
        yield data


def invoke(bundle, phase="prepare", *, go_patch=None):
    c, save, root = bundle["config"], bundle["save"], bundle["root"]
    manifest = save(root / "native-manifest.json", c)
    prior = {p: api.sha((root / "native" / p / "RESULT.json").read_bytes())
             for p in supervisor.PHASES[:supervisor.PHASES.index(phase)]}
    go = {"schema": "wd.m1-p3-native-go.v1", "status": "NATIVE_PHASE_GO", "study_id": c["study_id"],
          "protocol_sha256": c["protocol"]["sha256"], "manifest_sha256": manifest["sha256"], "phase": phase,
          "authorizer": bundle["authorizer"], "event_ref": "b" * 64,
          "issued_at_utc": "2035-04-06T06:01:00Z", "expires_at_utc": "2035-04-06T06:25:00Z",
          "prior_receipts": prior, "commitments_sha256": "9" * 64 if phase == "arms" else None}
    go.update(go_patch or {})
    go_ref = save(root / (phase + "-go.json"), go)
    return supervisor.launch(manifest["path"], manifest["sha256"], go_ref["path"], go_ref["sha256"],
                             phase=phase, execute=True, _test_host=bundle["host"], _test_popen=bundle["popen"])


def receipt(bundle, phase="prepare"):
    return json.loads((bundle["root"] / "supervisor" / phase / "RESULT.json").read_bytes())


@pytest.mark.parametrize("rid,elevated", [(12288, True), (12288, False), (8192, True), (None, False), (4096, False)])
def test_high_or_unknown_refuses_before_any_input_stdio_or_process(bundle, rid, elevated):
    host = bundle["host"]
    host.rid, host.elevated = rid, elevated
    result = invoke(bundle)
    assert result["reason"] == "medium" and not result["invocation_consumed"]
    assert host.events == ["identity"] and not host.reads
    assert bundle["popen"].calls == []
    assert not (bundle["root"] / "supervisor").exists()
    assert not (bundle["root"] / "private_stdio").exists()
    assert not (bundle["root"] / "native").exists()


def test_exact_child_contract_and_disk_hash_receipts(bundle, capsys):
    result = invoke(bundle)
    assert result["status"] == "COMPLETE" and result["actual_data_opened"] is True
    host, root = bundle["host"], bundle["root"]
    assert host.events[0] == "identity"
    assert all("unopened" not in p for p in host.reads)
    argv, kwargs = bundle["popen"].calls[0]
    assert argv[:7] == [str(supervisor.PYTHON_PATH), "-I", "-B", "-S", "-X",
                       "pycache_prefix=" + str(root / "native/prepare/pycache"), str(supervisor.WORKER_PATH)]
    assert argv[-2:] == ["--phase", "prepare"] and "--execute" in argv
    assert kwargs["shell"] is False and kwargs["close_fds"] is True
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["cwd"] == str(supervisor.CWD)
    assert kwargs["env"] == dict(bundle["config"]["environment"], TEMP=str(root / "supervisor/prepare"), TMP=str(root / "supervisor/prepare"))
    assert "PATH" not in kwargs["env"] and "PYTHONPATH" not in kwargs["env"]
    out = receipt(bundle)
    assert out["pid"] == 2002 and out["returncode"] == 0 and not out["timed_out"]
    assert out["child_identity"] == {"pid": 2002, "creation_filetime_100ns": "9876"}
    assert out["protocol_sha256"] != out["manifest_sha256"]
    for channel in ("stdout", "stderr"):
        path = Path(bundle["config"]["stdio_paths"]["prepare"][channel])
        assert out["stdio"][channel] == {"sha256": api.sha(path.read_bytes()), "bytes": path.stat().st_size}
    assert "PRIVATE" not in json.dumps(result) and capsys.readouterr().out == ""


def test_three_fresh_phases_no_retry_or_adopt(bundle):
    for phase in supervisor.PHASES:
        assert invoke(bundle, phase)["status"] == "COMPLETE"
    assert len(bundle["popen"].calls) == 3
    assert invoke(bundle, "arms")["reason"] == "consumed"
    assert len(bundle["popen"].calls) == 3


@pytest.mark.parametrize("patch,reason", [({"phase": "arms"}, "go"), ({"manifest_sha256": "0" * 64}, "go"),
    ({"protocol_sha256": "0" * 64}, "go"), ({"status": "OLD_GO"}, "go"),
    ({"issued_at_utc": "2035-04-06T06:03:00Z"}, "go"),
    ({"expires_at_utc": "2035-04-06T06:40:00Z"}, "go"), ({"commitments_sha256": "8" * 64}, "go"),
    ({"prior_receipts": {"prepare": "1" * 64}}, "prior")])
def test_wrong_original_bindings_or_clock_refuse_before_consumption(bundle, patch, reason):
    result = invoke(bundle, go_patch=patch)
    assert result["reason"] == reason and not result["invocation_consumed"]
    assert not bundle["popen"].calls and not (bundle["root"] / "supervisor").exists()


def test_native_directory_must_be_absent_before_prepare(bundle):
    (bundle["root"] / "native").mkdir()
    result = invoke(bundle)
    assert result["reason"] == "consumed" and not result["invocation_consumed"]
    assert not bundle["popen"].calls


@pytest.mark.parametrize("kind", ["outside", "same", "crossphase"])
def test_stdio_containment_and_six_distinct_paths(bundle, kind):
    paths = bundle["config"]["stdio_paths"]
    if kind == "outside":
        paths["prepare"]["stdout"] = str(bundle["root"] / "unsafe.log")
    elif kind == "same":
        paths["prepare"]["stdout"] = paths["prepare"]["stderr"]
    else:
        paths["prepare"]["stdout"] = paths["arms"]["stdout"]
    assert invoke(bundle)["status"] == "NOT_READY"
    assert not bundle["popen"].calls


def test_exclusive_stdio_refuses_existing_without_overwrite(bundle):
    path = Path(bundle["config"]["stdio_paths"]["prepare"]["stdout"])
    path.parent.mkdir()
    path.write_bytes(b"synthetic existing")
    result = invoke(bundle)
    assert result["reason"] == "consumed" and result["invocation_consumed"]
    assert path.read_bytes() == b"synthetic existing" and not bundle["popen"].calls
    assert (bundle["root"] / "supervisor/HALTED.json").exists()


@pytest.mark.parametrize("resist", [False, True])
def test_timeout_stops_only_returned_owned_child_and_is_sticky(bundle, resist):
    popen = bundle["popen"]
    popen.linger, popen.resist, popen.publish = True, resist, False
    result = invoke(bundle, go_patch={"expires_at_utc": "2035-04-06T06:02:03Z"})
    assert result["reason"] == "timeout" and result["actual_data_opened"] == "UNKNOWN"
    assert popen.child.terminated and popen.child.killed is resist
    out = receipt(bundle)
    assert out["timed_out"] and out["returncode"] in (-15, -9)
    assert set(out["stdio"]) == {"stdout", "stderr"}
    assert invoke(bundle)["reason"] == "consumed" and len(popen.calls) == 1


def test_budget_failure_stops_owned_child_and_retains_consumed_intent(bundle):
    bundle["popen"].linger, bundle["popen"].publish = True, False
    bundle["host"].fail_budget_at = 1
    result = invoke(bundle)
    assert result["reason"] in ("budget", "write") and bundle["popen"].child.terminated
    assert (bundle["root"] / "supervisor/prepare/INTENT.json").exists()


def test_popen_exception_sanitized_and_no_claim_of_private_read(bundle, capsys):
    bundle["popen"].failure = True
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and not result["child_started"]
    assert result["actual_data_opened"] is False
    assert "PRIVATE" not in json.dumps(result) and capsys.readouterr().out == ""
    assert receipt(bundle)["returncode"] is None


@pytest.mark.parametrize("kind", ["nonzero", "missing", "binding", "identity", "stdio"])
def test_failed_child_or_unverifiable_receipt_never_completes(bundle, kind):
    if kind == "nonzero":
        bundle["popen"].code = 7
    if kind == "missing":
        bundle["popen"].publish = False
    if kind == "binding":
        bundle["popen"].result_patch = {"go_sha256": "0" * 64}
    if kind == "identity":
        bundle["host"].wrong_creation = True
    if kind == "stdio":
        bundle["host"].drift_stdio = True
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and result["child_started"]
    assert (bundle["root"] / "supervisor/HALTED.json").exists()


@pytest.mark.parametrize("field", ["worker", "python", "environment"])
def test_wrong_fixed_pins_no_child(bundle, field):
    if field == "worker":
        bundle["config"][field]["sha256"] = "0" * 64
    elif field == "python":
        bundle["config"][field]["executable"] = str(bundle["root"] / "not-python.exe")
    else:
        bundle["config"][field]["PYTHONPATH"] = "SYNTHETIC_INJECTION"
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and not bundle["popen"].calls


def test_cli_has_no_fake_host_or_arbitrary_command_options(capsys):
    assert supervisor.main(["--execute", "--phase", "prepare", "--command", "anything"]) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "arguments" and "anything" not in output


def repin_protocol(bundle):
    c = bundle["config"]
    c["protocol"] = bundle["save"](Path(c["protocol"]["path"]), bundle["protocol_value"])
    c["owner"] = bundle["save"](Path(c["owner"]["path"]), {
        "schema": "wd.m1-p3-study-root.v1", "study_id": c["study_id"],
        "protocol_sha256": c["protocol"]["sha256"], "root": c["root"]})


def test_disjoint_control_ledger_bytes_share_one_budget(bundle, monkeypatch):
    control = Path(bundle["protocol_value"]["collection_controller"]["control_root"])
    control.mkdir()
    control.joinpath("synthetic-budget.bin").write_bytes(b"x" * 300000)
    monkeypatch.setattr(api, "BUDGET", 500000)
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and result["reason"] == "budget"
    assert not bundle["popen"].calls


def test_forest_escape_refuses_before_child(bundle):
    bundle["protocol_value"]["collection_controller"]["ledger_root"] = str(bundle["root"] / "nested-ledger")
    repin_protocol(bundle)
    assert invoke(bundle)["reason"] == "path"
    assert not bundle["popen"].calls


@pytest.mark.parametrize("kind", ["study_mismatch", "audit_mismatch", "root_alias", "area_escape", "area_overlap", "missing"])
def test_original_forest_metadata_is_mandatory_and_exact(bundle, kind):
    p = bundle["protocol_value"]
    cfg = p["collection_controller"]
    if kind == "study_mismatch":
        p["study_root"] = str(bundle["root"] / "wrong")
    if kind == "audit_mismatch":
        cfg["audit_root"] = str(bundle["root"])
    if kind == "root_alias":
        cfg["ledger_root"] = cfg["control_root"]
    if kind == "area_escape":
        cfg["inbox"] = str(bundle["host"].scope / "external-inbox")
    if kind == "area_overlap":
        cfg["score_root"] = str(Path(cfg["blind_root"]) / "nested-score")
    if kind == "missing":
        del p["collection_controller"]
    repin_protocol(bundle)
    result = invoke(bundle)
    assert result["reason"] == "path" and not result["invocation_consumed"]
    assert not bundle["popen"].calls


def test_future_forest_roots_absent_count_zero_and_actual_receipt_names_exact_roots(bundle):
    cfg = bundle["protocol_value"]["collection_controller"]
    assert not Path(cfg["control_root"]).exists() and not Path(cfg["ledger_root"]).exists()
    assert invoke(bundle)["status"] == "COMPLETE"
    budget = receipt(bundle)["budget"]
    assert budget["roots"] == [str(bundle["root"]), cfg["control_root"], cfg["ledger_root"]]
    assert budget["limit_bytes"] == 64 * 1024**2
    assert budget["free_floor_bytes"] == 3 * 1024**3
    assert budget["peak_accounted_bytes"] >= budget["last_accounted_bytes"] > 0


@pytest.mark.parametrize("kind", ["missing_pin", "wrong_pin", "wrong_path", "old_protocol"])
def test_original_supervisor_source_and_protocol_pin_required(bundle, kind):
    p = bundle["protocol_value"]
    if kind == "missing_pin":
        del p["native_supervisor"]
    if kind == "wrong_pin":
        p["native_supervisor"]["sha256"] = "0" * 64
    if kind == "wrong_path":
        p["native_supervisor"]["path"] = str(bundle["root"] / "other.py")
    if kind == "old_protocol":
        p["schema"] = "wd.m1-p2-protocol-lock.v1"
    repin_protocol(bundle)
    assert invoke(bundle)["status"] == "NOT_READY"
    assert not bundle["popen"].calls


def test_altered_source_loader_refuses_before_host_identity(bundle, monkeypatch):
    altered = bundle["root"] / "altered-worker.py"
    altered.write_bytes(b"raise RuntimeError('PRIVATE_SYNTHETIC_SOURCE')")
    monkeypatch.setattr(supervisor, "WORKER_PATH", altered)
    result = invoke(bundle)
    assert result["reason"] == "pins" and bundle["host"].events == []
    assert not bundle["popen"].calls


def test_prior_supervisor_receipt_binding_required(bundle):
    assert invoke(bundle)["status"] == "COMPLETE"
    path = bundle["root"] / "supervisor/prepare/RESULT.json"
    old = json.loads(path.read_bytes())
    old["worker_result_sha256"] = "0" * 64
    path.write_bytes(api.canon(old))
    result = invoke(bundle, "prepass")
    assert result["reason"] == "prior" and not result["invocation_consumed"]
    assert len(bundle["popen"].calls) == 1


def test_no_process_after_intent_write_failure_and_no_retry(bundle):
    bundle["host"].fail_write = "INTENT.json"
    result = invoke(bundle)
    assert result["invocation_consumed"] and not result["child_started"]
    assert not bundle["popen"].calls
    assert (bundle["root"] / "supervisor/prepare").is_dir()
    bundle["host"].fail_write = None
    assert invoke(bundle)["reason"] == "consumed"


def test_real_host_private_stdio_open_is_exclusive_regular_disk_file(bundle):
    # This exercises only file creation in this synthetic audit fixture, never
    # token/process APIs or actual worker/SUT data.
    path = bundle["root"] / "private_stdio/synthetic.log"
    path.parent.mkdir()
    with supervisor.Host(api).exclusive_stdio(path) as stream:
        assert os.fstat(stream.fileno()).st_nlink == 1
        stream.write(b"synthetic file bytes")
    with pytest.raises(FileExistsError):
        supervisor.Host(api).exclusive_stdio(path)


def test_original_metadata_rechecked_after_child_not_adopted(bundle):
    def corrupt(argv):
        Path(argv[argv.index("--go") + 1]).write_bytes(b"synthetic changed metadata")
    bundle["popen"].after_start = corrupt
    result = invoke(bundle)
    assert result["reason"] == "binding" and result["actual_data_opened"] is True
    assert (bundle["root"] / "supervisor/HALTED.json").exists()


def test_shared_ledger_growth_during_child_counts_and_stops_owned_child(bundle, monkeypatch):
    monkeypatch.setattr(api, "BUDGET", 500000)
    bundle["popen"].linger, bundle["popen"].publish = True, False
    host = bundle["host"]
    def grow():
        host.elapsed += 1
        ledger = Path(bundle["protocol_value"]["collection_controller"]["ledger_root"])
        ledger.mkdir(exist_ok=True)
        ledger.joinpath("synthetic-growing.bin").write_bytes(b"x" * 300000)
    host.pause = grow
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and result["reason"] == "budget"
    assert bundle["popen"].child.terminated


def test_shared_control_growth_after_child_prevents_complete(bundle, monkeypatch):
    monkeypatch.setattr(api, "BUDGET", 500000)
    def grow(argv):
        control = Path(bundle["protocol_value"]["collection_controller"]["control_root"])
        control.mkdir()
        control.joinpath("synthetic-growth.bin").write_bytes(b"x" * 300000)
    bundle["popen"].after_start = grow
    result = invoke(bundle)
    assert result["status"] == "NOT_READY" and result["reason"] == "budget"
    assert bundle["popen"].child.poll() == 0
