from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.work_queue_sweep_stale as sweep_cli  # noqa: E402
from waggledance.core.work_queue import claim_task  # noqa: E402

_IDENTITY_ENV = (
    "AGENT_BRIDGE_AGENT",
    "AGENT_BRIDGE_OWNER_SESSION_ID",
    "AGENT_BRIDGE_OWNER_TOKEN",
    "AGENT_BRIDGE_RUN_ID",
    "AGENT_BRIDGE_OWNER_PID",
    "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
)


@pytest.fixture(autouse=True)
def _hermetic_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts identity-less and bound to no agent label.

    A lane shell carries its own label and owner identity; without this the
    suite's result would depend on who runs it.
    """
    for name in _IDENTITY_ENV:
        monkeypatch.delenv(name, raising=False)


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


def test_cli_dry_run_human_output_no_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    bridge.mkdir(parents=True)
    exit_code = sweep_cli.main(["--bridge-root", str(bridge), "--max-age-seconds", "60"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "no stale claims" in captured.out


def test_cli_dry_run_lists_stale_in_human_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-stale",
        summary="stale",
        bridge_root=bridge,
        now_utc=_now() - timedelta(hours=1),
    )
    exit_code = sweep_cli.main(
        ["--bridge-root", str(bridge), "--max-age-seconds", "60"]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "WOULD ARCHIVE" in captured.out
    assert "task-cli-stale" in captured.out
    # Dry-run did not mutate.
    assert (bridge / "work_queue" / "claims" / "task-cli-stale.json").exists()


def test_cli_uses_runtime_bridge_root_env_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / "runtime" / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-env-stale",
        summary="stale from runtime root",
        bridge_root=bridge,
        now_utc=_now() - timedelta(hours=1),
    )
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge))

    exit_code = sweep_cli.main(["--max-age-seconds", "60", "--json"])
    captured = capsys.readouterr()

    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["archived"][0]["task_id"] == "task-cli-env-stale"
    assert payload["archived"][0]["applied"] is False
    # Dry-run did not mutate the runtime-root claim.
    assert (bridge / "work_queue" / "claims" / "task-cli-env-stale.json").exists()


def test_cli_apply_archives_and_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-apply",
        summary="apply me",
        bridge_root=bridge,
        now_utc=_now() - timedelta(hours=1),
    )
    exit_code = sweep_cli.main(
        [
            "--bridge-root",
            str(bridge),
            "--max-age-seconds",
            "60",
            "--apply",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["applied"] is True
    assert payload["max_age_seconds"] == 60
    assert len(payload["archived"]) == 1
    assert payload["archived"][0]["task_id"] == "task-cli-apply"
    assert payload["archived"][0]["applied"] is True
    # Original claim gone, archived path exists.
    assert not (bridge / "work_queue" / "claims" / "task-cli-apply.json").exists()
    archived_path = Path(payload["archived"][0]["archived_path"])
    assert archived_path.exists()


def test_cli_rejects_negative_max_age_seconds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-neg",
        summary="must not be archived",
        bridge_root=bridge,
        now_utc=_now() - timedelta(seconds=10),
    )
    exit_code = sweep_cli.main(
        [
            "--bridge-root",
            str(bridge),
            "--max-age-seconds",
            "-1",
            "--apply",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "max_age_seconds must be positive" in captured.err
    # Original claim untouched.
    assert (bridge / "work_queue" / "claims" / "task-cli-neg.json").exists()


def test_cli_rejects_zero_max_age_seconds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bridge = tmp_path / ".agent-bridge"
    claim_task(
        agent="claude-1",
        task_id="task-cli-zero",
        summary="must not be archived",
        bridge_root=bridge,
    )
    exit_code = sweep_cli.main(
        ["--bridge-root", str(bridge), "--max-age-seconds", "0", "--apply"]
    )
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "max_age_seconds must be positive" in captured.err


def test_cli_bridge_root_missing_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does-not-exist"
    exit_code = sweep_cli.main(["--bridge-root", str(missing)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "bridge root not found" in captured.err


# -- QB (RCO2 35B5527F): the mutex cleanup fails AFTER --apply archived its claims --------------------------------

def _mutex_failing_after(error: OSError | None):
    """A _root_mutex stand-in shaped like NamedMutexPort.hold: it yields, and only after the body returned raises
    ``error`` (the port's ReleaseMutex/CloseHandle failure). A body error propagates unchanged."""
    import contextlib

    @contextlib.contextmanager
    def hold(root):
        yield
        if error is not None:
            raise error
    return hold


def _stale(bridge: Path, task_id: str) -> None:
    claim_task(agent="claude-1", task_id=task_id, summary="stale", bridge_root=bridge,
               now_utc=_now() - timedelta(hours=1))


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
def test_cli_apply_reports_an_applied_archive_when_the_mutex_cleanup_fails_after_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, as_json: bool
) -> None:
    bridge = tmp_path / ".agent-bridge"
    _stale(bridge, "task-cli-cleanup")
    monkeypatch.setattr(sweep_cli, "_root_mutex",
                        _mutex_failing_after(OSError(None, "the runtime-root mutex release failed", None, 288)))
    argv = ["--bridge-root", str(bridge), "--max-age-seconds", "60", "--apply"] + (["--json"] if as_json else [])
    exit_code = sweep_cli.main(argv)
    captured = capsys.readouterr()
    assert exit_code == 4 == sweep_cli.MUTEX_CLEANUP_EXIT_CODE     # neither success (0) nor "nothing archived" (1)
    assert not (bridge / "work_queue" / "claims" / "task-cli-cleanup.json").exists()     # applied, once
    assert len(list((bridge / "work_queue" / "done").glob("*.stale_lease.json"))) == 1
    if as_json:
        payload = json.loads(captured.out)
        assert payload["applied"] is True and payload["outcome"] == "applied_mutex_cleanup_failed"
        assert [row["task_id"] for row in payload["archived"]] == ["task-cli-cleanup"]
        assert "the runtime-root mutex release failed" in payload["mutex_cleanup_error"]
    else:
        assert "ARCHIVED: task-cli-cleanup" in captured.out
        assert "sweep applied, then the runtime-root mutex cleanup failed" in captured.err


def test_cli_apply_with_a_clean_mutex_cleanup_is_a_plain_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    _stale(bridge, "task-cli-clean")
    monkeypatch.setattr(sweep_cli, "_root_mutex", _mutex_failing_after(None))
    exit_code = sweep_cli.main(["--bridge-root", str(bridge), "--max-age-seconds", "60", "--apply", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0 and "outcome" not in payload and "mutex_cleanup_error" not in payload
    assert [row["task_id"] for row in payload["archived"]] == ["task-cli-clean"]


# -- QB-L1 (RCO2 FF7BC9A6): an I/O error INSIDE --apply reports what it completed; only a proven no-op exits 1 ----

class _CoreIOError(OSError):
    """Stand-in for the core's typed I/O error (fable-5's side of the QB-L1 interface)."""

    def __init__(self, text, *, applied=None, completed=(), rollback_errors=(), residual=()):
        super().__init__(13, text)
        self.applied, self.completed = applied, list(completed)
        self.rollback_errors, self.residual = list(rollback_errors), list(residual)


def _planned(bridge: Path, *task_ids: str) -> list:
    """Real ArchivedClaim records for these stale claims (a dry run of the real core), as a partial sweep made them."""
    for task_id in task_ids:
        _stale(bridge, task_id)
    return sweep_cli.archive_stale_claims(bridge_root=bridge, now_utc=_now(), max_age_seconds=60, apply=False)


def _sweep_failing(monkeypatch, error: OSError, *, apply: bool = True, as_json: bool = True, bridge: Path):
    monkeypatch.setattr(sweep_cli, "_root_mutex", _mutex_failing_after(None))

    def failing_archive(**kwargs):
        raise error

    monkeypatch.setattr(sweep_cli, "archive_stale_claims", failing_archive)
    argv = ["--bridge-root", str(bridge), "--max-age-seconds", "60"] + (["--apply"] if apply else []) + \
        (["--json"] if as_json else [])
    return sweep_cli.main(argv)


def test_cli_apply_with_an_untyped_io_error_reports_an_unknown_outcome_and_exits_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    _stale(bridge, "task-cli-ioerror")
    exit_code = _sweep_failing(monkeypatch, OSError(28, "No space left on device"), bridge=bridge)
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 4 and payload["applied"] is True and payload["archived"] == []
    assert payload["effects_applied"] is None and payload["outcome"] == "io_error_outcome_unknown"
    assert payload["errors"] == ["OSError: [Errno 28] No space left on device"]


@pytest.mark.parametrize("count", [0, 1, 2], ids=["zero", "one", "many"])
def test_cli_apply_reports_the_archives_a_typed_io_error_says_were_completed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    bridge = tmp_path / ".agent-bridge"
    done = _planned(bridge, "task-a", "task-b", "task-c")[:count]
    error = _CoreIOError("unlink failed; that claim's archive rolled back", applied=bool(done), completed=done)
    exit_code = _sweep_failing(monkeypatch, error, bridge=bridge)
    payload = json.loads(capsys.readouterr().out)
    assert [row["task_id"] for row in payload["archived"]] == [record.claim.task_id for record in done]
    if count:
        assert exit_code == 4 and payload["effects_applied"] is True and payload["outcome"] == "io_error_applied"
    else:
        assert exit_code == 1 and payload["effects_applied"] is False
        assert payload["outcome"] == "io_error_nothing_applied"


@pytest.mark.parametrize("count", [1, 2], ids=["one", "many"])
@pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
def test_cli_apply_never_exits_1_while_listing_completed_archives(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, count: int, as_json: bool
) -> None:
    # QBL1-L1 (RCO1 20778D3C): applied=False with completed archives is a contradiction: UNKNOWN, exit 4, never
    # "nothing archived". applied missing (None) with completed archives is applied (the completed list proves it).
    bridge = tmp_path / ".agent-bridge"
    done = _planned(bridge, "task-a", "task-b", "task-c")[:count]
    exit_code = _sweep_failing(monkeypatch, _CoreIOError("contradictory", applied=False, completed=done),
                               as_json=as_json, bridge=bridge)
    captured = capsys.readouterr()
    assert exit_code == 4
    if as_json:
        payload = json.loads(captured.out)
        assert payload["effects_applied"] is None and payload["outcome"] == "io_error_outcome_unknown"
        assert [row["task_id"] for row in payload["archived"]] == [record.claim.task_id for record in done]
        assert "completed" in payload["contradiction"]
    else:
        assert "ARCHIVED: task-a" in captured.out and "io_error_outcome_unknown" in captured.err


def test_cli_apply_reports_completed_archives_as_applied_when_the_error_omits_applied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    done = _planned(bridge, "task-a", "task-b")[:1]
    exit_code = _sweep_failing(monkeypatch, _CoreIOError("no applied field", completed=done), bridge=bridge)
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 4 and payload["effects_applied"] is True and payload["outcome"] == "io_error_applied"


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
def test_cli_apply_with_a_failed_rollback_names_the_residual_and_exits_4(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, as_json: bool
) -> None:
    bridge = tmp_path / ".agent-bridge"
    done = _planned(bridge, "task-a", "task-b")[:1]
    error = _CoreIOError("unlink of task-b failed; rollback failed", applied=True, completed=done,
                         rollback_errors=["PermissionError: archive of task-b"], residual=["work_queue/done/task-b.json"])
    exit_code = _sweep_failing(monkeypatch, error, as_json=as_json, bridge=bridge)
    captured = capsys.readouterr()
    assert exit_code == 4
    if as_json:
        payload = json.loads(captured.out)
        assert payload["rollback_errors"] == ["PermissionError: archive of task-b"]
        assert payload["residual"] == ["work_queue/done/task-b.json"] and payload["outcome"] == "io_error_applied"
    else:
        assert "ARCHIVED: task-a" in captured.out
        assert "reconcile before the next sweep" in captured.err and "- work_queue/done/task-b.json" in captured.err


def test_cli_dry_run_io_error_is_a_plain_failure_with_nothing_applied(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = tmp_path / ".agent-bridge"
    bridge.mkdir(parents=True)
    exit_code = _sweep_failing(monkeypatch, OSError(5, "Input/output error"), apply=False, as_json=False, bridge=bridge)
    captured = capsys.readouterr()
    assert exit_code == 1 and captured.out == "" and "sweep failed: " in captured.err


def test_cli_apply_refused_by_the_mutex_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import contextlib

    from tools.bridge_v2_queue_transactions import QueueTransactionError

    bridge = tmp_path / ".agent-bridge"
    _stale(bridge, "task-cli-busy")

    @contextlib.contextmanager
    def busy(root):
        raise QueueTransactionError("runtime-root mutex busy")
        yield

    monkeypatch.setattr(sweep_cli, "_root_mutex", busy)
    exit_code = sweep_cli.main(["--bridge-root", str(bridge), "--max-age-seconds", "60", "--apply", "--json"])
    captured = capsys.readouterr()
    assert exit_code == 1 and captured.out == "" and "sweep refused: runtime-root mutex: " in captured.err
    assert (bridge / "work_queue" / "claims").exists() and len(list((bridge / "work_queue" / "claims").glob("*"))) == 1


def test_cli_dry_run_with_unparseable_timestamps_falls_back_then_uses_the_threshold(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Invalid-timestamp twin (Lead 04:02Z): an unparseable last_heartbeat_utc falls back to claimed_at_utc, and a
    # claim with neither parseable is aged at the threshold. The out-of-range ISO case (year 1, +14:00, OverflowError)
    # belongs to the parser fix in fable-5's scope and is not pinned here.
    bridge = tmp_path / ".agent-bridge"
    claims = bridge / "work_queue" / "claims"
    claims.mkdir(parents=True)
    fresh = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for task_id, claimed_at in (("task-bad-beat-fresh-claim", fresh), ("task-bad-both", "not-a-time-either")):
        (claims / (task_id + ".json")).write_text(json.dumps({
            "agent": "claude-1", "task_id": task_id, "summary": "s", "mode": "read-only", "write_scope": [],
            "run_id": "", "claimed_at_utc": claimed_at, "last_heartbeat_utc": "not-a-time", "lease_seconds": 900,
            "owner_identity": "none"}), encoding="utf-8")
    exit_code = sweep_cli.main(["--bridge-root", str(bridge), "--max-age-seconds", "60", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [(row["task_id"], row["age_seconds"]) for row in payload["archived"]] == [("task-bad-both", 60)]
