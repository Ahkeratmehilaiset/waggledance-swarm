"""Opt-in prefix checkpoint fencing, isolated: synthetic fixtures, mocked artifact verifier, no live data."""
import ast
from dataclasses import asdict
import hashlib
import inspect
import json
import os
from types import SimpleNamespace

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

pytestmark = pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")

Refused = checkpoint.CheckpointRefused


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Protocol only, as in test_prefix_checkpoint_v1: synthetic log, real prepare/commit, mocked artifacts."""
    from sandbox.mind.step2.test_status_public_filter import ROSTER, digest, enc, event, read_fixture

    log_dir = tmp_path / "log"
    log_dir.mkdir()
    _, pins, path = read_fixture(log_dir, [event()])
    anchor = json.loads(pins["anchor_bytes"])
    start = BridgePrefixPin(BridgeCursor(**anchor["cursor_start"]), hashlib.sha256(b"").hexdigest())
    tasks = frozenset({"public-task", "nonreview-private"})
    common = dict(policy_bytes=pins["policy_bytes"], expected_policy_sha256=pins["policy_sha256"],
                  roster_bytes=pins["roster_bytes"], expected_roster_sha256=pins["roster_sha256"],
                  expected_mode="public-v1", expected_public_policy_sha256=pins["public_policy_sha256"],
                  public_policy_bytes=pins["public_policy_bytes"])

    def read(pin):
        raw = enc(dict(anchor, cursor_start=asdict(pin.cursor)))
        return snapshot.read_anchored_delta(path, pin.cursor, tasks, anchor_bytes=raw, anchor_sha256=digest(raw),
            generation_path=anchor["generation_path"], max_bytes=65536, max_rows=100,
            policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"],
            roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"],
            public_policy_bytes=pins["public_policy_bytes"], public_policy_sha256=pins["public_policy_sha256"],
            expected_prefix_sha256=pin.sha256, max_prefix_bytes=65536), raw

    first, first_anchor = read(start)
    projection_sha = digest(json.dumps(first["projection"], sort_keys=True, indent=1).encode())
    artifacts = {name: (projection_sha if name == "projection_sha256" else "a" * 64) for name in checkpoint.ARTIFACTS}
    fake = dict(artifacts, source_mode={"status_mode": "public-v1", "public_policy_sha256": pins["public_policy_sha256"]},
                source_binding=checkpoint._source_binding(start.cursor, first["candidate_cursor"],
                    digest(first_anchor), projection_sha, pins["policy_sha256"], pins["roster_sha256"]))
    monkeypatch.setattr(checkpoint, "_verify_generation", lambda *args: fake)
    root = tmp_path / "shared"
    root.mkdir()
    genesis = dict(expected_pointer_sha256=None, expected_artifacts=artifacts, initial_cursor=start.cursor,
                   snapshot_result=first, expected_start_prefix=start, expected_anchor_sha256=digest(first_anchor),
                   expected_anchor_bytes=first_anchor, **common)

    def prepare(name):
        return checkpoint.prepare_prefix_genesis(root, name, start.cursor, first["candidate_cursor"], tasks,
                                                 enabled=True, **genesis)["attempt_sha256"]

    def commit(name, attempt=None, **extra):
        return checkpoint.commit_prefix_genesis(root, name, start.cursor, first["candidate_cursor"], tasks,
                                                expected_attempt_sha256=attempt, **genesis, **extra)["pointer_sha256"]

    def pointer():
        target = root / checkpoint.POINTER
        return sha(target.read_bytes()) if target.exists() else None

    def empty(append=True):
        """An empty advance over the landed genesis (call only after the genesis pointer exists)."""
        current = first["candidate_prefix"]
        with path.open("ab") as writer:
            writer.write(b"" if not append else enc(event(agent="claude-rco-1", agent_uuid=ROSTER["claude-rco-1"])) + b"\n")
        result, raw = read(current)
        args = dict(expected_pointer_sha256=pointer(), expected_anchor_sha256=digest(raw),
                    expected_anchor_bytes=raw, **common)
        attempt = checkpoint.prepare_prefix_empty(root, result, tasks, expected_start_prefix=current,
                                                  enabled=True, **args)["attempt_sha256"]

        def advance(attempt=None, **extra):
            return checkpoint.advance_prefix_empty(root, result, tasks, expected_start_prefix=current,
                                                   expected_attempt_sha256=attempt, **args, **extra)
        return attempt, advance, args["expected_pointer_sha256"]

    return SimpleNamespace(root=root, prepare=prepare, commit=commit, pointer=pointer, empty=empty)


def fence(env, prior, attempt, **extra):
    return checkpoint.open_prefix_fence(env.root, expected_prior_pointer_sha256=prior,
                                        expected_attempt_sha256=attempt, enabled=True, **extra)


def resolve(env, prior, attempt, **extra):
    return checkpoint.resolve_prefix_fence(env.root, expected_prior_pointer_sha256=prior,
                                           expected_attempt_sha256=attempt, enabled=True, **extra)["status"]


def listing(root):
    """Files besides the persistent writer lock (created empty by any locked call, including prepare)."""
    return {item.name: item.read_bytes() for item in root.iterdir()
            if item.is_file() and item.name != checkpoint.LOCK}


def refuses(call):
    with pytest.raises(Refused) as caught:
        call()
    assert str(caught.value) == "checkpoint_refused"


def test_fencing_is_default_off_and_legacy_writers_are_unchanged(env):
    attempt = env.prepare("gen-a")
    for enabled in (False, None, 1, "true"):
        refuses(lambda: checkpoint.open_prefix_fence(env.root, expected_prior_pointer_sha256=None,
                                                     expected_attempt_sha256=attempt, enabled=enabled))
        refuses(lambda: checkpoint.resolve_prefix_fence(env.root, expected_prior_pointer_sha256=None,
                                                        expected_attempt_sha256=attempt, enabled=enabled))
    assert listing(env.root) == {}
    # Without any fence the legacy commit is byte-for-byte the former behaviour and writes no fence file.
    assert env.commit("gen-a", attempt) == attempt
    assert sorted(listing(env.root)) == [checkpoint.POINTER]
    # A fenced commit needs an open fence; without one it publishes nothing.
    other_attempt, advance, prior = env.empty()
    refuses(lambda: advance(other_attempt, fenced=True))
    assert env.pointer() == prior
    assert advance(other_attempt)["pointer_sha256"] == other_attempt


def test_fenced_requires_a_bound_attempt_and_never_applies_to_preparation(env):
    attempt = env.prepare("gen-a")
    fence(env, None, attempt)
    refuses(lambda: env.commit("gen-a", None, fenced=True))  # unbound attempt
    for flag in (1, "yes", None):
        refuses(lambda: env.commit("gen-a", attempt, fenced=flag))
    assert env.pointer() is None


def test_open_binds_prior_and_attempt_and_never_overwrites(env):
    attempt, other = env.prepare("gen-a"), env.prepare("gen-b")
    for prior, bound in (("0" * 64, attempt), (None, "x" * 64), (None, attempt.upper()), (attempt, attempt),
                         (None, None), (None, 7)):
        refuses(lambda: fence(env, prior, bound))  # stale prior, malformed digest, or prior == attempt
    assert listing(env.root) == {}
    assert fence(env, None, attempt)["status"] == "fenced"
    kept = listing(env.root)
    assert sorted(kept) == [checkpoint.FENCE]
    assert json.loads(kept[checkpoint.FENCE]) == {"schema": checkpoint.FENCE_SCHEMA,
                                                 "prior_pointer_sha256": None, "attempt_sha256": attempt}
    refuses(lambda: fence(env, None, other))  # one active fence; the existing one is preserved
    refuses(lambda: fence(env, None, attempt))
    assert listing(env.root) == kept


def test_an_open_fence_refuses_every_other_cooperating_writer(env):
    attempt, other = env.prepare("gen-a"), env.prepare("gen-b")
    fence(env, None, attempt)
    refuses(lambda: env.commit("gen-a", attempt))  # the SAME attempt without the fence
    refuses(lambda: env.commit("gen-a"))  # the legacy unpinned form
    refuses(lambda: env.commit("gen-b", other))
    refuses(lambda: env.commit("gen-b", other, fenced=True))  # another attempt cannot use this fence
    assert env.pointer() is None
    assert env.commit("gen-a", attempt, fenced=True) == attempt
    assert resolve(env, None, attempt) == "landed"
    assert sorted(listing(env.root)) == sorted([checkpoint.POINTER, "q4-prefix-fence-" + attempt + ".landed.json"])


def test_the_empty_advance_writer_is_fenced_too(env):
    genesis = env.prepare("gen-a")
    env.commit("gen-a", genesis)
    attempt, advance, prior = env.empty()
    fence(env, prior, attempt)
    refuses(lambda: advance(attempt))
    refuses(lambda: advance())
    assert env.pointer() == prior
    assert advance(attempt, fenced=True)["pointer_sha256"] == attempt
    assert resolve(env, prior, attempt) == "landed"


def test_a_fenced_no_change_is_refused(env):
    genesis = env.prepare("gen-a")
    env.commit("gen-a", genesis)
    refuses(lambda: fence(env, genesis, genesis))
    attempt, advance, prior = env.empty(append=False)
    assert attempt == prior == genesis
    # A no-change call publishes nothing, so it can never satisfy a fence; the legacy form is unchanged.
    refuses(lambda: advance(attempt, fenced=True))
    assert advance(attempt) == {"status": "no_change", "pointer_sha256": prior}
    assert env.pointer() == prior


@pytest.mark.parametrize("stage", ["before_commit", "replace_fails", "after_replace", "between_record_and_unlink"])
def test_a_crash_at_each_stage_resolves_to_the_exact_definitive_outcome(env, monkeypatch, stage):
    attempt = env.prepare("gen-a")
    fence(env, None, attempt)
    if stage == "replace_fails":
        with monkeypatch.context() as patch:
            patch.setattr(checkpoint.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("injected")))
            refuses(lambda: env.commit("gen-a", attempt, fenced=True))
    elif stage == "after_replace":
        real_replace = os.replace

        def replace_then_crash(source, target):
            real_replace(source, target)
            raise OSError("injected after replace")
        with monkeypatch.context() as patch:
            patch.setattr(checkpoint.os, "replace", replace_then_crash)
            refuses(lambda: env.commit("gen-a", attempt, fenced=True))
    elif stage == "between_record_and_unlink":
        assert env.commit("gen-a", attempt, fenced=True) == attempt
        real_unlink = os.unlink

        def unlink_crash(target, *a, **k):
            if os.path.basename(str(target)) == checkpoint.FENCE:
                raise OSError("injected before fence removal")
            return real_unlink(target, *a, **k)
        with monkeypatch.context() as patch:
            patch.setattr(checkpoint.os, "unlink", unlink_crash)
            refuses(lambda: resolve(env, None, attempt))
        assert (env.root / checkpoint.FENCE).exists()
        refuses(lambda: env.commit("gen-b", env.prepare("gen-b")))  # still fenced in the crash window
    expected = "not_landed" if stage in ("before_commit", "replace_fails") else "landed"
    assert resolve(env, None, attempt) == expected
    assert not (env.root / checkpoint.FENCE).exists()
    assert resolve(env, None, attempt) == expected  # idempotent: the durable record answers
    if expected == "landed":
        assert env.pointer() == attempt
    else:
        # Definitive: neither a late fenced commit nor a late legacy commit of the same attempt can land now.
        refuses(lambda: env.commit("gen-a", attempt, fenced=True))
        refuses(lambda: env.commit("gen-a", attempt))
        refuses(lambda: env.commit("gen-a"))
        refuses(lambda: fence(env, None, attempt))
        assert env.pointer() is None
        # Other legacy work proceeds once the fence is resolved.
        other = env.prepare("gen-b")
        assert env.commit("gen-b", other) == other


def test_a_torn_fence_stage_leaves_no_fence_and_blocks_nothing(env, monkeypatch):
    attempt = env.prepare("gen-a")
    with monkeypatch.context() as patch:
        patch.setattr(checkpoint.os, "link", lambda *a: (_ for _ in ()).throw(OSError("injected")))
        refuses(lambda: fence(env, None, attempt))
    assert not (env.root / checkpoint.FENCE).exists()
    assert env.commit("gen-a", attempt) == attempt


def test_a_third_pointer_under_a_fence_is_refused_and_the_fence_kept(env):
    attempt, other = env.prepare("gen-a"), env.prepare("gen-b")
    fence(env, None, attempt)
    (env.root / checkpoint.POINTER).write_bytes(b'{"non":"cooperating"}')  # a writer that ignores the lock
    kept = (env.root / checkpoint.FENCE).read_bytes()
    refuses(lambda: resolve(env, None, attempt))
    assert (env.root / checkpoint.FENCE).read_bytes() == kept
    assert not any(name.startswith("q4-prefix-fence-") for name in listing(env.root))


def test_a_malformed_or_foreign_fence_fails_closed_and_is_preserved(env):
    attempt = env.prepare("gen-a")
    for raw in (b"not json", b'{"schema":"wd.q4-prefix-fence.v1"}',
                json.dumps({"schema": checkpoint.FENCE_SCHEMA, "prior_pointer_sha256": None,
                            "attempt_sha256": attempt}, indent=1).encode()):  # not canonical
        (env.root / checkpoint.FENCE).write_bytes(raw)
        refuses(lambda: env.commit("gen-a", attempt))
        refuses(lambda: env.commit("gen-a", attempt, fenced=True))
        refuses(lambda: resolve(env, None, attempt))
        refuses(lambda: fence(env, None, attempt))
        assert (env.root / checkpoint.FENCE).read_bytes() == raw
        assert env.pointer() is None
        (env.root / checkpoint.FENCE).unlink()  # test cleanup of its own fixture file only
    refuses(lambda: resolve(env, None, attempt))  # no fence and no record: nothing to resolve


def test_resolution_is_bound_to_the_exact_fence(env):
    attempt, other = env.prepare("gen-a"), env.prepare("gen-b")
    fence(env, None, attempt)
    refuses(lambda: resolve(env, None, other))
    refuses(lambda: resolve(env, "0" * 64, attempt))
    assert (env.root / checkpoint.FENCE).exists()
    assert resolve(env, None, attempt) == "not_landed"
    refuses(lambda: resolve(env, None, other))


def test_resolution_is_definitive_against_a_fenced_writer_racing_the_observation(env, monkeypatch):
    attempt = env.prepare("gen-a")
    fence(env, None, attempt)
    observe, raced = checkpoint._current_pointer, []

    def hooked(root):
        value = observe(root)
        if not raced:
            try:
                raced.append(env.commit("gen-a", attempt, fenced=True))
            except Refused:
                raced.append("fenced")  # the writer lock is held by the resolution
        return value

    with monkeypatch.context() as patch:
        patch.setattr(checkpoint, "_current_pointer", hooked)
        assert resolve(env, None, attempt) == "not_landed"
    assert raced == ["fenced"] and env.pointer() is None
    # The late writer that v2 could only call UNKNOWN can no longer land the attempt.
    refuses(lambda: env.commit("gen-a", attempt, fenced=True))
    refuses(lambda: env.commit("gen-a", attempt))
    assert env.pointer() is None


def test_resolution_racing_inside_a_fenced_commit_is_busy_and_the_commit_lands(env, monkeypatch):
    attempt = env.prepare("gen-a")
    fence(env, None, attempt)
    gate, raced = checkpoint._fence_gate, []

    def hooked(*args, **kwargs):
        if not raced:
            try:
                raced.append(resolve(env, None, attempt))
            except Refused:
                raced.append("busy")
        return gate(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(checkpoint, "_fence_gate", hooked)
        assert env.commit("gen-a", attempt, fenced=True) == attempt
    assert raced == ["busy"] and (env.root / checkpoint.FENCE).exists()
    assert resolve(env, None, attempt) == "landed"


def test_every_pointer_replacement_passes_the_fence_gate():
    """Writer inventory: the pointer is replaced only in _publish_pointer, whose first act is the gate."""
    tree = ast.parse(inspect.getsource(checkpoint))
    replacing = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "replace" and getattr(call.func.value, "id", None) == "os"):
                    replacing.add(node.name)
    assert replacing == {"_publish_pointer"}
    first = ast.parse(inspect.getsource(checkpoint._publish_pointer)).body[0].body[0]
    assert isinstance(first, ast.Expr) and first.value.func.id == "_fence_gate"
    callers = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
               and any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "_publish_pointer"
                       for c in ast.walk(node))}
    assert callers == {"advance_empty_delta", "commit_generation", "commit_history_generation"}


def test_the_history_writer_is_fenced_before_any_delta_file(tmp_path):
    pytest.importorskip("faiss")
    from sandbox.mind.step2.test_prefix_checkpoint_v1 import _delta, _read
    from sandbox.mind.step2.test_shared_index_builder import (
        APPROVED, LATER_PUBLIC_EVENTS, PUBLIC_EVENTS, _artifacts_of, build, predecessor_of,
    )
    root = tmp_path / "memory"
    root.mkdir()
    a, source_a, kw_a, start = _delta(tmp_path / "log", PUBLIC_EVENTS)
    first = build(root, name="gen-a", proj=a["projection"], approved_tasks=None, roster=None,
                  source=source_a, allow_unbound_source=False)
    checkpoint.commit_prefix_genesis(root, "gen-a", start.cursor, a["candidate_cursor"], APPROVED,
        expected_pointer_sha256=None, expected_artifacts=_artifacts_of(first), initial_cursor=start.cursor,
        snapshot_result=a, expected_start_prefix=start, **kw_a)
    pointer0 = sha((root / checkpoint.POINTER).read_bytes())
    _, first_pin = _read(root, pointer0)
    b, source_b, kw_b, _ = _delta(tmp_path / "log", LATER_PUBLIC_EVENTS, first_pin.cursor, first_pin)
    second = build(root, name="gen-b", proj=b["projection"], approved_tasks=None, roster=None,
        source=source_b, allow_unbound_source=False,
        predecessor=dict(predecessor_of(root, "gen-a", first),
                         watermark={"root": str(root), "pointer_sha256": pointer0}))
    history = dict(snapshot_result=b, expected_pointer_sha256=pointer0, expected_artifacts=_artifacts_of(second),
                   expected_start_prefix=first_pin, **kw_b)
    attempt = checkpoint.prepare_prefix_history(root, "gen-b", first_pin.cursor, b["candidate_cursor"], APPROVED,
                                                enabled=True, **history)["attempt_sha256"]
    checkpoint.open_prefix_fence(root, expected_prior_pointer_sha256=pointer0, expected_attempt_sha256=attempt,
                                 enabled=True)
    before = sorted(item.name for item in root.iterdir())
    for extra in ({"expected_attempt_sha256": attempt}, {}):
        refuses(lambda: checkpoint.commit_prefix_history(root, "gen-b", first_pin.cursor, b["candidate_cursor"],
                                                         APPROVED, **history, **extra))
        assert sorted(item.name for item in root.iterdir()) == before  # no delta file, no archive
    receipt = checkpoint.commit_prefix_history(root, "gen-b", first_pin.cursor, b["candidate_cursor"], APPROVED,
                                               expected_attempt_sha256=attempt, fenced=True, **history)
    assert receipt["pointer_sha256"] == attempt
    assert checkpoint.resolve_prefix_fence(root, expected_prior_pointer_sha256=pointer0,
                                           expected_attempt_sha256=attempt, enabled=True) == {"status": "landed"}
