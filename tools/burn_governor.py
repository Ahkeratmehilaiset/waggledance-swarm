#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Per-pool burn governor (RFC P3 / D8: no burn governance).

The codex pool exhausted by *burst-then-exhaust* — spending a scarce weekly
budget on low-value polling/loops until nothing remained for real work. This
module is the governance LOGIC the expensive loops should consult BEFORE an
LLM turn: given recent usage records and a per-pool budget, it returns a
``proceed`` / ``throttle`` / ``stop`` decision so a pool is paced to last its
window instead of bursting.

Pure + deterministic over its inputs (no clock, no I/O in the core); wiring it
to a live usage feed is a separate step. ``now`` is passed in.

WIRING GUARDRAIL (mandatory when this is hooked up): the governor is generic
per-pool and cannot tell a low-value poll from a safety-critical turn. It MUST
govern only DISCRETIONARY / low-value pool work (idle polling, speculative
slices). Safety-critical actions — RCO veto, ``build_consensus`` on a ready PR,
critical fixes, merges — MUST BYPASS the governor and are NEVER throttled or
stopped by budget, or a near-cap pool could wedge the gate (the very failure
this exists to prevent).

Decision policy (per pool, fail-safe):
* no/invalid cap configured  -> ``proceed`` ("ungoverned" — cannot pace without
  a cap; flagged so the absence is visible, never silently enforced);
* spend >= cap               -> ``stop``;
* fraction >= throttle_at OR projected to exhaust before the window resets at
  the current rate -> ``throttle``;
* otherwise                  -> ``proceed``.

    python tools/burn_governor.py --usage usage.jsonl --budgets budgets.json --now <epoch> --pool codex
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Mapping, Sequence

PROCEED = "proceed"
THROTTLE = "throttle"
STOP = "stop"
DEFAULT_THROTTLE_AT = 0.8


def _coerce_positive_number(value) -> float | None:
    """Return value as a positive float, or None if not a usable cap.

    Accepts int/float and clean numeric strings ("100"); rejects non-numeric
    strings, bools, None, and values <= 0 (caller treats None as a config error).
    """
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        try:
            n = float(value.strip())
        except ValueError:
            return None
        return n if n > 0 else None
    return None


def window_spend(
    records: Sequence[Mapping],
    pool: str,
    *,
    window_start: float,
    now: float,
) -> dict:
    """Sum output tokens (and count turns) for ``pool`` within (window_start, now].

    Records are dicts with ``pool``, ``ts`` (epoch seconds) and ``output_tokens``.
    Unparseable records are COUNTED in ``dropped`` and surfaced by the caller —
    NOT silently ignored: for a budget governor, dropping a real-token record
    lowers measured spend and could hide exhaustion (a fail-OPEN). The returned
    ``spend`` is therefore a LOWER BOUND when ``dropped > 0``, and the decision
    layer flags the data-quality gap so a caller can be conservative.

    ``dropped`` counts: non-Mapping records (uninterpretable), and this-pool
    in-scope records whose ``ts`` or ``output_tokens`` is missing/non-numeric.
    Records for a different pool are not "dropped" (they are simply out of scope).
    """
    spend = 0
    turns = 0
    dropped = 0
    for r in records:
        if not isinstance(r, Mapping):
            dropped += 1  # uninterpretable — cannot rule out this pool
            continue
        if str(r.get("pool", "")) != pool:
            continue  # other pool: out of scope, not a data-quality drop
        ts = r.get("ts")
        if not isinstance(ts, (int, float)):
            dropped += 1  # this-pool record with bad ts -> spend is incomplete
            continue
        if ts < window_start or ts > now:
            continue
        turns += 1
        tok = r.get("output_tokens")
        if isinstance(tok, (int, float)) and tok >= 0:
            spend += tok
        else:
            dropped += 1  # in-window turn with unknown tokens -> under-count risk
    return {"spend": spend, "turns": turns, "dropped": dropped}


def governor_decision(
    spend: float,
    cap: float | None,
    *,
    window_start: float,
    now: float,
    reset_ts: float | None,
    throttle_at: float = DEFAULT_THROTTLE_AT,
) -> dict:
    """Decide proceed/throttle/stop for one pool from its measured spend."""
    if cap is None:
        return {
            "decision": PROCEED,
            "governed": False,
            "fraction": None,
            "reason": "no cap configured (ungoverned)",
        }
    cap_num = _coerce_positive_number(cap)
    if cap_num is None:
        # cap is PRESENT but unusable (non-numeric string, <=0, etc.). This is a
        # config error, not an intentional "no cap" -> flag LOUDLY rather than
        # silently disabling governance for this pool.
        return {
            "decision": PROCEED,
            "governed": False,
            "fraction": None,
            "config_error": True,
            "reason": f"invalid cap config {cap!r} (governance DISABLED — fix config)",
        }
    cap = cap_num
    fraction = spend / cap
    result = {"governed": True, "fraction": round(fraction, 4)}
    if spend >= cap:
        result.update(decision=STOP, reason=f"cap reached ({spend}/{cap})")
        return result
    # Projected exhaustion at the current rate before the window resets.
    elapsed = now - window_start
    projected = None
    if reset_ts is not None and elapsed > 0 and spend > 0:
        rate = spend / elapsed  # tokens/sec so far this window
        remaining = cap - spend
        secs_to_cap = remaining / rate if rate > 0 else float("inf")
        projected = now + secs_to_cap
        if projected < reset_ts:
            result.update(
                decision=THROTTLE,
                projected_exhaustion_ts=round(projected, 3),
                reason=(
                    f"projected to exhaust at {round(projected,0)} before reset "
                    f"{round(reset_ts,0)} at current rate"
                ),
            )
            return result
    if fraction >= throttle_at:
        result.update(
            decision=THROTTLE,
            reason=f"fraction {round(fraction,3)} >= throttle_at {throttle_at}",
        )
        return result
    result.update(decision=PROCEED, reason=f"within budget ({round(fraction,3)})")
    if projected is not None:
        result["projected_exhaustion_ts"] = round(projected, 3)
    return result


def evaluate(
    records: Sequence[Mapping],
    budgets: Mapping[str, Mapping],
    *,
    now: float,
    throttle_at: float = DEFAULT_THROTTLE_AT,
) -> dict:
    """Return a decision per pool in ``budgets``.

    Each budget entry: {cap, window_seconds, reset_ts?}. ``window_start`` is
    ``now - window_seconds`` (or reset_ts - window_seconds if that is later).
    """
    out: dict[str, dict] = {}
    for pool, cfg in budgets.items():
        cfg = cfg if isinstance(cfg, Mapping) else {}
        cap = cfg.get("cap")
        window_seconds = cfg.get("window_seconds")
        reset_ts = cfg.get("reset_ts")
        if isinstance(window_seconds, (int, float)) and window_seconds > 0:
            window_start = now - window_seconds
        else:
            window_start = float("-inf")
        s = window_spend(records, pool, window_start=window_start, now=now)
        d = governor_decision(
            s["spend"], cap, window_start=window_start if window_start != float("-inf") else now,
            now=now, reset_ts=reset_ts if isinstance(reset_ts, (int, float)) else None,
            throttle_at=throttle_at,
        )
        d["spend"] = s["spend"]
        d["turns"] = s["turns"]
        d["dropped"] = s["dropped"]
        if s["dropped"] > 0:
            d["data_quality"] = (
                f"WARNING: {s['dropped']} usage record(s) unparseable for {pool} -> "
                "spend is a LOWER BOUND; governance may under-throttle (fail-open)"
            )
        out[pool] = d
    return out


def _load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip malformed; never crash the governor
    return rows


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--usage", required=True, help="JSONL of {pool, ts, output_tokens}")
    p.add_argument("--budgets", required=True, help="JSON {pool: {cap, window_seconds, reset_ts?}}")
    p.add_argument("--now", type=float, required=True, help="current epoch seconds")
    p.add_argument("--pool", help="optional: report only this pool")
    p.add_argument("--throttle-at", type=float, default=DEFAULT_THROTTLE_AT)
    p.add_argument("--json", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = _load_jsonl(args.usage)
    with open(args.budgets, encoding="utf-8") as fh:
        budgets = json.load(fh)
    result = evaluate(records, budgets, now=args.now, throttle_at=args.throttle_at)
    if args.pool:
        result = {args.pool: result.get(args.pool, {"decision": PROCEED, "governed": False,
                                                    "reason": "pool not in budgets (ungoverned)"})}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for pool, d in sorted(result.items()):
            print(f"{pool}: {d['decision'].upper()} ({d.get('reason','')})")
    # Exit non-zero if any pool says stop (so a caller can gate on $?).
    return 1 if any(d.get("decision") == STOP for d in result.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
