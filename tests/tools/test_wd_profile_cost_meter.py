# SPDX-License-Identifier: BUSL-1.1
"""Profile cost meter (lane profile switching PR-13): measured cost per lane profile, read-only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path

import pytest

import tools.wd_profile_cost_meter as meter
from tools.wd_profile_cost_meter import (attribute, claude_turns, codex_turns, fleet_worktrees, lane_from_cwd, main,
                                         profile_rollup, weighted)

NOW = datetime.now(timezone.utc).replace(microsecond=0)
SINCE = NOW - timedelta(hours=24)
RESET = (NOW + timedelta(hours=100)).timestamp()


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def write_jsonl(path: Path, records: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records), encoding="utf-8")
    return path


def assistant(minutes_ago, message_id, *, model="claude-opus-5-5", effort="high", usage=(1, 20, 300, 4),
              session="s1", sidechain=False):
    record = {"type": "assistant", "timestamp": iso(NOW - timedelta(minutes=minutes_ago)), "effort": effort,
              "isSidechain": sidechain,
              "message": {"id": message_id, "model": model,
                          "usage": {"input_tokens": usage[0], "cache_creation_input_tokens": usage[1],
                                    "cache_read_input_tokens": usage[2], "output_tokens": usage[3]}}}
    if session is not None:
        record["sessionId"] = session
    return record


def named(lane):
    return [{"type": "agent-name", "agentName": lane}, {"type": "custom-title", "customTitle": lane}]


# --- Claude transcripts ------------------------------------------------------------------------------


def test_claude_turns_dedupe_one_response_written_as_several_lines(tmp_path):
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("fable-5") + [
        assistant(10, "m1"), assistant(10, "m1"), assistant(10, "m1"),   # one API response, three content lines
        assistant(5, "m2", usage=(2, 0, 100, 50))])
    turns, stats = claude_turns(tmp_path, SINCE)
    assert [t["tokens"] for t in turns] == [{"input": 1, "cache_write": 20, "cache_read": 300, "output": 4},
                                            {"input": 2, "cache_write": 0, "cache_read": 100, "output": 50}]
    assert stats["duplicates"] == 2
    assert {t["lane"] for t in turns} == {"fable-5"}
    assert stats["last_turn_by_lane"] == {"fable-5": NOW - timedelta(minutes=5)}


def test_claude_turns_skip_synthetic_old_and_usage_less_turns(tmp_path):
    no_usage = assistant(3, "m4")
    del no_usage["message"]["usage"]
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("fable-5") + [
        assistant(10, "m1", model="<synthetic>"), assistant(60 * 30, "m2"), assistant(5, "m3"), no_usage,
        {"type": "user", "timestamp": iso(NOW), "message": {"content": "hi"}}])
    turns, _ = claude_turns(tmp_path, SINCE)
    assert [t["model"] for t in turns] == ["claude-opus-5-5"]
    assert len(turns) == 1


def test_claude_lane_is_none_when_the_transcript_names_two_lanes(tmp_path):
    write_jsonl(tmp_path / "p" / "s1.jsonl",
                [{"type": "agent-name", "agentName": "fable-5"}, {"type": "agent-name", "agentName": "claude-rco-1"},
                 assistant(5, "m1")])
    turns, stats = claude_turns(tmp_path, SINCE)
    assert turns[0]["lane"] is None
    assert stats["ambiguous_lane_files"] == 1 and stats["turns_without_lane"] == 1
    assert stats["last_turn_by_lane"] == {"unattributed": NOW - timedelta(minutes=5)}


def test_claude_lane_ignores_names_that_are_not_lanes(tmp_path):
    write_jsonl(tmp_path / "p" / "s1.jsonl", [{"type": "agent-name", "agentName": "someone-else"},
                                              {"type": "custom-title", "customTitle": "claude-rco-2"},
                                              assistant(5, "m1")])
    turns, _ = claude_turns(tmp_path, SINCE)
    assert turns[0]["lane"] == "claude-rco-2"


def test_subagent_transcripts_count_and_take_the_session_lane(tmp_path):
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("claude-rco-1") + [assistant(9, "m1")])
    write_jsonl(tmp_path / "p" / "s1" / "subagents" / "agent-a.jsonl",
                [assistant(8, "sub1", model="claude-haiku-4-5-20251001", sidechain=True)])
    turns, stats = claude_turns(tmp_path, SINCE)
    sub = [t for t in turns if t["session"] == "s1" and t["model"].startswith("claude-haiku")]
    assert len(sub) == 1 and sub[0]["lane"] == "claude-rco-1" and sub[0]["sidechain"] is True
    assert stats["files"] == 2 and stats["turns_without_lane"] == 0


def test_a_subagent_of_an_old_session_transcript_still_finds_its_lane(tmp_path):
    parent = write_jsonl(tmp_path / "p" / "s1.jsonl", named("claude-rco-2") + [assistant(60 * 40, "m1")])
    old = (NOW - timedelta(hours=40)).timestamp()
    os.utime(parent, (old, old))                       # not a recent file, so not read for turns
    write_jsonl(tmp_path / "p" / "s1" / "subagents" / "agent-a.jsonl", [assistant(8, "sub1", session=None)])
    turns, stats = claude_turns(tmp_path, SINCE)
    assert [(t["lane"], t["session"], t["sidechain"]) for t in turns] == [("claude-rco-2", "s1", True)]
    assert stats["files"] == 1


def test_a_subagent_without_a_session_transcript_is_unattributed(tmp_path):
    write_jsonl(tmp_path / "p" / "gone" / "subagents" / "agent-a.jsonl", [assistant(8, "sub1")])
    turns, stats = claude_turns(tmp_path, SINCE)
    assert turns[0]["lane"] is None and stats["turns_without_lane"] == 1


def test_a_subagent_naming_its_own_lane_keeps_it(tmp_path):
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("fable-5") + [assistant(9, "m1")])
    write_jsonl(tmp_path / "p" / "s1" / "subagents" / "agent-a.jsonl",
                named("claude-rco-1") + [assistant(8, "sub1")])
    turns, _ = claude_turns(tmp_path, SINCE)
    assert {t["lane"] for t in turns} == {"fable-5", "claude-rco-1"}


def test_malformed_and_oversized_lines_are_counted_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(meter, "MAX_LINE_BYTES", 400)
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("fable-5") + [
        "{not json", "[1, 2]", '"text"', json.dumps({"type": "assistant", "pad": "x" * 500}), assistant(5, "m1")])
    turns, stats = claude_turns(tmp_path, SINCE)
    assert len(turns) == 1
    assert stats["unreadable_lines"] == 2          # the broken line and the oversized one; valid non-objects skip


def test_negative_bool_and_float_counts_read_as_zero(tmp_path):
    record = assistant(5, "m1")
    record["message"]["usage"].update(input_tokens=-3, output_tokens=True, cache_read_input_tokens=2.5)
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("fable-5") + [record])
    turns, _ = claude_turns(tmp_path, SINCE)
    assert turns[0]["tokens"] == {"input": 0, "cache_write": 20, "cache_read": 0, "output": 0}


# --- lane mapping ------------------------------------------------------------------------------------


def manifest(tmp_path, lanes) -> Path:
    path = tmp_path / "wd-fleet.json"
    path.write_text(json.dumps({"lanes": lanes}), encoding="utf-8")
    return path


def test_fleet_manifest_maps_the_exact_worktree_to_its_lane(tmp_path):
    lead = str(tmp_path / "project2")
    worktrees = fleet_worktrees(manifest(tmp_path, [
        {"agent": "codex-lead-1", "worktree": lead}, {"agent": "stranger", "worktree": str(tmp_path / "x")},
        {"agent": "fable-5", "worktree": "   "}, {"agent": "claude-rco-1"}, "not an entry"]))
    assert list(worktrees.values()) == ["codex-lead-1"]
    assert lane_from_cwd(lead, worktrees) == "codex-lead-1"
    assert lane_from_cwd(lead + os.sep, worktrees) == "codex-lead-1"
    assert lane_from_cwd(lead, {}) is None                     # the directory name alone names no lane
    assert lane_from_cwd(str(tmp_path / "project2x"), worktrees) is None


def test_fleet_manifest_mapping_wins_over_the_directory_prefix(tmp_path):
    odd = str(tmp_path / "fable-5-borrowed")
    worktrees = fleet_worktrees(manifest(tmp_path, [{"agent": "claude-rco-1", "worktree": odd}]))
    assert lane_from_cwd(odd, worktrees) == "claude-rco-1"
    assert lane_from_cwd(odd, {}) == "fable-5"


@pytest.mark.parametrize("content", ["{broken", "[]", json.dumps({"lanes": {"a": 1}}), json.dumps({"x": 1})])
def test_an_unusable_fleet_manifest_maps_nothing(tmp_path, content):
    path = tmp_path / "wd-fleet.json"
    path.write_text(content, encoding="utf-8")
    assert fleet_worktrees(path) == {}
    assert fleet_worktrees(tmp_path / "missing.json") == {}
    assert fleet_worktrees(None) == {}


@pytest.mark.parametrize("cwd,lane", [("/w/codex-tools-1-post-reboot", "codex-tools-1"), ("/w/codex-tools-1", "codex-tools-1"),
                                      ("/w/codex-tools-1x", None), ("/w/fable-5-hex/", "fable-5"), ("", None),
                                      ("   ", None), (None, None), (7, None)])
def test_directory_prefix_fallback(cwd, lane):
    assert lane_from_cwd(cwd) == lane


# --- Codex rollouts ----------------------------------------------------------------------------------


def token_count(minutes_ago, total, *, limits=None):
    payload = {"type": "token_count", "info": {"total_token_usage": total}}
    if limits is not None:
        payload["rate_limits"] = limits
    return {"type": "event_msg", "timestamp": iso(NOW - timedelta(minutes=minutes_ago)), "payload": payload}


def total(inp, cached, out, write=0):
    return {"input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": write,
            "output_tokens": out}


def rollout(tmp_path, name, records, cwd="/w/codex-tools-1-main"):
    head = [{"type": "session_meta", "payload": {"id": name, "cwd": cwd}},
            {"type": "turn_context", "payload": {"model": "gpt-6-sol", "effort": "high"}}]
    return write_jsonl(tmp_path / "sessions" / "2026" / f"rollout-{name}.jsonl", head + records)


def test_codex_turns_are_the_growth_of_the_cumulative_total(tmp_path):
    rollout(tmp_path, "r1", [
        token_count(30, total(1000, 800, 50)),
        token_count(29, total(1000, 800, 50)),                  # repeated event: nothing new
        token_count(20, total(1600, 1300, 90, write=100)),
        token_count(10, total(200, 100, 10)),                   # fell: a new base, not a negative turn
    ])
    turns, _, stats = codex_turns(tmp_path / "sessions", SINCE)
    assert [t["tokens"] for t in turns] == [
        {"input": 200, "cache_write": 0, "cached_input": 800, "output": 50},
        {"input": 0, "cache_write": 100, "cached_input": 500, "output": 40},
        {"input": 100, "cache_write": 0, "cached_input": 100, "output": 10}]
    assert stats["duplicates"] == 1
    assert {(t["lane"], t["model"], t["effort"], t["session"]) for t in turns} == {("codex-tools-1", "gpt-6-sol", "high", "r1")}


def test_codex_turns_before_since_set_the_base_but_do_not_count(tmp_path):
    rollout(tmp_path, "r1", [token_count(60 * 30, total(5000, 4000, 100)), token_count(10, total(5600, 4500, 130))])
    turns, _, _ = codex_turns(tmp_path / "sessions", SINCE)
    assert [t["tokens"] for t in turns] == [{"input": 100, "cache_write": 0, "cached_input": 500, "output": 30}]


def test_codex_lane_comes_from_the_fleet_manifest(tmp_path):
    lead = str(tmp_path / "project2")
    rollout(tmp_path, "r1", [token_count(10, total(10, 0, 1))], cwd=lead)
    turns, _, stats = codex_turns(tmp_path / "sessions", SINCE, {os.path.normcase(os.path.normpath(lead)): "codex-lead-1"})
    assert turns[0]["lane"] == "codex-lead-1"
    turns, _, stats = codex_turns(tmp_path / "sessions", SINCE)
    assert turns[0]["lane"] is None and stats["turns_without_lane"] == 1


def window(used, reset=RESET):
    return {"used_percent": used, "resets_at": reset, "window_minutes": 300}


def test_codex_limit_buckets_never_mix(tmp_path):
    rollout(tmp_path, "r1", [
        token_count(30, total(10, 0, 1), limits={"limit_id": "codex", "primary": window(10), "secondary": window(40)}),
        token_count(20, total(20, 0, 2), limits={"primary": window(11)}),                     # no id: the main bucket
        token_count(15, total(30, 0, 3), limits={"limit_id": "premium", "primary": window(70), "secondary": None}),
        token_count(12, total(40, 0, 4), limits={"limit_id": 5, "primary": window(99)}),      # not a bucket name
        token_count(11, total(50, 0, 5), limits={"primary": window(-1)}),
        token_count(10, total(60, 0, 6), limits={"primary": window(True)}),
        token_count(9, total(70, 0, 7), limits={"primary": {"used_percent": 12}}),
        token_count(60 * 30, total(5, 0, 1), limits={"primary": window(1)}),                  # too old
    ])
    _, samples, _ = codex_turns(tmp_path / "sessions", SINCE)
    assert sorted((s["window"], s["used_percent"]) for s in samples) == [
        ("premium:primary", 70.0), ("primary", 10.0), ("primary", 11.0), ("secondary", 40.0)]


# --- rollup and attribution ------------------------------------------------------------------------------


def turn(provider, model, minutes_ago, tokens, effort="high", lane="codex-tools-1"):
    return {"provider": provider, "model": model, "effort": effort, "ts": NOW - timedelta(minutes=minutes_ago),
            "lane": lane, "sidechain": False, "session": "s", "tokens": tokens}


def pool_sample(minutes_ago, used, reset=RESET, window_name="primary"):
    return {"provider": "codex", "window": window_name, "used_percent": float(used), "resets_at": reset,
            "observed_at": NOW - timedelta(minutes=minutes_ago)}


def test_weighted_tokens_use_the_provider_weights():
    assert weighted(turn("codex", "m", 1, {"input": 10, "cache_write": 2, "cached_input": 100, "output": 1})) == 30.0
    assert weighted(turn("claude", "m", 1, {"input": 10, "cache_write": 4, "cache_read": 100, "output": 1})) == 30.0


def test_profile_rollup_counts_active_hours_by_five_minute_bins():
    rows = profile_rollup([turn("codex", "a", 1, {"output": 1}), turn("codex", "a", 2, {"output": 1}),
                           turn("codex", "a", 40, {"output": 1}, lane=None)])
    row = rows["codex:a:high"]
    assert row["turns"] == 3 and row["weighted_tokens"] == 24.0
    assert row["active_hours"] in (round(2 * 5 / 60, 3), round(3 * 5 / 60, 3))   # 1 and 2 min ago may straddle a bin
    assert row["lanes"] == {"codex-tools-1": 16.0, "unattributed": 8.0}


def test_attribution_credits_a_dominant_profile_and_the_pool():
    a = {"output": 125_000}           # 1M weighted tokens
    turns = [turn("codex", "a", 55, a), turn("codex", "a", 45, a), turn("codex", "b", 44, {"output": 12_500}),
             turn("codex", "a", 35, a), turn("codex", "b", 34, a)]
    samples = [pool_sample(60, 10), pool_sample(50, 12), pool_sample(40, 13), pool_sample(30, 15),
               pool_sample(20, 16), pool_sample(10, 16), pool_sample(5, 3)]
    pools = attribute(turns, samples)["codex/primary"]
    # 60..50: a alone, +2. 50..40: a 1M and b 0.1M (a 91%), +1. 30..40: a and b split evenly, +2.
    # 20..30: no tokens, +1 unexplained. 10..20: no tokens, +0. 5..10: fell, skipped.
    assert pools["segments"] == 3 and pools["points"] == 5.0
    assert pools["weighted_tokens"] == pytest.approx(4.1e6)
    assert pools["unexplained_points"] == 1.0 and pools["skipped_falling"] == 1
    assert pools["profiles"] == {"codex:a:high": {"segments": 2, "points": 3.0, "weighted_tokens": 2e6,
                                                   "points_per_million_weighted_tokens": 1.5, "low_precision": False}}
    assert pools["points_per_million_weighted_tokens"] == round(5 / 4.1, 4)


def test_a_segment_with_tokens_but_no_growth_still_counts_its_tokens():
    a = {"output": 125_000}
    turns = [turn("codex", "a", 55, a), turn("codex", "a", 45, a)]
    samples = [pool_sample(60, 10), pool_sample(50, 10), pool_sample(40, 11)]
    pools = attribute(turns, samples)["codex/primary"]
    assert pools["segments"] == 2 and pools["points"] == 1.0 and pools["points_per_million_weighted_tokens"] == 0.5
    assert pools["low_precision"] is True


def test_different_window_instances_are_never_one_segment():
    samples = [pool_sample(60, 90, reset=RESET - 3600), pool_sample(50, 2, reset=RESET)]
    assert attribute([turn("codex", "a", 55, {"output": 1})], samples) == {}


def test_a_jittering_resets_at_is_one_window_instance():
    # claude-rco-2 on #1747: real Codex rollouts report one instance's resets_at up to 13 s apart,
    # interleaved in time; split into pieces, the overlapping segments counted tokens twice.
    a = {"output": 125_000}
    turns = [turn("codex", "a", 55, a), turn("codex", "a", 45, a), turn("codex", "a", 35, a)]
    samples = [pool_sample(60, 10), pool_sample(50, 11, reset=RESET + 13), pool_sample(40, 12),
               pool_sample(30, 13, reset=RESET + 13)]
    pools = attribute(turns, samples)["codex/primary"]
    # Split by resets_at, (60,40] and (50,30] overlap: 4 points and the 45-minute turn twice (4M).
    assert pools["segments"] == 3 and pools["points"] == 3.0
    assert pools["weighted_tokens"] == pytest.approx(3e6)             # each turn once
    assert pools["points_per_million_weighted_tokens"] == 1.0


def test_resets_at_further_apart_than_the_jitter_are_separate_instances():
    samples = [pool_sample(60, 90, reset=RESET - 301), pool_sample(50, 2, reset=RESET)]
    assert attribute([turn("codex", "a", 55, {"output": 1})], samples) == {}
    samples = [pool_sample(60, 1, reset=RESET - 300), pool_sample(50, 2, reset=RESET)]     # the same-bound twin
    assert attribute([turn("codex", "a", 55, {"output": 125_000})], samples)["codex/primary"]["points"] == 1.0


def test_a_turn_at_a_sample_time_belongs_to_the_segment_that_sample_ends():
    samples = [pool_sample(60, 10), pool_sample(50, 12), pool_sample(40, 13)]    # the two segments grow unequally
    pools = attribute([turn("codex", "a", 50, {"output": 125_000})], samples)["codex/primary"]
    assert pools["segments"] == 1 and pools["points"] == 2.0 and pools["unexplained_points"] == 1.0


def test_claude_tokens_never_explain_codex_pool_growth():
    samples = [pool_sample(60, 10), pool_sample(50, 11)]
    pools = attribute([turn("claude", "c", 55, {"output": 1000})], samples)["codex/primary"]
    assert pools["segments"] == 0 and pools["unexplained_points"] == 1.0


def test_observer_samples_keep_foreign_limit_buckets_apart():
    assert meter._observer_window({"provider": "claude", "limit_id": "claude", "window": "seven_day"}) == "seven_day"
    assert meter._observer_window({"provider": "claude", "window": None}) == "unknown"
    assert meter._observer_window({"provider": "claude", "limit_id": "", "window": "five_hour"}) == "five_hour"
    assert meter._observer_window({"provider": "codex", "limit_id": "premium", "window": "primary"}) == "premium:primary"


# --- CLI -------------------------------------------------------------------------------------------------


def test_main_reports_without_executing(tmp_path, capsys):
    write_jsonl(tmp_path / "claude" / "p" / "s1.jsonl", named("fable-5") + [assistant(5, "m1")])
    rollout(tmp_path, "r1", [token_count(10, total(10, 0, 1), limits={"primary": window(3)})])
    assert main(["--claude-projects", str(tmp_path / "claude"), "--codex-sessions", str(tmp_path / "sessions"),
                 "--hours", "24", "--fleet-manifest", ""]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == "wd.profile-cost.v1" and out["execution_allowed"] is False
    assert out["sources"]["claude"]["turns"] == 1 and out["sources"]["codex"]["pool_samples"] == 1
    assert set(out["profiles"]) == {"claude:claude-opus-5-5:high", "codex:gpt-6-sol:high"}
    assert out["sources"]["claude"]["last_turn_by_lane"]["fable-5"].startswith(str((NOW - timedelta(minutes=5)).date()))


@pytest.mark.parametrize("hours", ["0", "-1", "745"])
def test_main_refuses_an_out_of_range_window(hours, capsys, tmp_path):
    assert main(["--claude-projects", str(tmp_path), "--codex-sessions", str(tmp_path), "--hours", hours]) == 2
    assert json.loads(capsys.readouterr().out)["execution_allowed"] is False


def test_main_accepts_the_longest_window(tmp_path, capsys):
    assert main(["--claude-projects", str(tmp_path), "--codex-sessions", str(tmp_path), "--hours", "744",
                 "--fleet-manifest", ""]) == 0


def test_the_default_fleet_manifest_is_the_tracked_one():
    assert meter.DEFAULT_FLEET_MANIFEST == Path(meter.__file__).resolve().parents[1] / "ops" / "windows" / "reboot" / "wd-fleet.json"
    assert "codex-lead-1" in fleet_worktrees(meter.DEFAULT_FLEET_MANIFEST).values()


# --- lane anatomy ----------------------------------------------------------------------------------------


def test_lane_anatomy_gives_context_percentiles_and_shares():
    turns = [turn("claude", "c", minute, {"input": 0, "cache_write": 0, "cache_read": context, "output": out},
                  lane="fable-5")
             for minute, (context, out) in enumerate([(100, 10), (200, 10), (300, 10), (400, 10), (500, 10),
                                                      (600, 10), (700, 10), (800, 10), (900, 10), (1000, 5000)])]
    row = meter.lane_anatomy(turns)["claude:fable-5"]
    assert row["requests"] == 10
    assert row["context_tokens"] == {"p50": 500, "p90": 900, "max": 1000}
    cache = 0.1 * sum(range(100, 1001, 100))
    total = cache + 5.0 * (9 * 10 + 5000)
    assert row["weighted_tokens"] == round(total, 1)
    assert row["cache_read_share"] == round(cache / total, 4)
    assert row["small_output_share"] == 0.9                     # 299 is small, 300 is not: see the boundary test


def test_small_output_boundary_and_codex_context_counts_every_input_kind():
    turns = [turn("codex", "x", 3, {"input": 10, "cache_write": 5, "cached_input": 85, "output": 299}),
             turn("codex", "x", 2, {"input": 10, "cache_write": 0, "cached_input": 0, "output": 300}, lane=None)]
    table = meter.lane_anatomy(turns)
    assert table["codex:codex-tools-1"]["context_tokens"] == {"p50": 100, "p90": 100, "max": 100}
    assert table["codex:codex-tools-1"]["small_output_share"] == 1.0
    assert table["codex:unattributed"]["small_output_share"] == 0.0
    assert table["codex:codex-tools-1"]["cache_read_share"] == round(8.5 / (10 + 5 + 8.5 + 8 * 299), 4)


def test_lane_anatomy_of_a_zero_token_lane_has_no_share():
    row = meter.lane_anatomy([turn("codex", "x", 1, {"input": 0, "output": 0})])["codex:codex-tools-1"]
    assert row["cache_read_share"] is None and row["weighted_tokens"] == 0.0
    assert meter.lane_anatomy([]) == {}


# --- review round 1 (codex-tools-1 on 7c5a153f) -------------------------------------------------------------


def test_rows_without_any_id_are_distinct_turns(tmp_path):
    # B1: 40 id-less rows were collapsed to 4 when id(record) was reused after each record was freed.
    rows = []
    for minute in range(40):
        row = assistant(minute + 1, None)
        rows.append(row)
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("fable-5") + rows)
    write_jsonl(tmp_path / "p" / "s2.jsonl", named("fable-5") + [assistant(3, None)])   # same line number, other file
    turns, stats = claude_turns(tmp_path, SINCE)
    assert len(turns) == 41 and stats["duplicates"] == 0


def test_a_row_with_only_a_request_id_is_deduplicated_by_it(tmp_path):
    first, second = assistant(5, None), assistant(5, None)
    first["requestId"] = second["requestId"] = "req_1"
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("fable-5") + [first, second])
    turns, stats = claude_turns(tmp_path, SINCE)
    assert len(turns) == 1 and stats["duplicates"] == 1


def test_the_report_carries_no_path(tmp_path, capsys):
    # B2: the docs promise counts, ids, names and timestamps only.
    write_jsonl(tmp_path / "claude" / "p" / "s1.jsonl", named("fable-5") + [assistant(5, "m1")])
    rollout(tmp_path, "r1", [token_count(10, total(10, 0, 1), limits={"primary": window(3)})])
    assert main(["--claude-projects", str(tmp_path / "claude"), "--codex-sessions", str(tmp_path / "sessions"),
                 "--store", str(tmp_path / "missing.sqlite"), "--hours", "24", "--fleet-manifest", ""]) == 0
    text = capsys.readouterr().out
    report = json.loads(text)
    assert report["sources"]["observer"] == {"error": "FileNotFoundError"}
    for fragment in (str(tmp_path), tmp_path.name, "missing.sqlite", "rollout-r1", "s1.jsonl"):
        assert fragment not in text


def test_a_stopped_transcript_still_shows_its_last_turn(tmp_path):
    # B3: a lane whose transcript was last written before the window must not vanish.
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("claude-rco-1") + [assistant(60 * 30, "old"), assistant(60 * 31, "older")])
    turns, stats = claude_turns(tmp_path, SINCE)
    assert turns == []
    assert stats["last_turn_by_lane"] == {"claude-rco-1": NOW - timedelta(minutes=60 * 30)}


def test_a_stopped_rollout_still_shows_its_last_turn(tmp_path):
    rollout(tmp_path, "r1", [token_count(60 * 30, total(10, 0, 1)), token_count(60 * 29, total(10, 0, 1))])
    turns, _, stats = codex_turns(tmp_path / "sessions", SINCE)
    assert turns == [] and stats["last_turn_by_lane"] == {"codex-tools-1": NOW - timedelta(minutes=60 * 30)}


def test_a_lane_without_any_recorded_turn_is_listed(tmp_path, capsys):
    write_jsonl(tmp_path / "claude" / "p" / "s1.jsonl", named("fable-5") + [assistant(5, "m1")])
    stale = write_jsonl(tmp_path / "claude" / "p" / "s2.jsonl", named("claude-rco-2") + [assistant(60 * 40, "m2")])
    old = (NOW - timedelta(hours=40)).timestamp()
    os.utime(stale, (old, old))                        # not written during the window, so not scanned
    assert main(["--claude-projects", str(tmp_path / "claude"), "--codex-sessions", str(tmp_path / "none"),
                 "--hours", "24", "--fleet-manifest", ""]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["sources"]["lanes_without_recorded_turns"] == ["claude-rco-1", "claude-rco-2", "codex-lead-1",
                                                                 "codex-tools-1"]


def test_a_symlinked_session_transcript_does_not_name_a_subagent_lane(tmp_path, monkeypatch):
    # S1: the parent fallback keeps the same symlink guard as every scanned file.
    parent = write_jsonl(tmp_path / "p" / "s1.jsonl", named("claude-rco-2") + [assistant(60 * 40, "m1")])
    old = (NOW - timedelta(hours=40)).timestamp()
    os.utime(parent, (old, old))
    write_jsonl(tmp_path / "p" / "s1" / "subagents" / "agent-a.jsonl", [assistant(8, "sub1")])
    real = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == parent or real(self))
    turns, _ = claude_turns(tmp_path, SINCE)
    assert [t["lane"] for t in turns] == [None]


def test_the_last_turn_is_the_newest_across_a_lanes_transcripts(tmp_path):
    write_jsonl(tmp_path / "p" / "s1.jsonl", named("fable-5") + [assistant(30, "m1")])     # read first, older
    write_jsonl(tmp_path / "p" / "s2.jsonl", named("fable-5") + [assistant(5, "m2")])
    _, stats = claude_turns(tmp_path, SINCE)
    assert stats["last_turn_by_lane"] == {"fable-5": NOW - timedelta(minutes=5)}


def test_a_stale_lower_view_from_another_session_never_recounts_growth():
    # claude-rco-1 on #1747: Lead and Tools each report the same pool; a session's view can lag.
    # 20 -> 19 (stale) -> 20 -> 21 is one point of growth, not two, and each turn counts once.
    a = {"output": 125_000}
    turns = [turn("codex", "a", 55, a), turn("codex", "a", 45, a), turn("codex", "a", 35, a)]
    samples = [pool_sample(60, 20), pool_sample(50, 19), pool_sample(40, 20), pool_sample(30, 21)]
    pools = attribute(turns, samples)["codex/primary"]
    assert pools["points"] == 1.0 and pools["skipped_falling"] == 1
    # (60,50] falls and is skipped; (60,40] grows 0 and holds the turns at 55 and 45; (40,30] grows 1.
    assert pools["segments"] == 2 and pools["weighted_tokens"] == pytest.approx(3e6)


def test_counted_growth_of_an_instance_equals_its_high_water_growth():
    samples = [pool_sample(90 - 5 * i, used) for i, used in enumerate([10, 12, 11, 12, 13, 12, 12, 15, 14, 16])]
    turns = [turn("codex", "a", 88 - 5 * i, {"output": 125_000}) for i in range(9)]
    pools = attribute(turns, samples)["codex/primary"]
    assert pools["points"] == 16 - 10
    assert pools["weighted_tokens"] == pytest.approx(9e6)           # every turn exactly once


def test_a_full_pool_says_nothing_about_cost_per_token():
    # The previous Codex window sat at 100% for a day while tokens were still spent (2026-09-25/26).
    a = {"output": 125_000}
    turns = [turn("codex", "a", 55, a), turn("codex", "a", 45, a), turn("codex", "a", 35, a)]
    samples = [pool_sample(60, 99), pool_sample(50, 100), pool_sample(40, 100), pool_sample(30, 100)]
    pools = attribute(turns, samples)["codex/primary"]
    assert pools["skipped_saturated"] == 2
    assert pools["segments"] == 1 and pools["points"] == 1.0 and pools["weighted_tokens"] == pytest.approx(1e6)
