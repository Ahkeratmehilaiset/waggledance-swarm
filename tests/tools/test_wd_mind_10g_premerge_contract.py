"""Small deterministic M02/M18 contracts; no 10GiB source or live owner."""
import hashlib
import io
import json
import threading
from types import SimpleNamespace
import pytest
from tools import wd_bridge_mind_session as session
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_absent_generation_prefix_readonly as prefix

LIMIT = 10 * 1024**3
OWNER = 3 * LIMIT + 6 * (4 * 1024**2 + 1) + 3
PREWARM = 7 * LIMIT + 4 * 1024**2 + 1

@pytest.mark.parametrize("offset", [LIMIT - 1, LIMIT, LIMIT + 1])
@pytest.mark.parametrize("operation,expected", [
    ("startup", OWNER), ("query", OWNER), ("prewarm", PREWARM),
    ("prewarm_refresh", PREWARM)])
def test_exact_source_boundary_without_source_io(monkeypatch, offset, operation, expected):
    def no_io(*args, **kwargs):
        raise AssertionError("source I/O before metadata preflight")
    snapshot = {"messages": {"end_cursor": SimpleNamespace(offset=offset)}}
    functions = {
        "startup": runtime._startup_owner_source_budget,
        "query": runtime._local_query_source_budget,
        "prewarm": runtime._prewarm_source_budget,
        "prewarm_refresh": lambda snap: runtime._prewarm_source_budget(snap, extra_prefix_passes=2)}
    with monkeypatch.context() as guard:
        guard.setattr("builtins.open", no_io)
        guard.setattr(prefix.os, "open", no_io)
        if offset > LIMIT:
            with pytest.raises(ValueError, match="source_budget"):
                functions[operation](snapshot)
        else:
            result = functions[operation](snapshot)
            assert type(result) is prefix._SourceReadBudget
            assert result._remaining == expected

def test_shared_allowance_hash_and_selected_read():
    class Counting(io.BytesIO):
        def __init__(self, value):
            super().__init__(value)
            self.calls = []
        def read(self, size=-1):
            self.calls.append(size)
            return super().read(size)
    allowance = prefix._SourceReadBudget(6)
    initial = Counting(b"abcd")
    assert prefix._hash_prefix(initial, 4, source_budget=allowance) == hashlib.sha256(b"abcd").hexdigest()
    selected = Counting(b"efg")
    with pytest.raises(ValueError, match="source_budget"):
        session._bounded_source_read(selected, allowance, 3)
    assert selected.calls == []
    assert prefix._budgeted_read(selected, 2, allowance) == b"ef"
    with pytest.raises(ValueError, match="source_budget"):
        session._bounded_source_read(selected, allowance, 1)
    assert initial.calls == [4] and selected.calls == [2]

def test_full_prefix_primitive_sees_unselected_same_size_change():
    source = io.BytesIO(b"old row\nselected row\n")
    size = len(source.getvalue())
    before = prefix._hash_prefix(source, size, source_budget=prefix._SourceReadBudget(size))
    source.seek(0)
    source.write(b"new")
    after = prefix._hash_prefix(source, size, source_budget=prefix._SourceReadBudget(size))
    assert before != after
    # Primitive coverage only: this does not prove actual session wiring.

@pytest.mark.parametrize("offset", [LIMIT - 1, LIMIT, LIMIT + 1])
def test_session_cap_is_distinct_from_total_read_allowance(monkeypatch, offset):
    total = 4 * LIMIT + (64 * 1024**2 + 1) + 1 + 6 * (4 * 1024**2 + 1)
    budget = session._SourceReadBudget(total)
    budget.reserve(64 * 1024**2 + 1)
    before = budget._remaining
    def no_io(*args, **kwargs):
        raise AssertionError("source I/O during session preflight")
    with monkeypatch.context() as guard:
        guard.setattr("builtins.open", no_io)
        if offset > LIMIT:
            with pytest.raises(session._SelectedSourcePrefixBudgetRefused):
                session._preflight_selected_source_prefix(budget, offset)
        else:
            session._preflight_selected_source_prefix(budget, offset)
    assert budget._remaining == before


@pytest.mark.parametrize("extra", [0, 1])
def test_assembly_checks_actual_frozen_offset_before_collecting_rows(tmp_path, monkeypatch, extra):
    source_dir = tmp_path / "runtime" / "shared"
    source_dir.mkdir(parents=True)
    source = source_dir / "events.jsonl"
    payload = b'{"message":"synthetic"}' + b' ' * extra + b'\n'
    source.write_bytes(payload)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    fleet = json.dumps({"schema_version": 2, "runtime_root": str(source_dir.parent)}).encode()
    (bundle / "wd-fleet.json").write_bytes(fleet)
    manifest = json.dumps({"schema_version": 1, "files": {"wd-fleet.json": reader._sha(fleet)}}).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)

    limit = len(payload) - extra
    monkeypatch.setattr(reader, "_EAGER_MEMORY_BUDGET_BYTES", limit * reader._EAGER_MEMORY_BYTES_PER_SOURCE_BYTE)
    monkeypatch.setattr(runtime, "_bundle_inputs", lambda *args: ())
    monkeypatch.setattr(reader, "_load_identifier_policy", lambda *args, **kwargs: {})
    scans = []
    def record_scan(stream, end, tasks):
        scans.append(end)
        raise ValueError("stop_after_preflight")
    monkeypatch.setattr(prefix, "_scan", record_scan)
    with pytest.raises(runtime.FullRuntimeRefused):
        runtime.assemble_full_runtime(enabled=True, bundle_root=str(bundle),
                                      expected_manifest_sha256=reader._sha(manifest),
                                      identifier_policy_bytes=b"{}", expected_identifier_policy_sha256=reader._sha(b"{}"))
    assert scans == ([] if extra else [limit])


def test_complete_projection_rejects_before_source_collection(monkeypatch):
    assert reader._max_eager_source_bytes() == 256 * 1024**2
    context = {"messages": {"end_cursor": SimpleNamespace(offset=reader._max_eager_source_bytes() + 1)}}
    def forbid(*args, **kwargs):
        pytest.fail("projection opened a dependency before its memory preflight")
    monkeypatch.setattr(reader, "_pinned_dependency", forbid)
    with pytest.raises(ValueError, match="source_over_budget"):
        reader._projection(context, complete_records=True)


@pytest.fixture(params=["bound", "wrong_identity"])
def over_eager_prefix(tmp_path, monkeypatch, request):
    # Seek across the unused prefix. The real tail, identity and size checks
    # run on disk, but no test should read or parse the entire prefix.
    from tools import wd_mind_content_snapshot_package as package
    shared = tmp_path / "runtime" / "shared"
    shared.mkdir(parents=True)
    source = shared / "events.jsonl"
    with source.open("wb") as out:
        out.seek(256 * 1024**2 + 1 - 4)
        out.write(b"\n{}\n")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    fleet = json.dumps({"schema_version": 2, "runtime_root": str(shared.parent)}).encode()
    (bundle / "wd-fleet.json").write_bytes(fleet)
    manifest = json.dumps({"schema_version": 1, "files": {"wd-fleet.json": reader._sha(fleet)}}).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    controls = (str(source), str(shared / "events.generation.json"), reader._sha(fleet), b"{}", b"{}")
    monkeypatch.setattr(package, "_consumer_bundle_controls", lambda *_: controls)
    monkeypatch.setattr(runtime, "_bundle_inputs", lambda *_: (fleet, b"{}", b"{}"))
    monkeypatch.setattr(reader, "_load_identifier_policy", lambda *args, **kwargs: {})
    monkeypatch.setattr(runtime, "create_operator_pinned_policy_anchor", lambda **kwargs: None)
    def forbidden(*args, **kwargs):
        pytest.fail("over-limit prefix contents were read")
    monkeypatch.setattr(prefix, "_scan", forbidden)
    bound = request.param == "bound"
    if not bound:
        from dataclasses import replace
        read_tail = prefix.bridge.read_bridge_log_tail_lines
        def wrong_identity(*args, **kwargs):
            return replace(read_tail(*args, **kwargs), file_identity="0" * 64)
        monkeypatch.setattr(prefix.bridge, "read_bridge_log_tail_lines", wrong_identity)
    return str(bundle), reader._sha(manifest), bound


def test_eager_limit_owner_start_diagnostic(over_eager_prefix):
    from tools import wd_mind_runtime_child as child
    bundle, pin, bound = over_eager_prefix
    adapter = child._LocalManifestQuery("codex-lead-1", bundle, pin, b"{}", reader._sha(b"{}"),
        "c"*64, 5, 0, 5, admission=SimpleNamespace(assert_owner=lambda _: None))
    adapter.start()
    try:
        assert adapter.wait(timeout=5)["state"] == "HOLD"
        reason = "SOURCE_BUDGET" if bound else "RUNTIME_BINDING"
        assert adapter.start_diagnostic() == {"reason": reason, "exception_type": "UNKNOWN", "authority": "none"}
    finally:
        adapter.close()


def test_eager_limit_session_stderr(over_eager_prefix, tmp_path, monkeypatch):
    bundle, pin, bound = over_eager_prefix
    policy = tmp_path / "policy"
    policy.write_bytes(b"{}")
    for name in ("_pin_flat_session", "_pin_selected_resolver_code", "_preflight_selected_resolver_code"):
        monkeypatch.setattr(session, name, lambda *_: None)
    args = ["--mind-local-candidate-read", "--policy-path", str(policy), "--policy-sha256", reader._sha(b"{}"),
            "--approval-reference-sha256", "c"*64, "--lane", "codex-lead-1", "--timeout-seconds", "5"]
    output, error = io.BytesIO(), io.BytesIO()
    assert session._local_candidate_entry(args, output, error, bundle, pin) == 2
    assert output.getvalue() == b""
    expected = {"error": "mind_session_content_refused", "authority": "none"}
    if bound:
        expected["reason"] = "SOURCE_BUDGET"
    assert json.loads(error.getvalue()) == expected


def test_eager_limit_refresh_keeps_previous_generation(over_eager_prefix, monkeypatch):
    bundle, pin, bound = over_eager_prefix
    handler = object.__new__(runtime._FlatRequestHandler)
    handler._lock = threading.Lock()
    handler._pending_rotator = handler._refresh_controller = None
    owner = object.__new__(runtime._SingleManifestOwner)
    owner._state, owner._lock = "READY", threading.Lock()
    previous = owner._generation = (object(), {}, handler)
    monkeypatch.setattr(runtime, "_verify_refresh_basis", lambda _: (bundle, pin, b"{}", reader._sha(b"{}")))
    reason = "SOURCE_BUDGET" if bound else "mind_full_runtime_refused"
    with pytest.raises(runtime.FullRuntimeRefused, match=f"^{reason}$"):
        owner._refresh_generation(enabled=True)
    assert owner._state == "HOLD" and owner._generation is previous
