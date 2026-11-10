"""Evidence-only prototype: a two-instant log-position bound for a withheld Mind answer. Synthetic data only.

Nothing here is wired into the lane caller, the launchers, the writer or any live path, and no answer text is
ever released. The prototype composes two observations of the EXISTING guard
tools.wd_q4_snapshot_lag.check_snapshot_position (before the query and right before output) for one pinned
watermark. A bound is a statement about the observed log POSITION only:

* it is not authenticated freshness, source authentication, coverage, a clock or a lease;
* bytes appended after the watermark are counted but never bound by digest: the guard returns only lag_bytes, so
  ANY rewrite of the unpinned suffix between the two instants whose length does not decrease (same length, or a
  different and longer suffix of complete records within the budget) is invisible (pinned below as a known
  limitation); binding the suffix would need the second observation pinned to the first observation's candidate
  prefix, which is a guard change, not test-only;
* any refusal of either observation, or a shrinking suffix between them, is UNKNOWN, never a weaker bound.

Further gaps a wired version must close (recorded, not solved here):

* cost and ceiling: every observation rehashes the whole pinned prefix (no cache, pinned below), so one bound costs
  two full prefix reads; the guard refuses a prefix above max_prefix_bytes, itself capped at the reader's
  MAX_PREFIX_BYTES (1 GiB), so once the log outgrows the ceiling every bound is UNKNOWN for good (pinned below);
  a measured cost budget and a growth plan are prerequisites for wiring;
* the window after the second instant: the bound is a statement about the two observation instants only; anything
  between the second observation and the moment the answer is emitted or consumed is unbound (the guard is not a
  lease); a wired version must say so and place the second instant as late as possible (pinned below);
* snapshot binding: the guard bounds whatever valid prefix pin it is given; nothing here proves that the watermark
  is the source prefix of the snapshot that produced the answer (pinned below); the caller must bind it to the
  validated snapshot and descriptor, together with blocker 2;
* the envelope helper below calls lane caller internals (_output, _TRUST, _sha, _canon): acceptable for evidence,
  brittle against refactors.

Two blockers for safe wiring are pinned as tests, not worked around:

1. The startup launchers accept the withheld envelope only with its exact key list, so adding position evidence
   to that envelope is not a compatible extension; it needs a reviewed launcher change in the same PR.
2. The content route anchors its source by the content decision ``source_prefix`` (sha256, offset, generation)
   without a file identity. The guard refuses an empty identity; substituting the identity observed at read time
   would accept a same-bytes replacement of the log, i.e. weaken the check. Recording the identity needs a change
   to the content decision writer, which is out of scope.
"""
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sandbox.mind.step2 import content_memory as cm
from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_snapshot_lag as guard
from tools.wd_q4_lane_query import CONTENT_BINDING_KEYS
from waggledance.core import bridge_log_reader as log_reader

ROOT = Path(__file__).resolve().parents[2]
LAUNCHERS = ("ops/windows/reboot/start-wd-agent.ps1", "ops/windows/reboot/start-wd-tools-consumer.ps1")
BUCKETS = (0, 4096, 65536)  # the coarse buckets the existing position diagnostic allows
OBSERVATION_KEYS = frozenset({"status", "lag_bytes", "source_authentication", "freshness"})
BOUND_KEYS = frozenset({"status", "observations", "lag_bucket", "freshness", "source_authentication", "coverage"})
RECORD = b'{"event":"synthetic"}\n'


def compose(pre, post):
    """Prototype rule (test-owned, never wired): both instants bounded for the same watermark, suffix not shrunk."""
    def bounded(observation):
        return (type(observation) is dict and frozenset(observation) == OBSERVATION_KEYS
                and observation["status"] == "BOUNDED_BY_LOG_POSITION"
                and observation["source_authentication"] == "UNKNOWN" and observation["freshness"] == "UNKNOWN"
                and type(observation["lag_bytes"]) is int and 0 <= observation["lag_bytes"] <= BUCKETS[-1])
    if not (bounded(pre) and bounded(post)) or post["lag_bytes"] < pre["lag_bytes"]:
        return {"status": "UNKNOWN"}
    bucket = next(limit for limit in BUCKETS if post["lag_bytes"] <= limit)
    return {"status": "BOUNDED_BY_LOG_POSITION", "observations": 2, "lag_bucket": bucket,
            "freshness": "UNKNOWN", "source_authentication": "UNKNOWN", "coverage": "UNKNOWN"}


@pytest.fixture
def log(tmp_path):
    path, generation = tmp_path / "events.jsonl", tmp_path / "generation.json"
    path.write_bytes(RECORD * 3)
    generation.write_text(json.dumps({"generation": "synthetic-v1"}), encoding="utf-8")
    with path.open("rb") as stream:
        identity = log_reader._file_identity(stream)
    raw = path.read_bytes()
    pin = log_reader.BridgePrefixPin(log_reader.BridgeCursor(len(raw), identity, "synthetic-v1"),
                                     hashlib.sha256(raw).hexdigest())
    return path, generation, pin


def observe(log, budget, watermark=None):
    path, generation, pin = log
    return guard.check_snapshot_position(enabled=True, log_path=str(path), generation_path=str(generation),
                                         watermark=pin if watermark is None else watermark,
                                         max_prefix_bytes=1024 * 1024, max_lag_bytes=budget)


def two_instants(log, budget, between=lambda: None, watermark=None):
    pre = observe(log, budget, watermark)
    between()
    return compose(pre, observe(log, budget, watermark))


def append(path, data):
    with path.open("ab") as stream:
        stream.write(data)


def test_unchanged_log_is_bounded_at_lag_zero_and_still_claims_no_freshness(log):
    bound = two_instants(log, 0)
    assert bound == {"status": "BOUNDED_BY_LOG_POSITION", "observations": 2, "lag_bucket": 0,
                     "freshness": "UNKNOWN", "source_authentication": "UNKNOWN", "coverage": "UNKNOWN"}
    assert frozenset(bound) == BOUND_KEYS and "lag_bytes" not in bound  # coarse bucket only, no exact count


def test_complete_records_appended_between_instants_report_only_the_coarse_bucket(log):
    bound = two_instants(log, 4096, between=lambda: append(log[0], RECORD * 2))
    assert bound["status"] == "BOUNDED_BY_LOG_POSITION" and bound["lag_bucket"] == 4096


def test_the_largest_bucket_is_reached_and_not_exceeded(log):
    big = b'{"event":"' + b"x" * 1000 + b'"}\n'
    bound = two_instants(log, 65536, between=lambda: append(log[0], big * 5))  # about 5 KB, above 4096
    assert bound["status"] == "BOUNDED_BY_LOG_POSITION" and bound["lag_bucket"] == 65536
    assert two_instants(log, 65536, between=lambda: append(log[0], big * 66)) == {"status": "UNKNOWN"}  # > 64 KiB


def test_append_beyond_the_budget_is_unknown_not_a_larger_bound(log):
    assert two_instants(log, 0, between=lambda: append(log[0], RECORD)) == {"status": "UNKNOWN"}
    assert two_instants(log, 4096, between=lambda: append(log[0], RECORD * 400)) == {"status": "UNKNOWN"}


def test_partial_record_appended_between_instants_is_unknown(log):
    assert two_instants(log, 4096, between=lambda: append(log[0], b'{"event":"half')) == {"status": "UNKNOWN"}


def test_suffix_shrinking_between_instants_is_unknown(log):
    path = log[0]
    append(path, RECORD * 2)
    pinned = path.read_bytes()[:log[2].cursor.offset]
    assert two_instants(log, 4096, between=lambda: path.write_bytes(pinned + RECORD)) == {"status": "UNKNOWN"}


@pytest.mark.parametrize("change", ["truncate_prefix", "rewrite_prefix", "replace_file", "generation"])
def test_any_change_to_the_pinned_prefix_or_its_identity_between_instants_is_unknown(log, change):
    path, generation, pin = log
    original = path.read_bytes()

    def mutate():
        if change == "truncate_prefix":
            path.write_bytes(original[:-1])
        elif change == "rewrite_prefix":
            path.write_bytes(b"x" + original[1:])
        elif change == "replace_file":
            path.rename(path.with_name("events.rotated.jsonl"))  # keep the old inode alive
            path.write_bytes(original)
            with path.open("rb") as stream:
                assert log_reader._file_identity(stream) != pin.cursor.file_identity
        else:
            generation.write_text(json.dumps({"generation": "synthetic-v2"}), encoding="utf-8")
    assert two_instants(log, 4096, between=mutate) == {"status": "UNKNOWN"}


def test_a_refused_first_instant_is_never_upgraded_by_a_later_one(log):
    path, _, pin = log
    assert compose({"status": "UNKNOWN"}, observe(log, 0)) == {"status": "UNKNOWN"}
    assert compose(observe(log, 0), {"status": "UNKNOWN"}) == {"status": "UNKNOWN"}
    assert two_instants(log, 0, watermark=replace(pin, sha256="0" * 64)) == {"status": "UNKNOWN"}


@pytest.mark.parametrize("rewrite", [b'{"event":"rewritten"}\n',
                                     b'{"event":"a-different-and-longer-suffix"}\n' * 3])
def test_limitation_any_non_shrinking_rewrite_of_the_unpinned_suffix_is_invisible(log, rewrite):
    """Position, not content: the suffix is counted, never hashed, so a rewrite that does not shrink it stays bounded."""
    path = log[0]
    append(path, RECORD)
    assert len(rewrite) >= len(RECORD)
    pre = observe(log, 4096)
    path.write_bytes(path.read_bytes()[:-len(RECORD)] + rewrite)  # the first instant's suffix no longer exists
    post = observe(log, 4096)
    assert post["status"] == "BOUNDED_BY_LOG_POSITION" and post["lag_bytes"] == len(rewrite)
    bound = compose(pre, post)
    assert bound["status"] == "BOUNDED_BY_LOG_POSITION" and bound["freshness"] == "UNKNOWN"


def test_every_observation_rehashes_the_whole_prefix_and_the_ceiling_refuses(log, monkeypatch):
    path, generation, pin = log
    calls = []
    real = guard.read_bridge_log

    def counting(*args, **kwargs):
        calls.append((kwargs["cursor"].offset, kwargs["expected_prefix_sha256"]))
        return real(*args, **kwargs)
    monkeypatch.setattr(guard, "read_bridge_log", counting)
    assert two_instants(log, 0)["status"] == "BOUNDED_BY_LOG_POSITION"
    assert calls == [(pin.cursor.offset, pin.sha256)] * 2  # two full prefix reads per bound, no cache
    assert log_reader.MAX_PREFIX_BYTES == 1024 * 1024 * 1024
    over = guard.check_snapshot_position(enabled=True, log_path=str(path), generation_path=str(generation),
                                         watermark=pin, max_prefix_bytes=pin.cursor.offset - 1, max_lag_bytes=0)
    assert over == {"status": "UNKNOWN"}  # a prefix above the configured ceiling is never bounded


def test_gap_the_window_after_the_second_instant_is_unbound(log):
    path = log[0]
    bound = two_instants(log, 0)
    path.write_bytes(b"x" + path.read_bytes()[1:])  # prefix rewritten right after the second instant
    assert observe(log, 0) == {"status": "UNKNOWN"}  # only a new observation would notice
    assert bound["status"] == "BOUNDED_BY_LOG_POSITION"  # the composed bound is a past statement, not a lease


def test_gap_any_valid_prefix_pin_is_bounded_so_snapshot_binding_is_the_callers_duty(log):
    path, _, pin = log
    raw = path.read_bytes()
    older = log_reader.BridgePrefixPin(
        log_reader.BridgeCursor(len(RECORD), pin.cursor.file_identity, pin.cursor.generation),
        hashlib.sha256(raw[:len(RECORD)]).hexdigest())
    bound = two_instants(log, 4096, watermark=older)
    assert bound["status"] == "BOUNDED_BY_LOG_POSITION" and bound["lag_bucket"] == 4096


def withheld_envelope():
    """The real caller _output on one synthetic validated ANSWER (same shape as the on-demand freshness tests)."""
    binding = {key: "a" * 64 for key in CONTENT_BINDING_KEYS}
    items = [{"ref_sha256": "b" * 64, "doc_id_sha256": "c" * 64, "stored_text": "synthetic-public-result"}]
    answer = {"schema": "wd.q4-lane-answer.v1", "lane": "fable-5", "status": "ANSWER", "reason": "",
              "result_count": 1, "results": items, "binding": binding, "trust": dict(caller._TRUST)}
    value = {"schema": "wd.q4-lane-caller-result.v1", "status": "VALIDATED", "lane": "fable-5",
             "lane_binding": "DECLARED_CONSISTENT", "lane_authentication": "UNKNOWN", "cleanup": "chain_confirmed",
             "inner_status": "ANSWER", "result_count": 1, "answer": answer,
             "answer_sha256": caller._sha(caller._canon(answer)), "identity_sha256": "d" * 64,
             "descriptor_sha256": "e" * 64, "call_id": "f" * 32}
    raw = caller._output(value, "fable-5", "d" * 64, "e" * 64, binding, 65536, "synthetic-owner-token-never-output")
    return json.loads(raw)


SHAPE_CHECK = re.compile(
    r"(\$fields = @\(.*?\)\s*\n"
    r"\s*\$names = @\(\$result\.PSObject\.Properties \| ForEach-Object \{ \$_\.Name \}\)\s*\n"
    r"\s*if \(\$names\.Count -ne \$fields\.Count\) \{ throw 'result_shape' \}\s*\n"
    r"\s*foreach \(\$key in \$fields\) \{ if \(\$names -cnotcontains \$key\) \{ throw 'result_shape' \} \})",
    re.S)
SHELL = shutil.which("pwsh") or shutil.which("powershell")


def _launcher_shape_check(relative):
    """The launcher's own key-count and containment lines for the withheld envelope, copied verbatim."""
    text = (ROOT / relative).read_text(encoding="utf-8")
    start = text.index("-ceq 'wd.q4-lane-content-withheld.v1'")
    found = SHAPE_CHECK.search(text, start)
    assert found is not None and found.start() - start < 2000  # the check right after the schema branch
    return found.group(1)


def _launcher_fields(relative):
    block = re.search(r"\$fields = @\((.*?)\)", _launcher_shape_check(relative), re.S).group(1)
    return frozenset(re.findall(r"'([a-z0-9_]+)'", block))


def _run_shape_check(relative, document, tmp_path):
    script = tmp_path / "shape_check.ps1"
    script.write_text("$ErrorActionPreference = 'Stop'\n"
                      "$result = [Console]::In.ReadToEnd().Trim() | ConvertFrom-Json\n"
                      + _launcher_shape_check(relative) + "\n'shape_ok'\n", encoding="utf-8")
    done = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-File", str(script)],
                          input=json.dumps(document), capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout.strip(), done.stderr


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_blocker_launchers_pin_the_exact_withheld_keys(launcher):
    envelope = withheld_envelope()
    assert envelope["status"] == "CONTENT_WITHHELD" and "stored_text" not in json.dumps(envelope)
    assert frozenset(envelope) == _launcher_fields(launcher)


@pytest.mark.skipif(SHELL is None, reason="PowerShell required to execute the launcher shape check")
@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_blocker_the_launcher_shape_check_refuses_appended_or_substituted_evidence(launcher, tmp_path):
    envelope = withheld_envelope()
    assert _run_shape_check(launcher, envelope, tmp_path)[:2] == (0, "shape_ok")  # positive control
    appended = dict(envelope, position_bound={"status": "BOUNDED_BY_LOG_POSITION", "lag_bucket": 0})
    code, out, err = _run_shape_check(launcher, appended, tmp_path)
    assert code != 0 and "shape_ok" not in out and "result_shape" in err  # key count differs
    substituted = dict(appended)
    del substituted["original_result_refs"]
    code, out, err = _run_shape_check(launcher, substituted, tmp_path)
    assert code != 0 and "shape_ok" not in out and "result_shape" in err  # same count, a required key missing


def test_blocker_content_source_prefix_has_no_file_identity(log):
    path, generation, pin = log
    assert cm.PREFIX_KEYS == frozenset({"sha256", "offset", "generation"})
    source_prefix = {"sha256": pin.sha256, "offset": pin.cursor.offset, "generation": pin.cursor.generation}
    without_identity = log_reader.BridgePrefixPin(
        log_reader.BridgeCursor(source_prefix["offset"], "", source_prefix["generation"]), source_prefix["sha256"])
    assert two_instants(log, 0, watermark=without_identity) == {"status": "UNKNOWN"}
    # Substituting the identity observed now would accept a same-bytes replacement the recorded identity refuses.
    original = path.read_bytes()
    path.rename(path.with_name("events.rotated.jsonl"))
    path.write_bytes(original)
    with path.open("rb") as stream:
        observed_now = log_reader._file_identity(stream)
    assert two_instants(log, 0) == {"status": "UNKNOWN"}
    weakened = replace(without_identity, cursor=replace(without_identity.cursor, file_identity=observed_now))
    assert two_instants(log, 0, watermark=weakened)["status"] == "BOUNDED_BY_LOG_POSITION"
