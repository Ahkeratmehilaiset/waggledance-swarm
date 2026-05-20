# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tools.agent_next_task import (
    SUBSTRATE_SMOKE_CANDIDATES,
    _pick_substrate_smoke,
    evaluate_agent_next_task,
    main,
)
from waggledance.core.work_queue import claim_task


NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)


def _events_file(bridge_root: Path, events: list[dict]) -> Path:
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return events_path


def _claims_dir(bridge_root: Path) -> Path:
    claims = bridge_root / "work_queue" / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    return claims


def _empty_bridge(tmp_path: Path) -> tuple[Path, Path, Path]:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            }
        ],
    )
    # Ensure the canonical claims dir under bridge_root exists so list_claims
    # finds an empty (not missing) directory.
    claims_dir = _claims_dir(bridge)
    return bridge, events_path, claims_dir


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def test_invalid_agent_id_is_rejected(tmp_path: Path) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)

    report = evaluate_agent_next_task(
        agent="Invalid Agent!",
        events_path=events_path,

        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "agent_invalid"
    assert report["next_action"] == "operator_handles"
    assert report["exit_code"] == 2


# ---------------------------------------------------------------------------
# defer-to-bridge-next-action branches
# ---------------------------------------------------------------------------


def test_defers_when_agent_has_active_claim(tmp_path: Path) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    claim_task(
        agent="claude",
        task_id="claude-already-running",
        summary="active work in progress",
        mode="write",
        write_scope=["tools/foo.py"],
        bridge_root=bridge,
    )

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,

        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["next_action"] == "follow_bridge_recommendation"
    assert report["bridge_recommendation"]["action"] == "continue_claim"
    assert report["bridge_recommendation"]["task_id"] == "claude-already-running"


def test_defers_when_open_incoming_request(tmp_path: Path) -> None:
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            },
            {
                "ts_utc": "2026-05-20T11:30:00Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "task_id": "important-request",
                "status": "request",
                "message": "please review my latest design proposal end to end",
            },
        ],
    )
    claims_dir = _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,

        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["bridge_recommendation"]["action"] == "answer_incoming"


# ---------------------------------------------------------------------------
# substrate-smoke pick branches
# ---------------------------------------------------------------------------


def test_picks_substrate_smoke_when_bridge_says_claim_unblocked_work(
    tmp_path: Path,
) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,

        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["next_action"] == "claim_and_run"
    assert report["underlying_bridge_action"] == "claim_unblocked_work"
    candidate = report["candidate"]
    assert candidate["kind"] == "run_substrate_smoke"
    assert candidate["mode"] == "read-only"
    assert candidate["write_scope"] == []
    assert candidate["target"] in {
        entry["target"] for entry in SUBSTRATE_SMOKE_CANDIDATES
    }
    assert candidate["task_id_suggestion"].startswith("claude-substrate-smoke-")
    assert "pytest" in candidate["recommended_command"]


def test_picks_substrate_smoke_when_bridge_says_parallel_read_only(
    tmp_path: Path,
) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    claim_task(
        agent="codex",
        task_id="codex-busy-write",
        summary="other agent owns write scope",
        mode="write",
        write_scope=["tools/bar.py"],
        bridge_root=bridge,
    )

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,

        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "claim_substrate_smoke"
    assert report["underlying_bridge_action"] == "parallel_read_only"


# ---------------------------------------------------------------------------
# rotation determinism
# ---------------------------------------------------------------------------


def test_same_agent_same_day_picks_same_candidate() -> None:
    first = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    second = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    assert first == second


def test_different_agents_get_different_salts() -> None:
    claude = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    codex = _pick_substrate_smoke(agent="codex", now_utc=NOW)
    # salts differ because "claude" and "codex" hash to different ord-sums
    assert claude["rotation"]["agent_salt"] != codex["rotation"]["agent_salt"]


def test_rotation_advances_with_day_of_year() -> None:
    later_day = datetime(2026, 5, 21, 12, 0, 0, tzinfo=timezone.utc)
    today_pick = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    tomorrow_pick = _pick_substrate_smoke(agent="claude", now_utc=later_day)
    assert (
        tomorrow_pick["rotation"]["index"]
        == (today_pick["rotation"]["index"] + 1) % len(SUBSTRATE_SMOKE_CANDIDATES)
    )


def test_rotation_wraps_around_pool() -> None:
    pool_size = len(SUBSTRATE_SMOKE_CANDIDATES)
    today = _pick_substrate_smoke(agent="claude", now_utc=NOW)
    wrap_day = datetime(
        NOW.year, NOW.month, NOW.day, tzinfo=timezone.utc
    ).replace(
        year=NOW.year + (NOW.timetuple().tm_yday + pool_size) // 366
    )
    # simpler: bump by exactly pool_size days using ordinal
    from datetime import timedelta

    wrap_day = NOW + timedelta(days=pool_size)
    wrap_pick = _pick_substrate_smoke(agent="claude", now_utc=wrap_day)
    assert wrap_pick["rotation"]["index"] == today["rotation"]["index"]


def test_candidates_pool_is_nonempty_and_each_entry_is_well_formed() -> None:
    assert SUBSTRATE_SMOKE_CANDIDATES
    for entry in SUBSTRATE_SMOKE_CANDIDATES:
        assert entry["target"]
        assert entry["rationale"]


# ---------------------------------------------------------------------------
# read-only contract
# ---------------------------------------------------------------------------


def test_tick_does_not_write_bridge_events_or_claims(tmp_path: Path) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    events_before = events_path.read_text(encoding="utf-8")
    claims_before = sorted(p.name for p in claims_dir.iterdir())

    evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,

        bridge_root=bridge,
        now_utc=NOW,
    )

    assert events_path.read_text(encoding="utf-8") == events_before
    assert sorted(p.name for p in claims_dir.iterdir()) == claims_before


def test_evaluation_is_deterministic_for_identical_inputs(tmp_path: Path) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)
    first = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,

        bridge_root=bridge,
        now_utc=NOW,
    )
    second = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,

        bridge_root=bridge,
        now_utc=NOW,
    )
    assert first["decision"] == second["decision"]
    assert first["candidate"] == second["candidate"]


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_main_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    bridge, events_path, claims_dir = _empty_bridge(tmp_path)

    exit_code = main(
        [
            "--agent",
            "claude",
            "--events",
            str(events_path),
            "--bridge-root",
            str(bridge),
            "--now",
            "2026-05-20T12:00:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["decision"] == "claim_substrate_smoke"
    assert parsed["candidate"]["kind"] == "run_substrate_smoke"


# ---------------------------------------------------------------------------
# regression: bridge_root is the authoritative source for claim loading
# ---------------------------------------------------------------------------


def test_bridge_root_is_authoritative_for_claims_not_some_external_dir(
    tmp_path: Path,
) -> None:
    """The tool MUST honor the active claim under bridge_root/work_queue/claims.

    Earlier the CLI accepted a separate ``--claims-dir`` argument that the
    evaluator silently ignored, so a wrapper that pointed ``claims_dir`` at the
    real bridge state while passing an empty ``bridge_root`` would receive a
    bogus ``claim_substrate_smoke`` recommendation instead of
    ``continue_claim``. This regression locks down the single-source contract:
    claims are loaded only from ``bridge_root/work_queue/claims``.
    """
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            }
        ],
    )
    # An orphan claims directory that does NOT live under bridge_root.
    # Earlier code would have happily consulted it; the contract is now to
    # ignore it entirely.
    orphan_claims = tmp_path / "orphan_claims"
    orphan_claims.mkdir()
    (orphan_claims / "claude-shadow-claim.json").write_text(
        json.dumps(
            {
                "task_id": "claude-shadow-claim",
                "agent": "claude",
                "summary": "shadow claim that lives outside bridge_root",
                "mode": "write",
                "write_scope": ["tools/x.py"],
                "claimed_at_utc": "2026-05-20T11:00:00Z",
                "last_heartbeat_utc": "2026-05-20T11:59:00Z",
                "lease_seconds": 900,
                "run_id": "claude-test",
                "pid": 0,
                "cwd": "",
                "git_branch": "main",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    # bridge_root has no real claim — the canonical claims dir is empty.
    _claims_dir(bridge)

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    # Because the active claim lives outside bridge_root, the tool must NOT
    # see it; the bridge state is "no active claim" and the recommendation
    # is to pick a substrate-smoke candidate. The earlier bug would have
    # returned defer_to_bridge_next_action / continue_claim by reading the
    # orphan directory through a separate --claims-dir parameter.
    assert report["decision"] == "claim_substrate_smoke"
    assert report["bridge_recommendation"]["active_claim_count"] == 0


def test_active_claim_under_bridge_root_yields_continue_claim_regardless_of_external_dirs(
    tmp_path: Path,
) -> None:
    """Mirror of the regression above with the situation reversed.

    When the real active claim DOES live under ``bridge_root``, the tool
    must defer to ``continue_claim`` even if an unrelated directory tree
    happens to exist nearby. This proves the bridge_root path is read
    correctly and that no external state can shadow it.
    """
    bridge = tmp_path / ".agent-bridge"
    events_path = _events_file(
        bridge,
        [
            {
                "ts_utc": "2026-01-01T00:00:00Z",
                "agent": "codex",
                "type": "heartbeat",
                "task_id": "baseline",
                "status": "active",
                "message": "background heartbeat",
            }
        ],
    )
    _claims_dir(bridge)
    claim_task(
        agent="claude",
        task_id="claude-real-bridge-root-claim",
        summary="canonical claim under bridge_root",
        mode="write",
        write_scope=["tools/y.py"],
        bridge_root=bridge,
    )
    # An unrelated directory tree that should be ignored.
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()

    report = evaluate_agent_next_task(
        agent="claude",
        events_path=events_path,
        bridge_root=bridge,
        now_utc=NOW,
    )

    assert report["decision"] == "defer_to_bridge_next_action"
    assert report["bridge_recommendation"]["action"] == "continue_claim"
    assert (
        report["bridge_recommendation"]["task_id"]
        == "claude-real-bridge-root-claim"
    )
