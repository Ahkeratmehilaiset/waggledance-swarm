"""Synthetic private-state contract; no live fleet, source or model."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import importlib
import os
from pathlib import Path

import pytest

from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin


def module():
    return importlib.import_module("sandbox.mind.step2.prefix_private_state")


@pytest.fixture
def dirs(tmp_path):
    private, shared = tmp_path / "private", tmp_path / "shared"
    private.mkdir()
    shared.mkdir()
    return private, shared


def pin(digest="1" * 64, offset=1):
    return BridgePrefixPin(BridgeCursor(offset, "posix-v1:1:2", "synthetic"), digest)


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_missing_wrong_or_pointer_derived_state_refuses(dirs):
    mod = module()
    private, shared = dirs
    with mod.locked_state(private, shared, enabled=True) as state:
        with pytest.raises(mod.StateRefused):
            state.require("a" * 64, pin())
        state.require(None, pin())
        state.begin(None)
        state.commit(None, "a" * 64, pin())
        state.require("a" * 64, pin())
        assert state.load("a" * 64) == pin()
        assert pin().sha256 not in repr(state.load("a" * 64))
        with pytest.raises(mod.StateRefused):
            state.require("b" * 64, pin())
        with pytest.raises(mod.StateRefused):
            state.require("a" * 64, pin("a" * 64))


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_pending_or_ambiguous_replace_never_becomes_genesis(dirs, monkeypatch):
    mod = module()
    private, shared = dirs
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin(None)
        real_replace = mod.os.replace
        def ambiguous(*args):
            real_replace(*args)
            raise OSError("synthetic ambiguity")
        monkeypatch.setattr(mod.os, "replace", ambiguous)
        with pytest.raises(mod.StateRefused):
            state.commit(None, "a" * 64, pin())
    with mod.locked_state(private, shared, enabled=True) as state:
        for pointer in (None, "a" * 64):
            with pytest.raises(mod.StateRefused):
                state.require(pointer, pin())


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_nested_roots_refused(dirs):
    mod = module()
    private, shared = dirs
    for left, right in ((private, private), (private, private.parent), (shared.parent, shared)):
        with pytest.raises(mod.StateRefused):
            with mod.locked_state(left, right, enabled=True):
                pytest.fail("overlapping roots accepted")


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_disabled_and_corrupted_private_state_fail_closed(dirs):
    mod = module()
    private, shared = dirs
    with pytest.raises(mod.StateRefused):
        with mod.locked_state(private, shared):
            pytest.fail("default must be off")
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin(None)
        state.commit(None, "a" * 64, pin())
        target = private / mod._STATE
        target.write_bytes(target.read_bytes().replace(b'"schema":', b'"schema":"forged","schema":'))
        with pytest.raises(mod.StateRefused) as error:
            state.load("a" * 64)
        assert error.value.__context__ is None and error.value.__cause__ is None


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_existing_reparse_state_is_refused(dirs):
    mod = module()
    private, shared = dirs
    external = shared / "synthetic"
    external.write_text("synthetic")
    try:
        (private / mod._STATE).symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable to test process")
    with mod.locked_state(private, shared, enabled=True) as state:
        with pytest.raises(mod.StateRefused):
            state.require(None, pin())


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_concurrent_stale_cas_cannot_overwrite(dirs):
    mod = module()
    private, shared = dirs
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin(None)
        state.commit(None, "a" * 64, pin())
    def attempt(next_pointer):
        try:
            with mod.locked_state(private, shared, enabled=True, timeout=3) as state:
                state.require("a" * 64, pin())
                state.begin("a" * 64)
                state.commit("a" * 64, next_pointer, replace(pin(), sha256=next_pointer))
            return "committed"
        except mod.StateRefused:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, ("b" * 64, "c" * 64)))
    assert sorted(outcomes) == ["committed", "refused"]


def test_root_alias_comparison_checks_ancestor_identity(monkeypatch):
    mod = module()
    private, shared = Path("synthetic-private"), Path("synthetic-shared")
    monkeypatch.setattr(mod.os.path, "samefile", lambda left, right:
                        left == private and right == shared)
    assert mod._root_disjoint(private, shared) is False
    monkeypatch.setattr(mod.os.path, "samefile", lambda left, right: False)
    assert mod._root_disjoint(private, shared) is True


@pytest.mark.parametrize("direction", ["private_is_shared_ancestor", "shared_is_private_ancestor"])
def test_aliased_ancestor_identity_refuses_both_nested_directions(monkeypatch, direction):
    mod = module()
    private = Path("synthetic-private-alias") / "private-child"
    shared = Path("synthetic-shared-alias") / "shared-child"
    pair = ((private.parent, shared) if direction == "shared_is_private_ancestor"
            else (shared.parent, private))
    calls = []
    def samefile(left, right):
        calls.append((left, right))
        return (left, right) == pair
    monkeypatch.setattr(mod.os.path, "samefile", samefile)
    assert mod._root_disjoint(private, shared) is False
    assert pair in calls


@pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
def test_root_identity_error_refuses_before_lock_open(dirs, monkeypatch):
    mod = module()
    private, shared = dirs
    def fail_identity(*_):
        raise OSError("synthetic identity error")
    def forbidden_lock(*_, **__):
        pytest.fail("lock must not be opened after an identity error")
    monkeypatch.setattr(mod.os.path, "samefile", fail_identity)
    monkeypatch.setattr(mod, "reader_slot", forbidden_lock)
    with pytest.raises(mod.StateRefused, match="^private_state_refused$") as error:
        with mod.locked_state(private, shared, enabled=True):
            pytest.fail("identity error must refuse")
    assert error.value.__context__ is None and error.value.__cause__ is None


# --- recoverable prefix intent (opt-in): synthetic pointer bytes stand in for the checkpoint publication -------

windows = pytest.mark.skipif(os.name != "nt", reason="secure root requires a Windows fixed volume")
B1, B2, B3 = b"synthetic-pointer-1", b"synthetic-pointer-2", b"synthetic-pointer-3"
P1, P2, P3 = (hashlib.sha256(raw).hexdigest() for raw in (B1, B2, B3))
OLD, NEW = pin("1" * 64, 1), pin("2" * 64, 2)


def publish(shared, raw):
    """Stand-in for the cooperating checkpoint writer replacing the shared pointer."""
    from sandbox.mind.step2 import shared_index_checkpoint as cp
    (shared / cp.POINTER).write_bytes(raw)


def seeded(dirs):
    mod = module()
    private, shared = dirs
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin(None)
        state.commit(None, P1, OLD)
    publish(shared, B1)
    return mod, private, shared


def refusal(mod, call):
    with pytest.raises(mod.StateRefused, match="^private_state_refused$") as error:
        call()
    assert error.value.__context__ is None and error.value.__cause__ is None


def settled(mod, private, shared, pointer, prefix):
    """The private state is exactly (pointer, prefix) with no open intent or marker."""
    with mod.locked_state(private, shared, enabled=True) as state:
        state.require(pointer, prefix)
        assert state.load(pointer) == prefix


@windows
def test_intent_methods_are_default_off_and_write_nothing(dirs):
    mod, private, shared = seeded(dirs)
    with mod.locked_state(private, shared, enabled=True) as state:
        refusal(mod, lambda: state.begin_intent(P1, OLD, P2, NEW))
        refusal(mod, lambda: state.begin_intent(P1, OLD, P2, NEW, enabled="yes"))
        refusal(mod, lambda: state.resolve_intent())
    assert not os.path.lexists(private / mod._PENDING)
    settled(mod, private, shared, P1, OLD)


@windows
def test_intent_then_landed_publication_resolves_once_without_replay(dirs):
    mod, private, shared = seeded(dirs)
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin_intent(P1, OLD, P2, NEW, enabled=True)
        refusal(mod, lambda: state.require(P1, OLD))  # legacy methods see the open intent and refuse
        refusal(mod, lambda: state.load(P1))
        refusal(mod, lambda: state.begin(P1))
        publish(shared, B2)
        assert state.resolve_intent(enabled=True) == "landed"
        refusal(mod, lambda: state.resolve_intent(enabled=True))  # nothing open: no silent replay
    settled(mod, private, shared, P2, NEW)


@windows
@pytest.mark.parametrize("stage", ["before_publish", "after_publish", "during_state_replace",
                                   "ambiguous_state_replace", "after_state_replace"])
def test_a_crash_at_each_stage_resolves_to_the_exact_outcome(dirs, monkeypatch, stage):
    mod, private, shared = seeded(dirs)
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin_intent(P1, OLD, P2, NEW, enabled=True)
    if stage == "before_publish":  # the process died after the intent, before the pointer replace
        kept = (private / mod._PENDING).read_bytes()
        for _ in range(2):  # a checkpoint-only writer could still land the attempt later: never "unchanged"
            with mod.locked_state(private, shared, enabled=True) as state:
                assert state.resolve_intent(enabled=True) == "unknown"
        assert (private / mod._PENDING).read_bytes() == kept
        assert mod._load(private / mod._STATE) == mod._value(P1, OLD)
        return
    publish(shared, B2)
    if stage != "after_publish":
        real_replace, real_unlink = mod.os.replace, mod.os.unlink

        def replace(*args):
            if stage == "during_state_replace":
                raise KeyboardInterrupt  # dies before the private state is replaced
            real_replace(*args)
            raise OSError("synthetic ambiguous replace")

        def unlink(path, *args, **kwargs):
            if Path(path).name == mod._PENDING:
                raise KeyboardInterrupt  # state replaced, dies before the intent is closed
            return real_unlink(path, *args, **kwargs)

        with monkeypatch.context() as broken:
            if stage == "after_state_replace":
                broken.setattr(mod.os, "unlink", unlink)
            else:
                broken.setattr(mod.os, "replace", replace)
            with mod.locked_state(private, shared, enabled=True) as state:
                with pytest.raises((KeyboardInterrupt, mod.StateRefused)):
                    state.resolve_intent(enabled=True)
        assert os.path.lexists(private / mod._PENDING)  # still open: recoverable, never guessed
    for _ in range(2):  # crash-equivalent restart; the second call proves nothing is replayed
        with mod.locked_state(private, shared, enabled=True) as state:
            outcomes = []
            try:
                outcomes.append(state.resolve_intent(enabled=True))
            except mod.StateRefused:
                outcomes.append("refused")
        if not os.path.lexists(private / mod._PENDING):
            break
    assert outcomes[0] == "landed"
    settled(mod, private, shared, P2, NEW)


@windows
def test_a_torn_intent_write_leaves_no_intent_and_blocks_nothing(dirs, monkeypatch):
    mod, private, shared = seeded(dirs)
    real = Path.open

    class Torn:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stream.close()
            return False

        def write(self, data):
            self.stream.write(data[:len(data) // 2])
            self.stream.flush()
            raise KeyboardInterrupt

        def flush(self):
            self.stream.flush()

        def fileno(self):
            return self.stream.fileno()

    def open_(self, mode="r", *args, **kwargs):
        stream = real(self, mode, *args, **kwargs)
        if "x" in mode and self.parent == private:
            return Torn(stream)
        return stream

    with monkeypatch.context() as broken:
        broken.setattr(Path, "open", open_)
        with mod.locked_state(private, shared, enabled=True) as state:
            with pytest.raises(KeyboardInterrupt):
                state.begin_intent(P1, OLD, P2, NEW, enabled=True)
    assert not os.path.lexists(private / mod._PENDING)  # never a partial intent under the final name
    settled(mod, private, shared, P1, OLD)
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin_intent(P1, OLD, P2, NEW, enabled=True)
        publish(shared, B2)
        assert state.resolve_intent(enabled=True) == "landed"


@windows
def test_a_third_pointer_is_refused_and_the_intent_kept(dirs):
    mod, private, shared = seeded(dirs)
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin_intent(P1, OLD, P2, NEW, enabled=True)
    kept = (private / mod._PENDING).read_bytes()
    publish(shared, B3)  # another cooperating writer's pointer, neither prior nor attempt
    for _ in range(2):
        with mod.locked_state(private, shared, enabled=True) as state:
            refusal(mod, lambda: state.resolve_intent(enabled=True))
            refusal(mod, lambda: state.require(P1, OLD))
    assert (private / mod._PENDING).read_bytes() == kept
    publish(shared, B1)  # back to exactly the prior bytes: still not proof the attempt cannot land later
    with mod.locked_state(private, shared, enabled=True) as state:
        assert state.resolve_intent(enabled=True) == "unknown"
    assert (private / mod._PENDING).read_bytes() == kept


@windows
def test_a_moved_state_with_a_third_pointer_is_refused_not_reported_landed(dirs, monkeypatch):
    mod, private, shared = seeded(dirs)
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin_intent(P1, OLD, P2, NEW, enabled=True)
    publish(shared, B2)
    real_unlink = mod.os.unlink

    def unlink(path, *args, **kwargs):
        if Path(path).name == mod._PENDING:
            raise KeyboardInterrupt  # state moved to the candidate, intent still open
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as broken:
        broken.setattr(mod.os, "unlink", unlink)
        with mod.locked_state(private, shared, enabled=True) as state:
            with pytest.raises(KeyboardInterrupt):
                state.resolve_intent(enabled=True)
    kept = (private / mod._PENDING).read_bytes()
    publish(shared, B3)  # meanwhile a third pointer
    with mod.locked_state(private, shared, enabled=True) as state:
        refusal(mod, lambda: state.resolve_intent(enabled=True))
    assert (private / mod._PENDING).read_bytes() == kept


@windows
def test_stale_or_inconsistent_attempts_are_refused_before_any_intent(dirs):
    mod, private, shared = seeded(dirs)
    with mod.locked_state(private, shared, enabled=True) as state:
        refusal(mod, lambda: state.begin_intent(P3, OLD, P2, NEW, enabled=True))  # stale prior pointer
        refusal(mod, lambda: state.begin_intent(P1, NEW, P2, NEW, enabled=True))  # wrong prior prefix
        refusal(mod, lambda: state.begin_intent(P1, OLD, P1, NEW, enabled=True))  # attempt == prior
        refusal(mod, lambda: state.begin_intent(None, OLD, P2, NEW, enabled=True))  # genesis over a state
        refusal(mod, lambda: state.begin_intent(P1, OLD, "X" * 64, NEW, enabled=True))
        assert not os.path.lexists(private / mod._PENDING)
        publish(shared, B3)  # the shared pointer already moved on: the prior is stale
        refusal(mod, lambda: state.begin_intent(P1, OLD, P2, NEW, enabled=True))
        assert not os.path.lexists(private / mod._PENDING)
        publish(shared, B1)
        state.begin_intent(P1, OLD, P2, NEW, enabled=True)
        refusal(mod, lambda: state.begin_intent(P1, OLD, P3, NEW, enabled=True))  # one open intent only


@windows
def test_genesis_intent_is_unknown_until_it_lands(dirs):
    mod = module()
    private, shared = dirs
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin_intent(None, OLD, P1, NEW, enabled=True)
        assert state.resolve_intent(enabled=True) == "unknown"  # no pointer yet: the intent stays open
        refusal(mod, lambda: state.require(None, OLD))
        publish(shared, B1)
        assert state.resolve_intent(enabled=True) == "landed"
    settled(mod, private, shared, P1, NEW)


@windows
def test_a_legacy_marker_stays_unknown_for_intent_recovery(dirs):
    mod, private, shared = seeded(dirs)
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin(P1)  # legacy generic marker, then the process dies
    marker = (private / mod._PENDING).read_bytes()
    publish(shared, B2)
    with mod.locked_state(private, shared, enabled=True) as state:
        refusal(mod, lambda: state.resolve_intent(enabled=True))
        refusal(mod, lambda: state.begin_intent(P1, OLD, P2, NEW, enabled=True))
    assert (private / mod._PENDING).read_bytes() == marker  # external reconciliation, never guessed


@windows
def test_concurrent_cooperating_publishers_cannot_both_land(dirs):
    mod, private, shared = seeded(dirs)

    def attempt(raw):
        pointer = hashlib.sha256(raw).hexdigest()
        try:
            with mod.locked_state(private, shared, enabled=True, timeout=3) as state:
                state.begin_intent(P1, OLD, pointer, replace(NEW, sha256=pointer), enabled=True)
                publish(shared, raw)
                return state.resolve_intent(enabled=True), pointer
        except mod.StateRefused:
            return "refused", pointer

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (B2, B3)))
    assert sorted(outcome for outcome, _ in outcomes) == ["landed", "refused"]
    winner = next(pointer for outcome, pointer in outcomes if outcome == "landed")
    settled(mod, private, shared, winner, replace(NEW, sha256=winner))


@windows
def test_private_intent_content_never_leaves_the_private_root(dirs):
    mod, private, shared = seeded(dirs)
    with mod.locked_state(private, shared, enabled=True) as state:
        state.begin_intent(P1, OLD, P2, NEW, enabled=True)
        intent = (private / mod._PENDING).read_bytes()
        publish(shared, B2)
        result = state.resolve_intent(enabled=True)
        assert result == "landed" and repr(state).find(NEW.sha256) == -1
    from sandbox.mind.step2 import shared_index_checkpoint as cp
    assert sorted(item.name for item in shared.iterdir()) == sorted([cp.POINTER, cp.LOCK])
    assert (shared / cp.POINTER).read_bytes() == B2
    for digest in (OLD.sha256, NEW.sha256):
        assert digest.encode() in intent  # private intent holds the prefix pins ...
        for item in shared.iterdir():  # ... and none of them reaches the shared root
            assert digest.encode() not in item.read_bytes()


# --- v2: deterministic interleavings with the ACTUAL checkpoint prepare/commit API (mocked artifact verifier) ----

@pytest.fixture
def real(tmp_path, monkeypatch):
    """Protocol only, as in test_prefix_checkpoint_v1: synthetic log, real prepare/commit, mocked artifacts."""
    import json
    from types import SimpleNamespace
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot, shared_index_checkpoint as checkpoint
    from sandbox.mind.step2.test_status_public_filter import digest, enc, event, read_fixture

    log_dir = tmp_path / "log"
    log_dir.mkdir()
    _, pins, path = read_fixture(log_dir, [event()])
    anchor = json.loads(pins["anchor_bytes"])
    start = BridgePrefixPin(BridgeCursor(**anchor["cursor_start"]), hashlib.sha256(b"").hexdigest())
    tasks = frozenset({"public-task", "nonreview-private"})
    anchor_raw = enc(anchor)
    first = snapshot.read_anchored_delta(path, start.cursor, tasks, anchor_bytes=anchor_raw,
        anchor_sha256=digest(anchor_raw), generation_path=anchor["generation_path"], max_bytes=65536, max_rows=100,
        policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"], roster_bytes=pins["roster_bytes"],
        roster_sha256=pins["roster_sha256"], public_policy_bytes=pins["public_policy_bytes"],
        public_policy_sha256=pins["public_policy_sha256"], expected_prefix_sha256=start.sha256,
        max_prefix_bytes=65536)
    projection_sha = digest(json.dumps(first["projection"], sort_keys=True, indent=1).encode())
    artifacts = {name: (projection_sha if name == "projection_sha256" else "a" * 64) for name in checkpoint.ARTIFACTS}
    fake = dict(artifacts, source_mode={"status_mode": "public-v1", "public_policy_sha256": pins["public_policy_sha256"]},
                source_binding=checkpoint._source_binding(start.cursor, first["candidate_cursor"], digest(anchor_raw),
                                                          projection_sha, pins["policy_sha256"], pins["roster_sha256"]))
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *args: fake)
    private, shared = tmp_path / "private", tmp_path / "shared"
    private.mkdir()
    shared.mkdir()
    genesis = dict(expected_pointer_sha256=None, expected_artifacts=artifacts, initial_cursor=start.cursor,
                   snapshot_result=first, expected_start_prefix=start, expected_anchor_sha256=digest(anchor_raw),
                   expected_anchor_bytes=anchor_raw, policy_bytes=pins["policy_bytes"],
                   expected_policy_sha256=pins["policy_sha256"], roster_bytes=pins["roster_bytes"],
                   expected_roster_sha256=pins["roster_sha256"], expected_mode="public-v1",
                   expected_public_policy_sha256=pins["public_policy_sha256"],
                   public_policy_bytes=pins["public_policy_bytes"])

    def prepare(name):
        return checkpoint.prepare_prefix_genesis(shared, name, start.cursor, first["candidate_cursor"], tasks,
                                                 enabled=True, **genesis)["attempt_sha256"]

    def commit(name, attempt):
        return checkpoint.commit_prefix_genesis(shared, name, start.cursor, first["candidate_cursor"], tasks,
                                                expected_attempt_sha256=attempt, **genesis)["pointer_sha256"]

    def pointer():
        target = shared / checkpoint.POINTER
        return hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None

    return SimpleNamespace(private=private, shared=shared, start=start, candidate=first["candidate_prefix"],
                           prepare=prepare, commit=commit, pointer=pointer, checkpoint=checkpoint)


def _race_after_observation(monkeypatch, mod, writer):
    """Run a checkpoint-only writer exactly once, right after the private module observed the shared pointer."""
    observe, raced = mod._shared_pointer, []

    def hooked(*args, **kwargs):
        value = observe(*args, **kwargs)
        if not raced:
            try:
                raced.append(writer())
            except Exception:
                raced.append("fenced")  # refused by the checkpoint (busy lock or CAS)
        return value

    monkeypatch.setattr(mod, "_shared_pointer", hooked)
    return raced


@windows
def test_resolution_fences_a_checkpoint_writer_between_observation_and_decision(real, monkeypatch):
    mod = module()
    attempt = real.prepare("gen-a")
    with mod.locked_state(real.private, real.shared, enabled=True) as state:
        state.begin_intent(None, real.start, attempt, real.candidate, enabled=True)
    with monkeypatch.context() as racing:
        raced = _race_after_observation(racing, mod, lambda: real.commit("gen-a", attempt))
        with mod.locked_state(real.private, real.shared, enabled=True) as state:
            try:
                outcome = state.resolve_intent(enabled=True)
            except mod.StateRefused:
                outcome = "refused"
    slot_open = os.path.lexists(real.private / mod._PENDING)
    # Never report a closed "unchanged" while the attempt is (or became) the shared pointer.
    assert not (real.pointer() == attempt and not slot_open and outcome != "landed")
    assert raced == ["fenced"] and real.pointer() is None  # the writer could not act inside the decision
    assert outcome == "unknown" and slot_open
    # A LATE checkpoint-only writer lands the same attempt after resolution: the kept intent still catches it.
    assert real.commit("gen-a", attempt) == attempt
    with mod.locked_state(real.private, real.shared, enabled=True) as state:
        assert state.resolve_intent(enabled=True) == "landed"
    settled(mod, real.private, real.shared, attempt, real.candidate)


def _race_before_slot(monkeypatch, mod, writer):
    """Run a checkpoint-only writer exactly once, immediately before the intent slot is created."""
    create, raced = mod._create_slot, []

    def hooked(*args, **kwargs):
        if not raced:
            try:
                raced.append(writer())
            except Exception:
                raced.append("fenced")
        return create(*args, **kwargs)

    monkeypatch.setattr(mod, "_create_slot", hooked)
    return raced


@windows
@pytest.mark.parametrize("point", ["after_observation", "before_slot"])
def test_begin_intent_fences_a_checkpoint_writer_between_check_and_slot(real, monkeypatch, point):
    mod = module()
    attempt, other = real.prepare("gen-a"), real.prepare("gen-b")
    race = _race_after_observation if point == "after_observation" else _race_before_slot
    with monkeypatch.context() as racing:
        raced = race(racing, mod, lambda: real.commit("gen-b", other))
        with mod.locked_state(real.private, real.shared, enabled=True) as state:
            try:
                state.begin_intent(None, real.start, attempt, real.candidate, enabled=True)
                opened = True
            except mod.StateRefused:
                opened = False
    # An intent is opened only against the pointer that is still current when the slot appears.
    assert not (opened and real.pointer() is not None)
    assert raced == ["fenced"] and opened and real.pointer() is None
    # A later checkpoint-only writer publishing a different pointer is a third pointer: refused, intent kept.
    assert real.commit("gen-b", other) == other
    kept = (real.private / mod._PENDING).read_bytes()
    with mod.locked_state(real.private, real.shared, enabled=True) as state:
        refusal(mod, lambda: state.resolve_intent(enabled=True))
    assert (real.private / mod._PENDING).read_bytes() == kept


@windows
def test_landed_resolution_with_the_actual_checkpoint_commit(real):
    mod = module()
    attempt = real.prepare("gen-a")
    with mod.locked_state(real.private, real.shared, enabled=True) as state:
        state.begin_intent(None, real.start, attempt, real.candidate, enabled=True)
        assert real.commit("gen-a", attempt) == attempt  # the cooperating writer, inside the private lock
        assert state.resolve_intent(enabled=True) == "landed"
    settled(mod, real.private, real.shared, attempt, real.candidate)
