"""Evidence-only prototype: a two-instant log-position bound for a withheld Mind answer. Synthetic data only.

Nothing here is wired into the lane caller, the launchers, the writer or any live path, and no answer text is
ever released. The prototype composes two observations of the EXISTING guard
tools.wd_q4_snapshot_lag.check_snapshot_position (before the query and right before output) for one pinned
watermark. A bound is a statement about the observed log POSITION only:

* it is not authenticated freshness, source authentication, coverage, a clock or a lease;
* bytes appended after the watermark are counted but never bound by digest, so a same-length rewrite of the
  unpinned suffix between the two instants is invisible (pinned below as a known limitation);
* any refusal of either observation, or a shrinking suffix between them, is UNKNOWN, never a weaker bound.

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


def test_limitation_same_length_rewrite_of_the_unpinned_suffix_is_invisible(log):
    """Position, not content: the appended suffix is counted, never hashed, so this stays bounded."""
    path = log[0]
    append(path, RECORD)
    other = b'{"event":"rewritten"}\n'
    assert len(other) == len(RECORD)
    bound = two_instants(log, 4096, between=lambda: path.write_bytes(path.read_bytes()[:-len(RECORD)] + other))
    assert bound["status"] == "BOUNDED_BY_LOG_POSITION" and bound["freshness"] == "UNKNOWN"


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


def _launcher_fields(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")
    start = text.index("-ceq 'wd.q4-lane-content-withheld.v1'")
    block = re.search(r"\$fields = @\((.*?)\)", text[start:], re.S).group(1)
    return frozenset(re.findall(r"'([a-z0-9_]+)'", block))


@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_blocker_launchers_pin_the_exact_withheld_keys_so_evidence_cannot_be_appended(launcher):
    envelope = withheld_envelope()
    fields = _launcher_fields(launcher)
    assert envelope["status"] == "CONTENT_WITHHELD" and "stored_text" not in json.dumps(envelope)
    assert frozenset(envelope) == fields
    # Any added position field changes the key set the launcher compares, so the read would refuse.
    assert frozenset(envelope) | {"position_bound"} != fields


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
