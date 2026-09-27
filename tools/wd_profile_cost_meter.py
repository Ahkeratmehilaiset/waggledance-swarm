#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Measured cost per lane profile from local session logs (lane profile switching PR-13).

Read-only and advisory: nothing here switches, launches or writes anything but its
report. Only token counts, model and effort ids, lane names and timestamps leave a
log; prompt and response content is never read into the report.

Sources
-------
- Claude Code transcripts (``$CLAUDE_CONFIG_DIR/projects/*/*.jsonl``) and their
  subagent transcripts (``projects/*/<session>/subagents/*.jsonl``): every
  assistant turn carries ``message.usage``, ``message.model`` and the session's
  ``effort``. One API response is written as several lines sharing
  ``message.id``, so turns are deduplicated by (session, message id). Subagent
  turns are real API calls and count. ``<synthetic>`` turns are not. The lane is
  the session transcript's ``agent-name`` or ``custom-title`` record; a subagent
  transcript has neither and takes the lane of its session transcript. The report
  gives each lane's last recorded turn, so a lane whose transcript stopped growing
  shows up instead of silently reading as idle.
- Codex rollouts (``$CODEX_HOME/sessions/**/rollout-*.jsonl``): ``turn_context``
  gives the model and effort, and ``token_count`` events give the session's
  cumulative ``total_token_usage``. A turn's usage is the growth of that total
  (repeated events add nothing, and a total that falls starts a new base). The
  ``rate_limits`` of each event are also Codex pool samples. A snapshot names its
  limit bucket in ``limit_id``: the main ``codex`` bucket (or no id) gives the
  ``primary`` and ``secondary`` pools, and any other bucket (``premium`` has been
  seen) is kept as a pool of its own, ``<limit_id>:<window>``, never mixed into
  the main one. The lane is the fleet manifest's lane whose ``worktree`` is
  ``session_meta.cwd``, else the lane id that starts the cwd's directory name.
- The capacity observer store (the PR-5 pacer's ``read_samples``): the Claude and
  Codex pool percentages. A sample whose ``limit_id`` is neither empty nor the
  provider's own name is kept as its own ``<limit_id>:<window>`` pool too.

Weighted tokens
---------------
Pools are not billed per token, so the meter weights each token class by its API
price ratio as a proxy (``WEIGHTS``). The pool attribution then measures how many
pool percentage points one million weighted tokens actually cost. The weights are
an assumption that the measurement tests; they are not a fact.

Lane anatomy
------------
Every tool call is one API request that reads the whole context again, so a
lane's cost is roughly its request count times its context size. Per lane the
report gives the request count, the context tokens per request (50th and 90th
percentile and the largest), the share of its weighted tokens that were cache
reads, and the share of requests that wrote fewer than ``SMALL_OUTPUT_TOKENS``
output tokens (bookkeeping steps). It shows where a context window or batching
change would pay off.

Pool attribution
----------------
Consecutive pool samples of one window instance (same ``resets_at``) form a
segment. A segment's growth, together with the weighted tokens of that provider
inside the segment, adds to the pool-wide estimate. That includes segments that did
not grow: pool percentages are integers, and counting only the steps would bias the
estimate upwards. A segment where one profile has at least ``DOMINANCE`` of the
tokens also adds to that profile's own estimate. A segment that grew with no local
tokens is ``unexplained`` (use outside the swarm). A segment that fell (a reset or
an anomaly) is skipped.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.lane_profile_catalog import LANES  # noqa: E402

SCHEMA = "wd.profile-cost.v1"
WEIGHTS = {
    "claude": {"input": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0},
    "codex": {"input": 1.0, "cache_write": 1.0, "cached_input": 0.1, "output": 8.0},
}
DOMINANCE = 0.8
MAX_LINE_BYTES = 8 * 1024 * 1024
ACTIVITY_BIN_MINUTES = 5
SMALL_OUTPUT_TOKENS = 300
CACHE_READ_KINDS = ("cache_read", "cached_input")
LOW_PRECISION_POINTS = 3.0       # below this many observed pool points an estimate is low precision
MAIN_CODEX_BUCKETS = (None, "codex")
DEFAULT_FLEET_MANIFEST = Path(__file__).resolve().parents[1] / "ops" / "windows" / "reboot" / "wd-fleet.json"
_LANE_PREFIX = re.compile(r"^(" + "|".join(re.escape(lane) for lane in LANES) + r")(?:-|$)")


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _records(path: Path, stats: dict) -> Iterable[dict]:
    """JSON objects of one JSONL file; oversized or malformed lines are counted, never raised."""
    try:
        stream = path.open("rb")
    except OSError:
        stats["unreadable_files"] += 1
        return
    with stream:
        for raw in stream:
            if len(raw) > MAX_LINE_BYTES:
                stats["unreadable_lines"] += 1
                continue
            try:
                record = json.loads(raw)
            except (ValueError, RecursionError):
                stats["unreadable_lines"] += 1
                continue
            if isinstance(record, dict):
                yield record


def _recent_files(paths: Iterable[Path], since: datetime) -> list[Path]:
    recent = []
    for path in paths:
        try:
            if path.is_file() and not path.is_symlink() and \
                    datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) >= since:
                recent.append(path)
        except OSError:
            continue
    return sorted(recent)


def _new_stats() -> dict:
    return {"files": 0, "unreadable_files": 0, "unreadable_lines": 0, "duplicates": 0,
            "turns_without_lane": 0, "ambiguous_lane_files": 0, "last_turn_by_lane": {}}


def _note_last_turn(stats: dict, turn: dict) -> None:
    lane = turn["lane"] or "unattributed"
    last = stats["last_turn_by_lane"].get(lane)
    if last is None or turn["ts"] > last:
        stats["last_turn_by_lane"][lane] = turn["ts"]


def _lane_names(record: dict) -> set[str]:
    kind = record.get("type")
    if kind == "agent-name" and record.get("agentName") in LANES:
        return {record["agentName"]}
    if kind == "custom-title" and record.get("customTitle") in LANES:
        return {record["customTitle"]}
    return set()


def _single_lane(lanes: set[str], stats: dict) -> str | None:
    if len(lanes) > 1:
        stats["ambiguous_lane_files"] += 1
    return next(iter(lanes)) if len(lanes) == 1 else None


def _session_lane(path: Path, stats: dict) -> str | None:
    """The lane named in a session transcript that is not itself being read for turns."""
    lanes: set[str] = set()
    for record in _records(path, stats):
        lanes |= _lane_names(record)
    return _single_lane(lanes, stats)


def claude_turns(projects_root: Path, since: datetime) -> tuple[list[dict], dict]:
    """Deduplicated Claude assistant turns since ``since``, with their lane when known."""
    stats = _new_stats()
    turns: list[dict] = []
    seen: set[tuple] = set()
    session_lanes: dict[Path, str | None] = {}
    sessions = _recent_files(projects_root.glob("*/*.jsonl"), since)
    subagents = _recent_files(projects_root.glob("*/*/subagents/*.jsonl"), since)
    for path in sessions + subagents:
        stats["files"] += 1
        is_subagent = path.parent.name == "subagents"
        lanes: set[str] = set()
        file_turns: list[dict] = []
        for record in _records(path, stats):
            kind = record.get("type")
            lanes |= _lane_names(record)
            if kind != "assistant":
                continue
            message = record.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            model = message.get("model") if isinstance(message, dict) else None
            if not isinstance(usage, dict) or not isinstance(model, str) or model == "<synthetic>":
                continue
            ts = _utc(record.get("timestamp"))
            if ts is None or ts < since:
                continue
            session = record.get("sessionId") if isinstance(record.get("sessionId"), str) else \
                (path.parents[1].name if is_subagent else path.stem)
            ident = message.get("id") or record.get("requestId") or record.get("uuid")
            key = (session, ident if isinstance(ident, str) else id(record))
            if key in seen:
                stats["duplicates"] += 1
                continue
            seen.add(key)
            effort = record.get("effort")
            file_turns.append({
                "provider": "claude", "session": session, "ts": ts, "model": model,
                "effort": effort if isinstance(effort, str) and effort else "unknown",
                "sidechain": is_subagent or record.get("isSidechain") is True,
                "tokens": {"input": _count(usage.get("input_tokens")),
                           "cache_write": _count(usage.get("cache_creation_input_tokens")),
                           "cache_read": _count(usage.get("cache_read_input_tokens")),
                           "output": _count(usage.get("output_tokens"))}})
        if is_subagent and not lanes:
            parent = path.parents[2] / f"{path.parents[1].name}.jsonl"
            if parent not in session_lanes:
                session_lanes[parent] = _session_lane(parent, stats) if parent.is_file() else None
            lane = session_lanes[parent]
        else:
            lane = _single_lane(lanes, stats)
            if not is_subagent:
                session_lanes[path] = lane
        for turn in file_turns:
            turn["lane"] = lane
            if lane is None:
                stats["turns_without_lane"] += 1
            _note_last_turn(stats, turn)
        turns += file_turns
    return turns, stats


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.strip())).rstrip("\\/")


def fleet_worktrees(manifest: Path | None) -> dict[str, str]:
    """Normalized worktree path -> lane, from the fleet manifest's ``lanes`` (empty when unreadable)."""
    if manifest is None:
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    lanes = data.get("lanes") if isinstance(data, dict) else None
    worktrees: dict[str, str] = {}
    for entry in lanes if isinstance(lanes, list) else []:
        if isinstance(entry, dict) and entry.get("agent") in LANES and isinstance(entry.get("worktree"), str) \
                and entry["worktree"].strip():
            worktrees[_path_key(entry["worktree"])] = entry["agent"]
    return worktrees


def lane_from_cwd(cwd: Any, worktrees: dict[str, str] | None = None) -> str | None:
    """The fleet lane whose worktree is ``cwd``, else the lane id that starts its directory name."""
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    if worktrees and _path_key(cwd) in worktrees:
        return worktrees[_path_key(cwd)]
    match = _LANE_PREFIX.match(Path(cwd.strip().rstrip("\\/")).name)
    return match.group(1) if match else None


def _codex_samples(limits: dict, ts: datetime) -> list[dict]:
    bucket = limits.get("limit_id")
    if bucket is not None and not isinstance(bucket, str):
        return []
    samples = []
    for window in ("primary", "secondary"):
        snapshot = limits.get(window)
        if not isinstance(snapshot, dict):
            continue
        used, reset = snapshot.get("used_percent"), snapshot.get("resets_at")
        if type(used) in (int, float) and type(reset) in (int, float) and used >= 0:
            name = window if bucket in MAIN_CODEX_BUCKETS else f"{bucket}:{window}"
            samples.append({"provider": "codex", "window": name, "used_percent": float(used),
                            "resets_at": float(reset), "observed_at": ts})
    return samples


def codex_turns(sessions_root: Path, since: datetime, worktrees: dict[str, str] | None = None
                ) -> tuple[list[dict], list[dict], dict]:
    """(turns, pool samples, stats) from Codex rollouts since ``since``."""
    stats = _new_stats()
    turns: list[dict] = []
    samples: list[dict] = []
    for path in _recent_files(sessions_root.glob("**/rollout-*.jsonl"), since):
        stats["files"] += 1
        lane, session, model, effort, previous = None, path.stem, None, None, None
        for record in _records(path, stats):
            kind = record.get("type")
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            if kind == "session_meta":
                lane = lane_from_cwd(payload.get("cwd"), worktrees)
                if isinstance(payload.get("id"), str):
                    session = payload["id"]
            elif kind == "turn_context":
                model = payload.get("model") if isinstance(payload.get("model"), str) else model
                raw_effort = payload.get("effort") or payload.get("reasoning_effort")
                effort = raw_effort if isinstance(raw_effort, str) and raw_effort else effort
            elif kind == "event_msg" and payload.get("type") == "token_count":
                ts = _utc(record.get("timestamp"))
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                total = info.get("total_token_usage")
                limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
                if ts is not None and ts >= since:
                    samples += _codex_samples(limits, ts)
                if not isinstance(total, dict):
                    continue
                current = {key: _count(total.get(key)) for key in
                           ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")}
                if previous is None or any(current[k] < previous[k] for k in current):
                    delta = dict(current)            # first total, or a new base after a fall
                else:
                    delta = {k: current[k] - previous[k] for k in current}
                previous = current
                if not any(delta.values()):
                    stats["duplicates"] += 1
                    continue
                if ts is None or ts < since:
                    continue
                uncached = max(delta["input_tokens"] - delta["cached_input_tokens"] - delta["cache_write_input_tokens"], 0)
                turn = {"provider": "codex", "session": session, "ts": ts, "lane": lane,
                        "model": model or "unknown", "effort": effort or "unknown", "sidechain": False,
                        "tokens": {"input": uncached, "cache_write": delta["cache_write_input_tokens"],
                                   "cached_input": delta["cached_input_tokens"], "output": delta["output_tokens"]}}
                if lane is None:
                    stats["turns_without_lane"] += 1
                _note_last_turn(stats, turn)
                turns.append(turn)
    return turns, samples, stats


def weighted(turn: dict) -> float:
    weights = WEIGHTS[turn["provider"]]
    return sum(weights.get(kind, 0.0) * count for kind, count in turn["tokens"].items())


def _profile_key(turn: dict) -> str:
    return f"{turn['provider']}:{turn['model']}:{turn['effort']}"


def profile_rollup(turns: list[dict]) -> dict:
    """Per profile and lane: turns, token classes, weighted tokens and weighted tokens per active hour."""
    table: dict[str, dict] = {}
    for turn in turns:
        key = _profile_key(turn)
        row = table.setdefault(key, {"provider": turn["provider"], "model": turn["model"], "effort": turn["effort"],
                                     "turns": 0, "sidechain_turns": 0, "tokens": {}, "weighted_tokens": 0.0,
                                     "lanes": {}, "_bins": set()})
        row["turns"] += 1
        row["sidechain_turns"] += 1 if turn["sidechain"] else 0
        for kind, count in turn["tokens"].items():
            row["tokens"][kind] = row["tokens"].get(kind, 0) + count
        row["weighted_tokens"] += weighted(turn)
        lane = turn["lane"] or "unattributed"
        row["lanes"][lane] = row["lanes"].get(lane, 0.0) + weighted(turn)
        row["_bins"].add(int(turn["ts"].timestamp() // (ACTIVITY_BIN_MINUTES * 60)))
    for row in table.values():
        hours = len(row.pop("_bins")) * ACTIVITY_BIN_MINUTES / 60
        row["active_hours"] = round(hours, 3)
        row["weighted_tokens_per_active_hour"] = round(row["weighted_tokens"] / hours, 1) if hours else None
        row["weighted_tokens"] = round(row["weighted_tokens"], 1)
        row["lanes"] = {lane: round(value, 1) for lane, value in sorted(row["lanes"].items())}
    return dict(sorted(table.items()))


def _nearest_rank(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def lane_anatomy(turns: list[dict]) -> dict:
    """Per provider and lane: requests, context tokens per request, cache-read and small-output shares."""
    rows: dict[str, dict] = {}
    for turn in turns:
        key = f"{turn['provider']}:{turn['lane'] or 'unattributed'}"
        row = rows.setdefault(key, {"contexts": [], "weighted": 0.0, "cache_weighted": 0.0, "small": 0})
        tokens = turn["tokens"]
        row["contexts"].append(sum(tokens.get(kind, 0) for kind in ("input", "cache_write", *CACHE_READ_KINDS)))
        row["weighted"] += weighted(turn)
        weights = WEIGHTS[turn["provider"]]
        row["cache_weighted"] += sum(weights.get(kind, 0.0) * tokens.get(kind, 0) for kind in CACHE_READ_KINDS)
        row["small"] += 1 if tokens.get("output", 0) < SMALL_OUTPUT_TOKENS else 0
    table = {}
    for key, row in sorted(rows.items()):
        requests = len(row["contexts"])
        table[key] = {"requests": requests,
                      "context_tokens": {"p50": _nearest_rank(row["contexts"], 0.5),
                                         "p90": _nearest_rank(row["contexts"], 0.9), "max": max(row["contexts"])},
                      "weighted_tokens": round(row["weighted"], 1),
                      "cache_read_share": round(row["cache_weighted"] / row["weighted"], 4) if row["weighted"] else None,
                      "small_output_share": round(row["small"] / requests, 4)}
    return table


def _segments(samples: list[dict]) -> list[tuple[str, datetime, datetime, float]]:
    """(pool, start, end, growth) for consecutive samples of one window instance."""
    pools: dict[tuple, list[dict]] = {}
    for sample in samples:
        pools.setdefault((sample["provider"], sample["window"], sample["resets_at"]), []).append(sample)
    segments = []
    for (provider, window, _), rows in pools.items():
        rows = sorted(rows, key=lambda row: row["observed_at"])
        for first, second in zip(rows, rows[1:]):
            if second["observed_at"] > first["observed_at"]:
                segments.append((f"{provider}/{window}", first["observed_at"], second["observed_at"],
                                 second["used_percent"] - first["used_percent"]))
    return segments


def attribute(turns: list[dict], samples: list[dict], *, dominance: float = DOMINANCE) -> dict:
    """Pool percentage points per million weighted tokens, pool-wide and per dominant profile."""
    by_provider: dict[str, list[dict]] = {}
    for turn in turns:
        by_provider.setdefault(turn["provider"], []).append(turn)
    for rows in by_provider.values():
        rows.sort(key=lambda turn: turn["ts"])
    pools: dict[str, dict] = {}
    for pool, start, end, growth in _segments(samples):
        entry = pools.setdefault(pool, {"segments": 0, "points": 0.0, "weighted_tokens": 0.0,
                                        "skipped_falling": 0, "unexplained_points": 0.0, "profiles": {}})
        if growth < 0:
            entry["skipped_falling"] += 1
            continue
        provider = pool.split("/", 1)[0]
        inside = [t for t in by_provider.get(provider, []) if start < t["ts"] <= end]
        per_profile: dict[str, float] = {}
        for turn in inside:
            per_profile[_profile_key(turn)] = per_profile.get(_profile_key(turn), 0.0) + weighted(turn)
        total = sum(per_profile.values())
        if total <= 0:
            entry["unexplained_points"] += growth
            continue
        entry["segments"] += 1
        entry["points"] += growth
        entry["weighted_tokens"] += total
        top, top_tokens = max(per_profile.items(), key=lambda item: item[1])
        if top_tokens / total >= dominance:
            profile = entry["profiles"].setdefault(top, {"segments": 0, "points": 0.0, "weighted_tokens": 0.0})
            profile["segments"] += 1
            profile["points"] += growth
            profile["weighted_tokens"] += top_tokens
    for entry in pools.values():
        for target in (entry, *entry["profiles"].values()):
            tokens = target["weighted_tokens"]
            target["points_per_million_weighted_tokens"] = round(target["points"] * 1e6 / tokens, 4) if tokens else None
            target["low_precision"] = target["points"] < LOW_PRECISION_POINTS
            target["weighted_tokens"] = round(tokens, 1)
    return dict(sorted(pools.items()))


def _observer_window(sample: dict) -> str:
    window = sample.get("window") or "unknown"
    bucket = sample.get("limit_id")
    return window if bucket in (None, "", sample.get("provider")) else f"{bucket}:{window}"


def report(*, claude_root: Path | None, codex_root: Path | None, store: Path | None, since: datetime,
           now: datetime, fleet_manifest: Path | None = None) -> dict:
    turns: list[dict] = []
    samples: list[dict] = []
    sources: dict[str, Any] = {}
    if claude_root is not None:
        found, stats = claude_turns(claude_root, since)
        turns += found
        sources["claude"] = dict(stats, root=str(claude_root), turns=len(found))
    if codex_root is not None:
        found, codex_samples, stats = codex_turns(codex_root, since, fleet_worktrees(fleet_manifest))
        turns += found
        samples += codex_samples
        sources["codex"] = dict(stats, root=str(codex_root), turns=len(found), pool_samples=len(codex_samples))
    if store is not None:
        try:
            from tools.wd_capacity_pacing import read_samples
            observed = [s for s in read_samples(store) if s["observed_at"] >= since]
            samples += [dict(s, window=_observer_window(s)) for s in observed]
            sources["observer"] = {"store": str(store), "pool_samples": len(observed)}
        except (OSError, ValueError) as exc:
            sources["observer"] = {"store": str(store), "error": exc.__class__.__name__}
    return {"schema": SCHEMA, "execution_allowed": False, "since": since.isoformat(), "generated_at": now.isoformat(),
            "weights": WEIGHTS, "dominance": DOMINANCE, "sources": sources,
            "profiles": profile_rollup(turns), "lanes": lane_anatomy(turns), "pools": attribute(turns, samples),
            "limitations": ["weights_are_api_price_ratio_proxies_measured_by_the_pool_attribution",
                            "integer_pool_percentages_need_several_points_for_precision",
                            "grok_is_measured_separately_by_the_grok_calibration_log",
                            "claude_effort_is_the_session_effort_recorded_per_turn"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    claude_home = Path(os.path.abspath(os.environ["CLAUDE_CONFIG_DIR"])) if os.environ.get("CLAUDE_CONFIG_DIR", "").strip() \
        else Path.home() / ".claude"
    codex_home = Path(os.path.abspath(os.environ["CODEX_HOME"])) if os.environ.get("CODEX_HOME", "").strip() \
        else Path.home() / ".codex"
    parser.add_argument("--claude-projects", default=str(claude_home / "projects"))
    parser.add_argument("--codex-sessions", default=str(codex_home / "sessions"))
    parser.add_argument("--store", default=None, help="capacity observer store (observations.sqlite)")
    parser.add_argument("--fleet-manifest", default=str(DEFAULT_FLEET_MANIFEST),
                        help="wd-fleet.json whose lanes map worktrees to lanes ('' to use directory names only)")
    parser.add_argument("--hours", type=float, default=48.0)
    args = parser.parse_args(argv)
    if not 0 < args.hours <= 24 * 31:
        print(json.dumps({"schema": SCHEMA, "error": "hours must be in (0, 744]", "execution_allowed": False}))
        return 2
    now = datetime.now(timezone.utc)
    result = report(claude_root=Path(args.claude_projects), codex_root=Path(args.codex_sessions),
                    store=Path(args.store) if args.store else None, since=now - timedelta(hours=args.hours), now=now,
                    fleet_manifest=Path(args.fleet_manifest) if args.fleet_manifest.strip() else None)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
