"""Tests-first CLI contract candidate; NOT a live Mind or Windows peer witness.

Proposed interface: no arguments means OFF without environment/config/stdin reads;
--enabled accepts one bounded JSON query from stdin. The existing
WD_Q4_SHARED_DESCRIPTOR_PATH/SHA256 pair selects the descriptor. Its bundle_root
contains deployment-manifest.json, externally pinned by
WD_REBOOT_EXPECTED_MANIFEST_HASH, and wd-fleet.json. Fleet bytes must match BOTH
the descriptor and the anchored manifest. No CALLER aliases substitute for this.
Identity comes from
AGENT_BRIDGE_AGENT, AGENT_BRIDGE_AGENT_UUID, AGENT_BRIDGE_RUN_ID and an in-process
hash of AGENT_BRIDGE_OWNER_TOKEN. These are cooperative declarations, not auth.

Only the adapter is replaced by a recorder in these unit tests. Its synthetic
return cannot establish a working native peer, cleanup or an installed route.
Source A43589FD stays unchanged until Lead preserves this revision's RED run.
"""
import builtins
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "tools/wd_q4_lane_caller_cli.py"
LANE = "codex-tools-1"
UUID = "00000009-0000-4000-8000-000000000000"
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
UUIDS = {lane: f"0000000{index + 1}-0000-4000-8000-000000000000" for index, lane in enumerate(LANES)}
UUIDS[LANE] = UUID
QUERY_CANARY = "synthetic-query-canary-never-an-answer"
TOKEN_CANARY = "synthetic-owner-token-never-output"
REFUSAL = '{"error":"lane_caller_refused","authority":"none"}\n'
LIMIT = 65536
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
CARD = "status done codex-lead-1 pr:7 head:unknown next:none until:"
MANIFEST_LIMIT = 1024 * 1024


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_cli():
    # Deliberately loads the actual candidate; never importorskip.
    spec = importlib.util.spec_from_file_location("synthetic_q4_cli_under_test", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoRead:
    @property
    def buffer(self):
        return self

    def read(self, *args, **kwargs):
        raise AssertionError("OFF read stdin")


def forbidden(*args, **kwargs):
    raise AssertionError("unexpected OFF IO")


class NoEnvironment(dict):
    get = __getitem__ = __iter__ = forbidden


def test_off_reads_no_environment_config_stdin_or_chain(monkeypatch, capsys):
    cli = load_cli()
    # Lexical guards restore before pytest formats any failed assertion.
    with monkeypatch.context() as guard:
        guard.setattr(os, "environ", NoEnvironment())
        guard.setattr(os, "getcwd", forbidden)
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(sys, "stdin", NoRead())
        code = cli.main([])
    observed = capsys.readouterr()
    assert code == 0
    assert observed.out == '{"status":"OFF"}\n'
    assert observed.err == ""


@pytest.fixture
def case(tmp_path, monkeypatch):
    files = {}

    def put(name, raw):
        path = tmp_path / name
        path.write_bytes(raw)
        files[name] = path
        return str(path)

    worktrees = {lane: tmp_path / "lanes" / lane for lane in LANES}
    for directory in worktrees.values():
        directory.mkdir(parents=True)
    worktree = worktrees[LANE]
    monkeypatch.chdir(worktree)
    members = {lane: {"agent": lane, "agent_uuid": UUIDS[lane], "worktree": str(worktrees[lane])}
               for lane in LANES}
    runtime_root = tmp_path / "synthetic-bridge-runtime"
    for lane in LANES:
        (runtime_root / "shared" / ".codex-audit" / lane / "q4-caller-state").mkdir(parents=True)
    fleet = canon({"runtime_root": str(runtime_root),
                   "lanes": [members[lane] for lane in LANES if lane != LANE],
                   "tools_supervisor": members[LANE]})
    put("wd-fleet.json", fleet)
    manifest = {"schema_version": 1, "source_commit": "a" * 40,
                "files": {"wd-fleet.json": sha(fleet)}}
    put("deployment-manifest.json", canon(manifest))
    binding = b'{"schema":"wd.q4-lane-binding.v1","synthetic":true}'
    fixed_plan = b'{"backend":"public-snapshot-v1","synthetic":true}'
    snapshot = b"synthetic-fixed-bundle"
    runtime = b"synthetic-runtime-manifest"
    interpreter = b"synthetic-interpreter-never-executed"
    binding_path = put("binding.json", binding)
    fixed_path = put("fixed.json", fixed_plan)
    snapshot_path = put("snapshot.bin", snapshot)
    runtime_path = put("runtime.json", runtime)
    interpreter_path = put("interpreter.bin", interpreter)
    public_plan = put("public-plan.json", b'{"synthetic":true}')
    expected_binding = {key: "2" * 64 for key in (
        "pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
        "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256")}
    descriptor = {
        "schema": "wd.q4-shared-session-config.v2", "fleet_sha256": sha(fleet),
        "lanes": list(LANES), "bundle_root": str(tmp_path), "public_plan": public_plan,
        "interpreter": interpreter_path, "cwd": str(worktree),
        "binding_path": binding_path, "fixed_plan_path": fixed_path,
        "snapshot_path": snapshot_path, "runtime_manifest_path": runtime_path,
        "interpreter_sha256": sha(interpreter), "binding_sha256": sha(binding),
        "fixed_plan_sha256": sha(fixed_plan), "snapshot_sha256": sha(snapshot),
        "runtime_manifest_sha256": sha(runtime), "interpreter_max_bytes": LIMIT,
        "timeout_seconds": 60, "max_output_bytes": LIMIT,
        "outer": {}, "expected_binding": expected_binding,
    }
    put("descriptor.json", canon(descriptor))
    environment = {
        "AGENT_BRIDGE_AGENT": LANE, "AGENT_BRIDGE_AGENT_UUID": UUID,
        "AGENT_BRIDGE_RUN_ID": "synthetic-cli-run", "AGENT_BRIDGE_OWNER_TOKEN": TOKEN_CANARY,
        "WD_Q4_SHARED_DESCRIPTOR_PATH": str(files["descriptor.json"]),
        "WD_Q4_SHARED_DESCRIPTOR_SHA256": sha(files["descriptor.json"].read_bytes()),
        "WD_REBOOT_EXPECTED_MANIFEST_HASH": sha(files["deployment-manifest.json"].read_bytes()),
    }
    for key in ("WD_Q4_CALLER_DESCRIPTOR_PATH", "WD_Q4_CALLER_DESCRIPTOR_SHA256",
                "WD_Q4_CALLER_FLEET_PATH", "WD_Q4_CALLER_FLEET_SHA256"):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    # Use the real adapter exception types even though the call is synthetic.
    spec = importlib.util.spec_from_file_location(
        "synthetic_real_caller_exceptions", ROOT / "tools/wd_q4_lane_caller_adapter.py")
    real_adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real_adapter)
    adapter = types.ModuleType("tools.wd_q4_lane_caller_adapter")
    adapter.CallerCleanupUnknown = real_adapter.CallerCleanupUnknown
    adapter.CallerRefused = real_adapter.CallerRefused
    calls = []
    outcome = {}

    def record(**kwargs):
        calls.append(kwargs)
        # The recorder tests CLI formatting only. Synthetic owner transitions
        # exercise guard sequencing; they do not prove real native cleanup.
        item, context = kwargs["_owner_guard"], kwargs["_owner_context"]
        assert item._accept_owner_context(context) is context
        if "exception" in outcome:
            raise outcome["exception"]
        selected_lane = json.loads(kwargs["identity_bytes"])["agent"]
        result = {"schema": "wd.q4-lane-caller-result.v1", "status": "REFUSED",
                  "lane": selected_lane, "lane_binding": "DECLARED_CONSISTENT",
                  "lane_authentication": "UNKNOWN", "cleanup": "chain_confirmed",
                  "inner_status": "session_refused", "result_count": 0,
                  "answer": None, "answer_sha256": None,
                  "identity_sha256": sha(kwargs["identity_bytes"]),
                  "descriptor_sha256": kwargs["expected_descriptor_sha256"],
                  "call_id": "1" * 32}
        if outcome.get("validated"):
            for name in ("adapter", "attestation", "transport"):
                ticket = context._ticket(name)
                assert context._accept_ticket(name, ticket) is ticket
                ticket._begin()
                ticket._complete()
            items = [] if "stored_text" not in outcome else [{
                "ref_sha256": sha(b"synthetic-public-ref"),
                "doc_id_sha256": sha(b"synthetic-public-doc"), "stored_text": outcome["stored_text"]}]
            inner_status = "ANSWER" if items else "ABSTAIN"
            answer = {"schema": "wd.q4-lane-answer.v1", "lane": selected_lane,
                      "status": inner_status, "reason": "", "result_count": len(items), "results": items,
                      "binding": dict(expected_binding), "trust": {
                          "lane_authentication": "UNKNOWN", "freshness": "UNKNOWN",
                          "publisher_authentication": "UNKNOWN", "projection_public_attestation": "UNKNOWN",
                          "transport": "caller_injected", "projection": "caller_pinned"}}
            result.update(status="VALIDATED", inner_status=inner_status, answer=answer, result_count=len(items),
                          answer_sha256=sha(canon(answer)))
        result.update(outcome.get("result", {}))
        return result

    adapter.call_lane_session = record
    monkeypatch.setitem(sys.modules, adapter.__name__, adapter)
    import tools
    monkeypatch.setattr(tools, "wd_q4_lane_caller_adapter", adapter, raising=False)
    query = canon({"situation": QUERY_CANARY, "time": "2026-01-01T00:00:00Z"})
    values = {"files": files, "descriptor": descriptor, "manifest": manifest, "env": environment,
            "calls": calls, "outcome": outcome, "query": query,
            "worktree": worktree, "worktrees": worktrees, "cli": load_cli(), "adapter": adapter,
            "real_adapter": real_adapter}
    values["latest"] = prepare_synthetic_latest(values, monkeypatch)
    # Synthetic local clock seam: start observation, then end observation.
    clock = {"times": [], "default": NOW}

    def utc_now():
        return clock["times"].pop(0) if clock["times"] else clock["default"]

    monkeypatch.setattr(values["cli"], "_utc_now", utc_now, raising=False)
    values["clock"] = clock
    return values


def invoke(case, monkeypatch, raw=None, argv=None):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(
        case["query"] if raw is None else raw), encoding="utf-8"))
    return case["cli"].main(["--enabled"] if argv is None else argv)


def prepare_synthetic_latest(case, monkeypatch):
    root = case["files"]["descriptor.json"].parent / "synthetic-index"
    root.mkdir()
    pointer = root / "q4-latest.json"
    pointer.write_bytes(b"synthetic-index-generation-one")
    binding = case["descriptor"]["expected_binding"]
    binding["pointer_sha256"] = sha(pointer.read_bytes())
    plan = {"schema": "wd.q4-public-wrapper-plan.v1", "config": {"descriptor_sha256": binding["descriptor_sha256"]},
            "root": str(root), "policy_path": str(root / "policy"), "roster_path": str(root / "roster"),
            "public_policy_path": str(root / "public-policy"),
            **{key: binding[key] for key in ("pointer_sha256", "source_anchor_sha256", "encoder_sha256",
                                              "policy_sha256", "roster_sha256", "public_policy_sha256")}}
    plan_raw = canon(plan)
    case["files"]["public-plan.json"].write_bytes(plan_raw)
    binding_raw = canon({"schema": "wd.q4-lane-binding.v1", "public_plan_sha256": sha(plan_raw), "binding": binding})
    case["files"]["binding.json"].write_bytes(binding_raw)
    case["descriptor"]["binding_sha256"] = sha(binding_raw)
    descriptor_raw = canon(case["descriptor"])
    case["files"]["descriptor.json"].write_bytes(descriptor_raw)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", sha(descriptor_raw))
    return pointer


def test_stale_snapshot_refuses_before_query_and_adapter(case, monkeypatch, capsys):
    pointer = case["latest"]
    case["outcome"]["validated"] = True
    pointer.write_bytes(b"synthetic-index-generation-two")
    assert invoke(case, monkeypatch) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == REFUSAL
    assert case["calls"] == []


def test_live_mode_refuses_if_no_live_publication_before_query(case, monkeypatch, capsys):
    """Opt-in dynamic mode must not silently fall back to an old static descriptor."""
    monkeypatch.setenv("WD_Q4_LIVE_MODE", "1")
    case["outcome"]["validated"] = True
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def _stage_card_plan(case):
    """Synthetic staged card binding and v3 plan (card decision and policy pins)."""
    test_root = case['files']['descriptor.json'].parent
    binding = case['descriptor']['expected_binding']
    binding.update(card_decision_sha256='9' * 64, card_policy_sha256='8' * 64)
    plan = json.loads(case['files']['public-plan.json'].read_bytes())
    plan.update(schema='wd.q4-public-wrapper-plan.v3', card_decision_path=str(test_root / 'decision'),
                card_decision_sha256=binding['card_decision_sha256'],
                card_policy_path=str(test_root / 'card-policy'), card_policy_sha256=binding['card_policy_sha256'],
                pointer_path=str(case['latest']), source_path=str(test_root / 'source'),
                generation_path=str(test_root / 'generation'),
                end_cursor={'offset': 1, 'file_identity': 'synthetic-file-id', 'generation': None})
    plan_raw = canon(plan)
    case['files']['public-plan.json'].write_bytes(plan_raw)
    binding_raw = canon({'schema': 'wd.q4-lane-binding.v1', 'public_plan_sha256': sha(plan_raw),
                         'binding': binding})
    case['files']['binding.json'].write_bytes(binding_raw)
    case['descriptor']['binding_sha256'] = sha(binding_raw)


def _direct_card_route(case, monkeypatch):
    """The staged card binding on the direct (non-live) descriptor route."""
    _stage_card_plan(case)
    raw = canon(case['descriptor'])
    case['files']['descriptor.json'].write_bytes(raw)
    monkeypatch.setenv('WD_Q4_SHARED_DESCRIPTOR_SHA256', sha(raw))


def _pending_baseline(case, monkeypatch, *, staged=True):
    """Synthetic baseline laid out like the live selector's generation root."""
    from tools import wd_q4_live_snapshot_publish as live
    test_root = case['files']['descriptor.json'].parent
    if staged:
        _stage_card_plan(case)
    root = test_root.parent / (test_root.name + '-synthetic-generations')
    generation = root / ('a' * 40)
    generation.mkdir(parents=True)
    for key, name in live._BASELINE_GENERATION_PATHS:
        path = generation / name
        path.write_bytes(Path(case['descriptor'][key]).read_bytes())
        case['descriptor'][key] = str(path)
    path = generation / 'shared-session-config.v2.json'
    raw = canon(case['descriptor'])
    path.write_bytes(raw)
    monkeypatch.setenv('WD_Q4_SHARED_DESCRIPTOR_PATH', str(path))
    monkeypatch.setenv('WD_Q4_SHARED_DESCRIPTOR_SHA256', sha(raw))
    monkeypatch.setenv('WD_Q4_LIVE_MODE', '1')
    monkeypatch.setenv('WD_Q4_LIVE_P1', '1')
    attempt = 'b' * 32
    marker = root / live._PUBLICATION_PENDING
    marker.write_bytes(canon({'schema': 'wd.q4-live-publication-pending.v1', 'attempt': attempt,
        'baseline_sha256': sha(raw), 'previous_selector_sha256': None,
        'publication_sha256': 'c' * 64, 'temporary_name': '.q4-live-current-' + attempt + '.tmp'}))
    temp = root / ('.q4-live-current-' + attempt + '.tmp')
    temp.write_bytes(b'synthetic-interrupted-temp')
    return root, marker, temp


def test_interrupted_p1_returns_labelled_baseline_without_touching_publication(case, monkeypatch, capsys):
    root, marker, temp = _pending_baseline(case, monkeypatch)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    case['outcome']['validated'] = True
    case['outcome']['stored_text'] = CARD + '2026-01-01T00:01Z'
    assert invoke(case, monkeypatch) == 0
    actual = json.loads(capsys.readouterr().out)
    assert actual['schema'] == 'wd.q4-lane-interrupted-envelope.v1'
    assert (actual['selection'], actual['publication_state'], actual['freshness']) == (
        'BASELINE_FALLBACK', 'INTERRUPTED', 'STALE')
    assert actual['result']['schema'] == 'wd.q4-lane-content-withheld.v1'
    assert actual['result']['status'] == 'CONTENT_WITHHELD'
    assert actual['result']['original_inner_status'] == 'ANSWER'
    assert actual['result']['original_result_count'] == 1
    assert 'stored_text' not in json.dumps(actual)
    assert actual.get('answer') is None  # Existing v1 consumers cannot silently mistake the envelope for v1.
    assert marker.exists() and temp.exists()
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_pending_marker_removed_during_recall_refuses_output(case, monkeypatch, capsys):
    _, marker, _ = _pending_baseline(case, monkeypatch)
    case['outcome']['validated'] = True
    original = case['adapter'].call_lane_session
    def finish_then_remove(**kwargs):
        value = original(**kwargs)
        marker.unlink()
        return value
    monkeypatch.setattr(case['adapter'], 'call_lane_session', finish_then_remove)
    assert_refused(invoke(case, monkeypatch), capsys)


def test_pending_marker_replaced_with_identical_bytes_during_recall_refuses(case, monkeypatch, capsys):
    root, marker, _ = _pending_baseline(case, monkeypatch)
    replacement = root / 'unrelated-synthetic-marker'
    replacement.write_bytes(marker.read_bytes())
    case['outcome']['validated'] = True
    original = case['adapter'].call_lane_session
    def finish_then_replace(**kwargs):
        value = original(**kwargs)
        os.replace(replacement, marker)
        return value
    monkeypatch.setattr(case['adapter'], 'call_lane_session', finish_then_replace)
    assert_refused(invoke(case, monkeypatch), capsys)


def test_damaged_baseline_refuses_even_with_pending_marker(case, monkeypatch, capsys):
    _pending_baseline(case, monkeypatch)
    Path(case['descriptor']['snapshot_path']).write_bytes(b'damaged')
    case['outcome']['validated'] = True
    assert_refused(invoke(case, monkeypatch), capsys)
    assert not case['calls']


def test_live_without_pending_refuses_malformed_selector(case, monkeypatch, capsys):
    root, marker, _ = _pending_baseline(case, monkeypatch)
    marker.unlink()
    (root / 'q4-live-current.json').write_bytes(b'not-json')
    case['outcome']['validated'] = True
    assert_refused(invoke(case, monkeypatch), capsys)
    assert not case['calls']


def test_pending_with_unrelated_malformed_selector_refuses(case, monkeypatch, capsys):
    root, _, _ = _pending_baseline(case, monkeypatch)
    (root / 'q4-live-current.json').write_bytes(b'not-json')
    case['outcome']['validated'] = True
    assert_refused(invoke(case, monkeypatch), capsys)
    assert not case['calls']


def test_pending_with_missing_selector_candidate_refuses(case, monkeypatch, capsys):
    from tools import wd_q4_live_snapshot_publish as live
    root, marker, _ = _pending_baseline(case, monkeypatch)
    pending = json.loads(marker.read_bytes())
    selection = {'schema': live._SCHEMA, 'baseline_sha256': pending['baseline_sha256'],
                 'generation': 'd' * 40, 'descriptor_sha256': 'e' * 64,
                 'index_pointer_sha256': 'f' * 64}
    raw = canon(selection)
    pending['publication_sha256'] = sha(raw)
    marker.write_bytes(canon(pending))
    (root / live._POINTER).write_bytes(raw)
    case['outcome']['validated'] = True
    assert_refused(invoke(case, monkeypatch), capsys)
    assert not case['calls']


@pytest.mark.parametrize('fault', ['empty', 'noncanonical', 'schema', 'baseline', 'attempt', 'temporary',
                                   'same-selector-hashes', 'extra-field', 'missing-field'])
def test_untrusted_or_incomplete_pending_marker_refuses_before_query(case, monkeypatch, capsys, fault):
    root, marker, _ = _pending_baseline(case, monkeypatch)
    pending = json.loads(marker.read_bytes())
    if fault == 'empty':
        marker.write_bytes(b'')
    elif fault == 'noncanonical':
        marker.write_bytes(json.dumps(pending).encode())
    else:
        if fault == 'schema': pending['schema'] = 'other'
        elif fault == 'baseline': pending['baseline_sha256'] = 'e' * 64
        elif fault == 'attempt': pending['attempt'] = 'A' * 32
        elif fault == 'temporary': pending['temporary_name'] = '../not-ours'
        elif fault == 'same-selector-hashes': pending['previous_selector_sha256'] = pending['publication_sha256']
        elif fault == 'extra-field': pending['extra'] = True
        elif fault == 'missing-field': pending.pop('attempt')
        marker.write_bytes(canon(pending))
    case['outcome']['validated'] = True
    assert_refused(invoke(case, monkeypatch), capsys)
    assert not case['calls']


def test_pending_marker_does_not_enable_fallback_without_p1_opt_in(case, monkeypatch, capsys):
    _pending_baseline(case, monkeypatch)
    monkeypatch.delenv('WD_Q4_LIVE_P1')
    case['outcome']['validated'] = True
    assert_refused(invoke(case, monkeypatch), capsys)
    assert not case['calls']


def test_p1_pending_does_not_make_legacy_baseline_valid(case, monkeypatch, capsys):
    _pending_baseline(case, monkeypatch, staged=False)
    case['outcome']['validated'] = True
    assert_refused(invoke(case, monkeypatch), capsys)
    assert not case['calls']


@pytest.mark.parametrize('p1,expected', [('',None), ('1',True)])
def test_live_selector_receives_only_explicit_p1_opt_in(case, monkeypatch, capsys, p1, expected):
    from tools import wd_q4_live_snapshot_publish as live
    seen=[]
    def stop_before_query(**kwargs):
        seen.append(kwargs)
        raise ValueError('synthetic selector stop')
    monkeypatch.setattr(live, 'select', stop_before_query)
    monkeypatch.setenv('WD_Q4_LIVE_MODE','1')
    monkeypatch.setenv('WD_Q4_LIVE_P1',p1)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(seen)==1 and seen[0].get('p1_enabled') is expected
    assert not case['calls']


@pytest.mark.parametrize('p1,live_mode',[('bad','1'),('1','')])
def test_invalid_p1_environment_refuses_before_selection_and_query(case, monkeypatch, capsys, p1, live_mode):
    from tools import wd_q4_live_snapshot_publish as live
    seen=[]
    monkeypatch.setattr(live,'select',lambda **kwargs: seen.append(kwargs))
    monkeypatch.setenv('WD_Q4_LIVE_MODE',live_mode)
    monkeypatch.setenv('WD_Q4_LIVE_P1',p1)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert not seen and not case['calls']


def test_p1_coverage_combination_refuses_before_selection_and_query(case, monkeypatch, capsys):
    from tools import wd_q4_live_snapshot_publish as live
    seen=[]
    monkeypatch.setattr(live,'select',lambda **kwargs: seen.append(kwargs))
    monkeypatch.setenv('WD_Q4_LIVE_MODE','1')
    monkeypatch.setenv('WD_Q4_LIVE_P1','1')
    monkeypatch.setenv('WD_Q4_WITH_COVERAGE','1')
    assert_refused(invoke(case, monkeypatch), capsys)
    assert not seen and not case['calls']


@pytest.mark.parametrize('coverage,live', [('1', ''), ('bad', '1')])
def test_coverage_envelope_requires_valid_explicit_live_mode(case, monkeypatch, capsys, coverage, live):
    monkeypatch.setenv('WD_Q4_WITH_COVERAGE', coverage)
    monkeypatch.setenv('WD_Q4_LIVE_MODE', live)
    case['outcome']['validated'] = True
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case['calls'] == []


def test_snapshot_changed_during_query_is_never_published(case, monkeypatch, capsys):
    pointer = case["latest"]
    case["outcome"]["validated"] = True
    original = case["adapter"].call_lane_session
    def update_pointer(**kwargs):
        result = original(**kwargs)
        pointer.write_bytes(b"synthetic-index-generation-two")
        return result
    monkeypatch.setattr(case["adapter"], "call_lane_session", update_pointer)
    assert invoke(case, monkeypatch) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == REFUSAL
    assert len(case["calls"]) == 1
    state = case["files"]["descriptor.json"].parent / "synthetic-bridge-runtime" / "shared" / ".codex-audit" / LANE / "q4-caller-state"
    assert not (state / (LANE + ".pending.json")).exists()


def test_missing_current_pointer_refuses_before_query(case, monkeypatch, capsys):
    case["latest"].unlink()
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_unpinned_public_plan_refuses_before_query(case, monkeypatch, capsys):
    case["files"]["public-plan.json"].write_bytes(b'{}')
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def assert_refused(code, capsys, expected_code=2):
    observed = capsys.readouterr()
    assert code == expected_code
    assert observed.out == ""
    assert observed.err == REFUSAL
    assert QUERY_CANARY not in observed.out + observed.err
    assert TOKEN_CANARY not in observed.out + observed.err


@pytest.mark.parametrize("key", [
    "AGENT_BRIDGE_AGENT", "AGENT_BRIDGE_AGENT_UUID", "AGENT_BRIDGE_RUN_ID",
    "AGENT_BRIDGE_OWNER_TOKEN", "WD_Q4_SHARED_DESCRIPTOR_PATH",
    "WD_Q4_SHARED_DESCRIPTOR_SHA256", "WD_REBOOT_EXPECTED_MANIFEST_HASH",
])
def test_missing_identity_or_pin_refuses_before_adapter(case, monkeypatch, capsys, key):
    monkeypatch.delenv(key)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("key,value", [
    ("AGENT_BRIDGE_AGENT", "fable-5"),
    ("AGENT_BRIDGE_AGENT_UUID", "00000008-0000-4000-8000-000000000000"),
    ("WD_Q4_SHARED_DESCRIPTOR_SHA256", "0" * 64),
    ("WD_REBOOT_EXPECTED_MANIFEST_HASH", "0" * 64),
])
def test_wrong_declared_identity_or_pin_refuses_before_adapter(case, monkeypatch, capsys, key, value):
    monkeypatch.setenv(key, value)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_actual_cwd_must_match_pinned_fleet_worktree(case, monkeypatch, capsys, tmp_path):
    other = tmp_path / "other-lane"
    other.mkdir()
    monkeypatch.chdir(other)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("name", ["binding.json", "fixed.json", "snapshot.bin", "runtime.json", "interpreter.bin"])
def test_actual_input_bytes_must_match_descriptor_pins(case, monkeypatch, capsys, name):
    case["files"][name].write_bytes(b"tampered-synthetic-input")
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("raw", [
    b"", b"\xff", b"x" * (LIMIT + 1), b"[]", b'{"situation":"x"}',
    b'{"situation":"x","time":"2026-01-01T00:00:00Z","extra":1}',
    b'{"situation":"x","situation":"y","time":"2026-01-01T00:00:00Z"}',
], ids=["empty", "invalid-utf8", "oversized", "array", "missing-time", "extra-field", "duplicate-field"])
def test_unknown_or_unbounded_stdin_refuses_before_adapter(case, monkeypatch, capsys, raw):
    assert_refused(invoke(case, monkeypatch, raw), capsys)
    assert case["calls"] == []


def test_query_is_not_accepted_from_argv(case, monkeypatch, capsys):
    assert_refused(invoke(case, monkeypatch, argv=["--enabled", "--query", QUERY_CANARY]), capsys)
    assert case["calls"] == []


def test_delegation_once_builds_only_cooperative_in_process_identity(case, monkeypatch, capsys):
    # A synthetic refusal is enough to inspect the call without inventing native success.
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) == 1
    call = case["calls"][0]
    identity = json.loads(call["identity_bytes"])
    assert identity["agent"] == LANE and identity["agent_uuid"] == UUID
    assert identity["caller_pid"] == os.getpid()
    assert identity["cwd"] == str(Path.cwd())
    assert identity["worktree"] == str(case["worktree"])
    assert identity["owner_token_sha256"] == sha(TOKEN_CANARY.encode("utf-8"))
    assert call["expected_identity_sha256"] == sha(call["identity_bytes"])
    assert TOKEN_CANARY not in call["identity_bytes"].decode("ascii")
    assert call["situation"] == QUERY_CANARY
    assert call["inputs"]["binding_bytes"] == case["files"]["binding.json"].read_bytes()


def test_synthetic_adapter_abstention_is_bounded_canonical_and_never_authentication(case, monkeypatch, capsys):
    # This recorder does not exercise a native peer or establish real cleanup.
    case["outcome"]["validated"] = True
    assert invoke(case, monkeypatch) == 0
    observed = capsys.readouterr()
    assert observed.err == "" and len(observed.out.encode("ascii")) <= LIMIT
    result = json.loads(observed.out)
    assert observed.out == canon(result).decode("ascii") + "\n"
    assert result["status"] == "VALIDATED" and result["inner_status"] == "ABSTAIN"
    assert result["lane"] == LANE and result["lane_binding"] == "DECLARED_CONSISTENT"
    assert result["lane_authentication"] == "UNKNOWN"
    assert result["answer_sha256"] == sha(canon(result["answer"]))
    assert QUERY_CANARY not in observed.out and TOKEN_CANARY not in observed.out
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("result", [
    {"lane_authentication": "AUTHENTICATED"},
    {"lane_binding": "AUTHENTICATED"}, {"lane": "fable-5"},
    {"unexpected": QUERY_CANARY},
    {"answer": {"stored_text": "z" * (LIMIT + 1)}},
], ids=["strong-auth", "strong-binding", "wrong-lane", "extra-field", "oversized"])
def test_untrusted_oversized_or_stronger_auth_output_is_not_published(case, monkeypatch, capsys, result):
    case["outcome"]["result"] = result
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) <= 1


@pytest.mark.parametrize("exc,expected_code", [
    (ValueError(QUERY_CANARY), 2), (RuntimeError(TOKEN_CANARY), 2), (TimeoutError(QUERY_CANARY), 4),
    (KeyboardInterrupt(), 130), (SystemExit(7), 130), (SystemExit(0), 130),
    (SystemExit(None), 130), (SystemExit(TOKEN_CANARY), 130),
], ids=["value-error", "runtime-error", "timeout", "keyboard-interrupt",
        "exit-nonzero", "exit-zero", "exit-none", "exit-detail"])
def test_unavailable_chain_uses_fixed_refusal_without_exception_detail(case, monkeypatch, capsys, exc, expected_code):
    legacy_baseline(case, monkeypatch)
    case["outcome"]["exception"] = exc
    assert_refused(invoke(case, monkeypatch), capsys, expected_code)
    assert len(case["calls"]) == 1


def legacy_baseline(case, monkeypatch):
    """Reach PR1985's outcome checks for RED; new code must use SHARED instead.

    These are test-only old settings, never an implementation fallback. They
    isolate E1/E2 from the separate launcher-name mismatch on the old Source.
    """
    monkeypatch.setenv("WD_Q4_CALLER_DESCRIPTOR_PATH", str(case["files"]["descriptor.json"]))
    monkeypatch.setenv("WD_Q4_CALLER_DESCRIPTOR_SHA256", sha(case["files"]["descriptor.json"].read_bytes()))
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_PATH", str(case["files"]["wd-fleet.json"]))
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_SHA256", sha(case["files"]["wd-fleet.json"].read_bytes()))


def replace_manifest(case, monkeypatch, raw):
    case["files"]["deployment-manifest.json"].write_bytes(raw)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", sha(raw))


@pytest.mark.parametrize("lane", LANES, ids=LANES)
def test_all_five_shared_descriptor_lanes_are_declared_consistent(case, monkeypatch, capsys, lane):
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[lane])
    monkeypatch.chdir(case["worktrees"][lane])
    case["outcome"]["validated"] = True
    assert invoke(case, monkeypatch) == 0
    observed = capsys.readouterr()
    assert observed.err == ""
    result = json.loads(observed.out)
    assert result["lane"] == lane and result["lane_binding"] == "DECLARED_CONSISTENT"
    assert result["lane_authentication"] == "UNKNOWN"
    assert QUERY_CANARY not in observed.out and TOKEN_CANARY not in observed.out
    assert len(case["calls"]) == 1
    identity = json.loads(case["calls"][0]["identity_bytes"])
    assert identity["worktree"] == str(case["worktrees"][lane])
    assert identity["cwd"] == str(Path.cwd())


def test_legacy_caller_settings_cannot_replace_missing_shared_descriptor(case, monkeypatch, capsys):
    legacy_baseline(case, monkeypatch)
    monkeypatch.delenv("WD_Q4_SHARED_DESCRIPTOR_PATH")
    monkeypatch.delenv("WD_Q4_SHARED_DESCRIPTOR_SHA256")
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_stale_caller_aliases_cannot_override_anchored_shared_configuration(case, monkeypatch, capsys):
    monkeypatch.setenv("WD_Q4_CALLER_DESCRIPTOR_PATH", str(case["files"]["descriptor.json"]))
    monkeypatch.setenv("WD_Q4_CALLER_DESCRIPTOR_SHA256", "0" * 64)
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_PATH", str(case["files"]["wd-fleet.json"]))
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_SHA256", "0" * 64)
    case["outcome"]["validated"] = True
    assert invoke(case, monkeypatch) == 0
    observed = capsys.readouterr()
    assert observed.err == "" and json.loads(observed.out)["lane_authentication"] == "UNKNOWN"
    assert len(case["calls"]) == 1


def test_manifest_bytes_must_match_external_anchor(case, monkeypatch, capsys):
    legacy_baseline(case, monkeypatch)
    case["files"]["deployment-manifest.json"].write_bytes(b'{"files":{}}')
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("state", [
    "missing", "empty", "bad-json", "duplicate-files", "bad-schema", "boolean-schema",
    "missing-fleet-pin", "wrong-fleet-pin", "non-hex-fleet-pin", "oversized",
], ids=["missing", "empty", "bad-json", "duplicate-files", "bad-schema", "boolean-schema",
        "missing-fleet-pin", "wrong-fleet-pin", "non-hex-fleet-pin", "oversized"])
def test_anchored_manifest_still_requires_closed_fleet_pin_contract(case, monkeypatch, capsys, state):
    legacy_baseline(case, monkeypatch)
    manifest = json.loads(canon(case["manifest"]))
    if state == "missing":
        case["files"]["deployment-manifest.json"].unlink()
    else:
        if state == "empty":
            raw = b""
        elif state == "bad-json":
            raw = b"not-json"
        elif state == "duplicate-files":
            raw = b'{"schema_version":1,"files":{},"files":{}}'
        else:
            if state == "bad-schema":
                manifest["schema_version"] = 2
            elif state == "boolean-schema":
                manifest["schema_version"] = True
            elif state == "missing-fleet-pin":
                manifest["files"].pop("wd-fleet.json")
            elif state == "wrong-fleet-pin":
                manifest["files"]["wd-fleet.json"] = "0" * 64
            elif state == "non-hex-fleet-pin":
                manifest["files"]["wd-fleet.json"] = "not-a-pin"
            elif state == "oversized":
                manifest["padding"] = "x" * MANIFEST_LIMIT
            raw = canon(manifest)
        replace_manifest(case, monkeypatch, raw)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_descriptor_fleet_pin_must_equal_manifest_pin(case, monkeypatch, capsys):
    case["descriptor"]["fleet_sha256"] = "0" * 64
    raw = canon(case["descriptor"])
    case["files"]["descriptor.json"].write_bytes(raw)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", sha(raw))
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_rehashing_tampered_fleet_and_descriptor_cannot_replace_manifest_anchor(case, monkeypatch, capsys):
    legacy_baseline(case, monkeypatch)
    fleet = case["files"]["wd-fleet.json"].read_bytes() + b" "
    case["files"]["wd-fleet.json"].write_bytes(fleet)
    case["descriptor"]["fleet_sha256"] = sha(fleet)
    descriptor = canon(case["descriptor"])
    case["files"]["descriptor.json"].write_bytes(descriptor)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", sha(descriptor))
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_SHA256", sha(fleet))
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("status,cleanup,inner,expected_code", [
    ("UNKNOWN", "unknown", "transport_cleanup_unknown", 3),
    ("UNKNOWN", "unknown", "attestation_cleanup_unknown", 3),
    ("REFUSED", "unknown", "session_refused", 3),
    ("VALIDATED", "unknown", "ANSWER", 3),
    ("TIMEOUT", "unknown", "timeout", 3),
    ("TIMEOUT", "chain_confirmed", "timeout", 4),
], ids=["transport-unknown", "attestation-unknown", "refused-but-unknown",
        "validated-but-unknown", "unknown-overrides-timeout", "timeout-confirmed"])
def test_cleanup_uncertainty_and_timeout_are_distinct_terminal_outcomes(
        case, monkeypatch, capsys, status, cleanup, inner, expected_code):
    legacy_baseline(case, monkeypatch)
    case["outcome"]["result"] = {"status": status, "cleanup": cleanup, "inner_status": inner}
    assert_refused(invoke(case, monkeypatch), capsys, expected_code)
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("situation", ["status", "done", "codex-lead-1", "a"],
                         ids=["status-word", "done-word", "lane-name", "single-letter"])
def test_public_stored_text_and_metadata_may_coincide_with_short_queries(case, monkeypatch, capsys, situation):
    legacy_baseline(case, monkeypatch)
    case["outcome"].update(validated=True, stored_text="status done codex-lead-1 synthetic public record")
    raw = canon({"situation": situation, "time": "2026-01-01T00:00:00Z"})
    assert invoke(case, monkeypatch, raw) == 0
    observed = capsys.readouterr()
    result = json.loads(observed.out)
    assert observed.err == "" and result["status"] == "CONTENT_WITHHELD"
    assert result["original_inner_status"] == "ANSWER" and result["original_result_count"] == 1
    assert "stored_text" not in observed.out
    assert result["lane_authentication"] == "UNKNOWN"
    assert TOKEN_CANARY not in observed.out and len(case["calls"]) == 1


def test_token_secrecy_remains_required_even_for_validated_public_output(case, monkeypatch, capsys):
    legacy_baseline(case, monkeypatch)
    case["outcome"].update(validated=True, stored_text=TOKEN_CANARY)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("lane", LANES, ids=LANES)
@pytest.mark.parametrize("exception_name,expected", [
    ("CallerCleanupUnknown", 3), ("CallerRefused", 2),
], ids=["cleanup-unknown", "ordinary-refusal"])
def test_adapter_precheck_exception_maps_to_cleanup_or_refusal_without_leaks(
        case, monkeypatch, capsys, lane, exception_name, expected):
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[lane])
    monkeypatch.chdir(case["worktrees"][lane])
    # This synthetic detail must never be rendered, including on stderr.
    case["outcome"]["exception"] = getattr(case["adapter"], exception_name)(
        "synthetic-exception-detail " + QUERY_CANARY + " " + TOKEN_CANARY)
    assert_refused(invoke(case, monkeypatch), capsys, expected)
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("lane", LANES, ids=LANES)
def test_enabled_cli_and_real_adapter_parent_path_needs_no_model_import(case, monkeypatch, capsys, lane):
    """The session is synthetic; this is not a live peer or installed-bundle test."""
    import importlib
    import tools

    real = case["real_adapter"]
    monkeypatch.setitem(sys.modules, "tools.wd_q4_lane_caller_adapter", real)
    monkeypatch.setattr(tools, "wd_q4_lane_caller_adapter", real, raising=False)
    monkeypatch.setattr(real, "_QUARANTINED", False)
    monkeypatch.setattr(real, "_HOLD", [])
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[lane])
    monkeypatch.chdir(case["worktrees"][lane])
    original_parts = real._parts
    parts_refused = []
    def observe_parts(value):
        try:
            return original_parts(value)
        except ValueError:
            parts_refused.append(value == str(case["worktrees"][lane]))
            raise
    monkeypatch.setattr(real, "_parts", observe_parts)
    calls = []
    def fake_session(**kwargs):
        calls.append(kwargs["lane"])
        # Synthetic stand-ins preserve this test's parent-import scope only;
        # they are not native transport or attestation cleanup evidence.
        item, context = kwargs["_owner_guard"], kwargs["_owner_context"]
        assert item._accept_owner_context(context) is context
        for name in ("transport", "attestation"):
            ticket = context._ticket(name)
            assert context._accept_ticket(name, ticket) is ticket
            ticket._begin()
            ticket._complete()
        return {"schema": "wd.q4-lane-answer.v1", "lane": lane, "status": "ABSTAIN",
                "reason": "", "result_count": 0, "results": [],
                "binding": dict(case["descriptor"]["expected_binding"]), "trust": {
                    "lane_authentication": "UNKNOWN", "freshness": "UNKNOWN",
                    "publisher_authentication": "UNKNOWN", "projection_public_attestation": "UNKNOWN",
                    "transport": "caller_injected", "projection": "caller_pinned"}}
    monkeypatch.setattr(real.session, "query_session", fake_session)
    blocked = {"numpy", "faiss", "torch", "sentence_transformers", "transformers"}
    attempted = []
    old_import, old_module = builtins.__import__, importlib.import_module
    def reject(name):
        if name.split(".")[0] in blocked:
            attempted.append(name.split(".")[0])
            raise ImportError("synthetic parent dependency blocked")
    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        reject(name)
        return old_import(name, globals, locals, fromlist, level)
    def guarded_module(name, package=None):
        reject(name)
        return old_module(name, package)
    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(importlib, "import_module", guarded_module)
    code = invoke(case, monkeypatch)
    observed = capsys.readouterr()
    assert code == 0 and observed.err == "", {"synthetic_worktree_refused": parts_refused}
    result = json.loads(observed.out)
    assert result["lane"] == lane and result["inner_status"] == "ABSTAIN"
    assert result["lane_authentication"] == "UNKNOWN" and calls == [lane]
    assert not attempted and QUERY_CANARY not in observed.out and TOKEN_CANARY not in observed.out


# --- live clock: observed local UTC ranks the query; returned cards rechecked before output -------------------

def _query_at(case, when):
    case["query"] = canon({"situation": QUERY_CANARY, "time": when})


@pytest.mark.parametrize("when", ["2025-12-31T23:54:59Z", "2026-01-01T00:05:01Z", "2025-12-31T00:00:00Z",
                                  "2027-01-01T00:00:00Z"])
def test_supplied_time_more_than_300_seconds_from_observed_refuses_before_adapter(case, monkeypatch, capsys, when):
    case["outcome"].update(validated=True)
    _query_at(case, when)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("when", ["2025-12-31T23:55:01Z", "2026-01-01T00:05:00Z", "2026-01-01T00:00:00Z"])
def test_supplied_time_within_300_seconds_is_accepted_but_observed_start_ranks(case, monkeypatch, capsys, when):
    case["outcome"].update(validated=True)
    start = NOW + timedelta(microseconds=250000)
    case["clock"]["times"] = [start, start + timedelta(seconds=2)]
    _query_at(case, when)
    assert invoke(case, monkeypatch) == 0
    assert capsys.readouterr().err == ""
    assert len(case["calls"]) == 1 and case["calls"][0]["time"] == start
    assert case["clock"]["times"] == []


def test_backward_local_clock_during_call_refuses_output(case, monkeypatch, capsys):
    case["outcome"].update(validated=True)
    case["clock"]["times"] = [NOW, NOW - timedelta(microseconds=1)]
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("end, until, ok", [
    (NOW + timedelta(seconds=59), "2026-01-01T00:01Z", True),
    (NOW + timedelta(seconds=60), "2026-01-01T00:01Z", False),
    (NOW + timedelta(seconds=61), "2026-01-01T00:01Z", False),
    (NOW + timedelta(minutes=9), "2026-01-01T00:05Z", False),
], ids=["current-at-end", "expires-at-end", "expired-during-call", "expired-long-call"])
def test_card_expiring_during_the_call_is_not_output(case, monkeypatch, capsys, end, until, ok):
    _direct_card_route(case, monkeypatch)
    case["outcome"].update(validated=True, stored_text=CARD + until)
    case["clock"]["times"] = [NOW, end]
    if ok:
        assert invoke(case, monkeypatch) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "CONTENT_WITHHELD" and output["original_result_count"] == 1
        assert "stored_text" not in json.dumps(output)
    else:
        assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("text", ["status done codex-lead-1 pr:7 next:none", CARD + "2026-01-01T00:01",
                                  CARD + "2026-13-01T00:01Z"], ids=["no-until", "no-z", "bad-month"])
def test_card_without_a_usable_until_is_not_output(case, monkeypatch, capsys, text):
    _direct_card_route(case, monkeypatch)
    case["outcome"].update(validated=True, stored_text=text)
    assert_refused(invoke(case, monkeypatch), capsys)


def test_interrupted_card_baseline_expiring_during_the_call_is_not_output(case, monkeypatch, capsys):
    _pending_baseline(case, monkeypatch)
    case["outcome"].update(validated=True, stored_text=CARD + "2026-01-01T00:01Z")
    case["clock"]["times"] = [NOW, NOW + timedelta(seconds=60)]
    assert_refused(invoke(case, monkeypatch), capsys)


def test_v6_non_card_results_are_not_rechecked_by_card_status(case, monkeypatch, capsys):
    """v6 records bypass card expiry checks but unverified text is still withheld."""
    text = "status done codex-lead-1 synthetic public record until:2020-01-01T00:00Z"
    case["outcome"].update(validated=True, stored_text=text)
    case["clock"]["times"] = [NOW, NOW + timedelta(seconds=30)]
    assert invoke(case, monkeypatch) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "CONTENT_WITHHELD"
    assert result["original_inner_status"] == "ANSWER" and result["original_result_count"] == 1
    assert result["trust"]["freshness"] == "UNKNOWN"
    assert "stored_text" not in json.dumps(result) and text not in json.dumps(result)


def test_content_binding_cli_requires_exact_seven_well_formed_pins():
    from tools.wd_q4_lane_query import CONTENT_BINDING_KEYS
    from tools import wd_q4_lane_caller_cli as target
    binding = {key: "a" * 64 for key in CONTENT_BINDING_KEYS}
    assert target._binding(binding) == binding
    for key in CONTENT_BINDING_KEYS:
        bad = dict(binding)
        del bad[key]
        with pytest.raises(ValueError):
            target._binding(bad)
        bad = dict(binding, **{key: "z" * 64})
        with pytest.raises(ValueError):
            target._binding(bad)
    with pytest.raises(ValueError):
        target._binding(dict(binding, unexpected="a" * 64))


def test_content_output_rejects_each_substituted_pin_even_after_rehash():
    from tools import wd_q4_lane_caller_cli as target
    from tools.wd_q4_lane_query import CONTENT_BINDING_KEYS
    expected = {key: "a" * 64 for key in CONTENT_BINDING_KEYS}
    answer = {"schema": "wd.q4-lane-answer.v1", "lane": "fable-5", "status": "ABSTAIN", "reason": "",
              "result_count": 0, "results": [], "binding": expected, "trust": dict(target._TRUST)}
    result = {"schema": "wd.q4-lane-caller-result.v1", "status": "VALIDATED", "lane": "fable-5",
              "lane_binding": "DECLARED_CONSISTENT", "lane_authentication": "UNKNOWN",
              "cleanup": "chain_confirmed", "inner_status": "ABSTAIN", "result_count": 0,
              "answer": answer, "answer_sha256": target._sha(target._canon(answer)),
              "identity_sha256": "1" * 64, "descriptor_sha256": "2" * 64, "call_id": "3" * 32}
    assert target._output(result, "fable-5", "1" * 64, "2" * 64, expected, 65536, "not-in-output")
    for key in CONTENT_BINDING_KEYS:
        changed = dict(answer, binding=dict(expected, **{key: "b" * 64}))
        forged = dict(result, answer=changed, answer_sha256=target._sha(target._canon(changed)))
        with pytest.raises(ValueError):
            target._output(forged, "fable-5", "1" * 64, "2" * 64, expected, 65536, "not-in-output")


def test_content_pinned_snapshot_is_rechecked_without_legacy_pointer(tmp_path):
    from tools import wd_q4_lane_caller_cli as target, wd_q4_lane_link as link
    from sandbox.mind.step2.shared_index_preflight import _encoder_digest
    memory = tmp_path / "memory"
    memory.mkdir()
    encoder = {"model": "synthetic", "revision": "b" * 40, "weights_sha256": "a" * 64}
    projection = b"{}"
    projection_pin = sha(projection)
    manifest = canon({"projection_schema": "wd.mind-content-memory-projection.v1",
                      "projection_sha256": projection_pin})
    descriptor = canon({"schema": "wd.bridge-mind-q4-memory-descriptor.v2",
                        "scope": "wd.q4-shared-projected-events.v1", "memory_sha256": sha(manifest),
                        "dim": 2, "encoder": encoder})
    files = {"descriptor": (memory / "descriptor.json", descriptor),
             "memory": (memory / "memory.json", manifest),
             "projection": (memory / "projection.json", projection),
             "policy": (tmp_path / "policy.json", b"{}"),
             "roster": (tmp_path / "roster.json", b"{}"),
             "content_decision": (tmp_path / "decision.json", b"{}")}
    for path, raw in files.values():
        path.write_bytes(raw)
    pins = {"encoder_sha256": _encoder_digest(encoder), "policy_sha256": sha(files["policy"][1]),
            "roster_sha256": sha(files["roster"][1]), "content_decision_sha256": sha(files["content_decision"][1]),
            "descriptor_sha256": sha(descriptor), "memory_sha256": sha(manifest),
            "projection_sha256": projection_pin}
    plan = {"schema": link._CONTENT_PLAN_SCHEMA,
            "config": {"memory": str(memory), "descriptor": str(files["descriptor"][0]),
                       "descriptor_sha256": pins["descriptor_sha256"], "runtime_revision": "b" * 40},
            "encoder_sha256": pins["encoder_sha256"], "memory_sha256": pins["memory_sha256"],
            "projection_sha256": pins["projection_sha256"]}
    for key in ("policy", "roster", "content_decision"):
        plan[key + "_path"] = str(files[key][0])
        plan[key + "_sha256"] = pins[key + "_sha256"]
    if link._CONTENT_PLAN_SCHEMA == "wd.q4-public-wrapper-plan.v5":
        for key in ("message_source_path", "message_generation_path", "message_policy_path",
                    "doc_repo_root", "doc_policy_path", "doc_git_executable", "doc_git_sha256"):
            plan[key] = None
    public = tmp_path / "public-plan.json"
    public_raw = canon(plan)
    public.write_bytes(public_raw)
    binding_doc = canon({"schema": "wd.q4-lane-binding.v1", "binding": pins,
                         "public_plan_sha256": sha(public_raw)})
    descriptor_input = {"public_plan": str(public)}
    inputs = {"binding_bytes": binding_doc}
    target._require_current_snapshot(descriptor_input, pins, inputs)
    for path, raw in files.values():
        path.write_bytes(raw + b" ")
        with pytest.raises(ValueError):
            target._require_current_snapshot(descriptor_input, pins, inputs)
        path.write_bytes(raw)
    public.write_bytes(public_raw + b" ")
    with pytest.raises(ValueError):
        target._require_current_snapshot(descriptor_input, pins, inputs)
