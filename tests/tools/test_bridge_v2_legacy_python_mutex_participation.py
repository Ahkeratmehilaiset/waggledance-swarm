# SPDX-License-Identifier: BUSL-1.1
"""S2 (RCO2 21:24:58Z; Lead 0b33855f): the legacy Python claims writers take the v2 runtime-root mutex first.

tools/work_queue.py claim, release and heartbeat now run inside NamedMutexPort().hold(mutex_name(root)), the same
Windows named mutex every v2 writer takes first, so a v2 participant holding it excludes the legacy write: the CLI
waits a bounded time and refuses without writing. tools/work_queue_sweep_stale.py --apply takes the same mutex
(fable-5 foreman call 2026-10-01, inventory option a); its dry run takes none. Fixture roots only; the lane
environment is scrubbed.

Scope stated, not claimed: these two tools are the only production callers of the waggledance.core.work_queue
writers (a static inventory, 2026-10-01). The legacy PowerShell writers take the same kernel object through
Enter-BridgeQueueRootMutex (.agent-bridge/bin/ClaimLeaseHeartbeat.ps1, which loads the twin BridgeV2QueueMutex.ps1)
in the S2 PowerShell half (fable-5 1764254b), composed with this one; session-heartbeat files, an agent writing a
claim file directly, and the limits of a static inventory remain outside. The mutex exists only on Windows;
elsewhere nothing is excluded and nothing is claimed. A writer is refused before it writes for any --bridge-root
the v2 canonical-root rule refuses (not a local drive-letter path, such as a relative, drive- or root-relative, UNC
or device form; a .. or alias segment; an alternate stream; a link or reparse point on the path), since the mutex
is named from that canonical root; the relative and alias cases are pinned below, and such a refusal exits 2 while
every other refusal exits 1 (fable-5 B1). run_idle_protocol_once.py mutates no claim or done file (pinned below).
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from tools import work_queue as wq_cli
from tools import work_queue_sweep_stale as sweep_cli
from tools.bridge_v2_queue_transactions import QueueTransactionError, mutex_name
from waggledance.core.work_queue import claim_task

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="the v2 runtime-root mutex is a Windows named mutex")

PY_HOLDER = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "from tools.bridge_v2_queue_ports_windows import NamedMutexPort\n"
    "from tools.bridge_v2_queue_transactions import mutex_name\n"
    "with NamedMutexPort().hold(mutex_name(sys.argv[1]), 10):\n"
    "    print('held', flush=True)\n"
    "    sys.stdin.read()\n"
)
# Another process tries the root mutex for 0.3 s: "busy" while someone holds it, "free" otherwise.
PY_PROBE = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "from tools.bridge_v2_queue_ports_windows import NamedMutexPort\n"
    "from tools.bridge_v2_queue_transactions import LockTimeout, mutex_name\n"
    "try:\n"
    "    with NamedMutexPort().hold(mutex_name(sys.argv[1]), 0.3):\n"
    "        print('free')\n"
    "except LockTimeout:\n"
    "    print('busy')\n"
)
PS_HOLDER = ("$m = [System.Threading.Mutex]::new($false, '{name}'); if (-not $m.WaitOne(10000)) {{ exit 3 }}; "
             "[Console]::Out.WriteLine('held'); [Console]::Out.Flush(); [void][Console]::In.ReadToEnd(); "
             "$m.ReleaseMutex(); $m.Dispose()")
CLAIM = ["claim", "--agent", "fable-5", "--task-id", "team/s2", "--summary", "s2", "--mode", "write",
         "--write-scope", "tools/x.py"]


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.upper().startswith(("AGENT_BRIDGE", "WD_"))}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    for name in [k for k in os.environ if k.upper().startswith(("AGENT_BRIDGE", "WD_"))]:
        monkeypatch.delenv(name)
    monkeypatch.setattr(wq_cli, "LOCK_TIMEOUT_SECONDS", 0.5, raising=False)
    root = tmp_path / "bridge"
    (root / "work_queue" / "claims").mkdir(parents=True)
    (root / "work_queue" / "done").mkdir(parents=True)
    return root


class Holder:
    """Another process holding mutex_name(root) until ``release``."""

    def __init__(self, command: list[str]) -> None:
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, env=_env())
        line = self.process.stdout.readline().strip()
        if line != "held":
            self.release()
            pytest.fail("the holder did not take the mutex: " + line + " " + self.process.stderr.read()[:300])

    def release(self) -> None:
        if self.process.poll() is None:
            self.process.stdin.close()
            self.process.wait(timeout=20)


def _python_holder(root: Path) -> Holder:
    return Holder([sys.executable, "-c", PY_HOLDER, str(root), str(REPO)])


def _probe(root: Path) -> str:
    return subprocess.run([sys.executable, "-c", PY_PROBE, str(root), str(REPO)], env=_env(), capture_output=True,
                          text=True, timeout=60).stdout.strip()


def _cli(root: Path, *args: str) -> int:
    return wq_cli.main(["--bridge-root", str(root), "--json", *args])


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted((root / "work_queue").rglob("*")) if p.is_file()}


def _claims(root: Path) -> list:
    return sorted((root / "work_queue" / "claims").glob("*.json"))


def test_a_legacy_claim_waits_for_the_v2_root_mutex_and_refuses_without_writing(bridge, capsys):
    holder = _python_holder(bridge)
    try:
        started = time.monotonic()
        code = _cli(bridge, *CLAIM)
        waited = time.monotonic() - started
        out = capsys.readouterr().out
    finally:
        holder.release()
    assert code != 0 and _claims(bridge) == []                      # nothing landed while v2 held the root
    assert 0.4 <= waited < 5 and "runtime-root mutex busy" in out
    assert _cli(bridge, *CLAIM) == 0 and len(_claims(bridge)) == 1  # positive twin after the release


def test_a_legacy_claim_writes_while_another_process_finds_the_root_mutex_busy(bridge, monkeypatch):
    # fable-5 A1 (00:32:44Z on 58c8dd64): a refusal at acquisition does not prove the write runs while the mutex is
    # HELD; a writer that acquires, releases and then writes would pass every other test. Probe inside the write.
    seen = []
    real = wq_cli.claim_task

    def probing_claim(**kwargs):
        seen.append(_probe(bridge))
        return real(**kwargs)

    monkeypatch.setattr(wq_cli, "claim_task", probing_claim)
    assert _cli(bridge, *CLAIM) == 0 and len(_claims(bridge)) == 1
    assert seen == ["busy"] and _probe(bridge) == "free"            # held during the write, released after it


def test_a_legacy_release_and_heartbeat_wait_and_change_nothing_while_held(bridge, monkeypatch, capsys):
    # Fixture owner identity (never the lane's): the core extends a lease only for its owning session.
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_SESSION_ID", "session-fixture")
    monkeypatch.setenv("AGENT_BRIDGE_OWNER_TOKEN", "token-fixture")
    assert _cli(bridge, *CLAIM) == 0
    before = _tree(bridge)
    capsys.readouterr()
    holder = _python_holder(bridge)
    try:
        assert _cli(bridge, "heartbeat", "--agent", "fable-5", "--task-id", "team/s2") != 0
        assert "runtime-root mutex busy" in capsys.readouterr().out
        assert _cli(bridge, "release", "--agent", "fable-5", "--task-id", "team/s2") != 0
        assert "runtime-root mutex busy" in capsys.readouterr().out
        assert _tree(bridge) == before                                # no claim, done or beat change
    finally:
        holder.release()
    assert _cli(bridge, "heartbeat", "--agent", "fable-5", "--task-id", "team/s2") == 0
    assert _cli(bridge, "release", "--agent", "fable-5", "--task-id", "team/s2") == 0
    assert _claims(bridge) == [] and list((bridge / "work_queue" / "done").glob("*"))


def _open_witness(root: Path) -> tuple:
    """A second, SYNCHRONIZE-only handle to mutex_name(root): it keeps the named object alive, so a holder's death
    abandons it (without one the object would be destroyed with its last handle and simply created anew)."""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    witness = kernel32.OpenMutexW(0x00100000, False, mutex_name(root))      # SYNCHRONIZE only
    assert witness, "could not open the holder's mutex"
    return kernel32, witness


def test_an_abandoned_root_mutex_refuses_once_and_is_never_silently_recovered(bridge, capsys):
    holder = _python_holder(bridge)
    kernel32, witness = _open_witness(bridge)
    try:
        holder.process.kill()                                        # ends while holding: the mutex is abandoned
        holder.process.wait(timeout=20)
        assert _cli(bridge, *CLAIM) != 0 and _claims(bridge) == []
        assert "abandoned" in capsys.readouterr().out
        # fable-5 23:19:55Z: while the witness keeps the SAME object alive, another process takes it at once, so the
        # refusal released the ownership its abandoned wait was granted. A port that closed the handle unreleased
        # would leave this thread the owner (an in-process retry would re-enter it), and this take would time out.
        _python_holder(bridge).release()
    finally:
        kernel32.CloseHandle(witness)
    assert _cli(bridge, *CLAIM) == 0 and len(_claims(bridge)) == 1  # the positive twin once nothing holds the root


def test_a_relative_root_is_refused_for_writers_and_still_read(bridge, monkeypatch, capsys):
    # A behaviour change, pinned (fable-5 23:19:55Z): the mutex name needs the canonical absolute root, so a writer
    # given a relative --bridge-root is refused before it writes (fail-closed); reads take no mutex and still work.
    monkeypatch.chdir(bridge.parent)
    assert wq_cli.main(["--bridge-root", bridge.name, "--json", *CLAIM]) == 2 and _claims(bridge) == []  # input error
    assert "runtime-root mutex: runtime root" in capsys.readouterr().out
    assert wq_cli.main(["--bridge-root", bridge.name, "--json", "list"]) == 0
    # RS7 (legacy core): a claim cwd must be a repository top level, and tmp_path is none, so the absolute twin claims
    # from REPO as every other claim in this file does; the twin is about the absolute root, not about the cwd.
    monkeypatch.chdir(REPO)
    assert _cli(bridge, *CLAIM) == 0 and len(_claims(bridge)) == 1  # the absolute twin


@pytest.mark.parametrize("text", [
    ("the runtime-root mutex could not be created or opened (ValueError: exactly one enabled token logon SID is "
     "required)"),
    "the runtime-root mutex could not be created or opened (ValueError: invalid token user SID)",
    "runtime-root mutex busy"], ids=["required_literal", "invalid_literal", "busy"])
def test_a_mutex_refusal_exits_1_whatever_its_text_says(bridge, monkeypatch, text):
    # fable-5 B1 (01:06:10Z): a refusal is classified by its source, never by its words; a creation failure quoting
    # a policy literal ("... is required", "invalid ...") is not an input error.
    @contextlib.contextmanager
    def refusing(root):
        raise QueueTransactionError(text)
        yield

    monkeypatch.setattr(wq_cli, "_root_mutex", refusing)
    assert _cli(bridge, *CLAIM) == 1 and _claims(bridge) == []


@pytest.mark.parametrize("name", ["bridge~1", "bridge.", "bridge "],
                         ids=["short_name", "trailing_dot", "trailing_space"])
def test_an_unusable_root_is_an_input_error_and_exits_2(bridge, name):
    # The canonical-root rule refuses an alias segment lexically, as it refuses a relative root: both exit 2.
    assert wq_cli.main(["--bridge-root", str(bridge.parent / name), "--json", *CLAIM]) == 2


def test_reads_do_not_take_the_mutex(bridge):
    holder = _python_holder(bridge)
    try:
        started = time.monotonic()
        assert _cli(bridge, "list") == 0 and time.monotonic() - started < 0.4
    finally:
        holder.release()


def test_the_mutex_is_released_after_a_primary_writer_error(bridge, capsys):
    assert _cli(bridge, *CLAIM) == 0
    assert _cli(bridge, "release", "--agent", "codex-tools-1", "--task-id", "team/s2") != 0   # not the owner
    capsys.readouterr()
    holder = _python_holder(bridge)                                  # takes it at once: nothing left held
    holder.release()


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_a_powershell_holder_of_the_same_name_excludes_the_legacy_claim(bridge, shell):
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(shell + " is not available on this host (unknown, not proven)")
    script = PS_HOLDER.format(name=mutex_name(bridge))
    holder = Holder([exe, "-NoProfile", "-NonInteractive", "-Command", script])
    try:
        assert _cli(bridge, *CLAIM) != 0 and _claims(bridge) == []
    finally:
        holder.release()
    assert _cli(bridge, *CLAIM) == 0


def test_run_idle_protocol_once_writes_no_claim_or_done_file(bridge, tmp_path):
    assert _cli(bridge, *CLAIM) == 0
    (bridge / "shared").mkdir()
    (bridge / "shared" / "events.jsonl").write_text("", encoding="utf-8")
    before = _tree(bridge)
    completed = subprocess.run([sys.executable, str(REPO / "tools" / "run_idle_protocol_once.py"), "--bridge-root", str(bridge),
                    "--from-agent", "fable-5", "--to", "codex-lead-1", "--json",
                    "--scratch-dir", str(tmp_path / "scratch")], cwd=str(REPO), env=_env(), capture_output=True,
                   text=True, timeout=120)
    assert completed.stdout.strip(), "idle runner emitted no JSON report"
    report = json.loads(completed.stdout)
    assert isinstance(report, dict)
    # Empty events deliberately yield unknown, not a successful idle decision.
    assert report["decision"] == "unknown" and report["emitted"] is False
    assert report["errors"]
    assert completed.returncode == report["exit_code"] == 2
    assert _tree(bridge) == before                                   # a reader only: no participation needed


def test_MUTANT_IDLE_CHILD_NOT_STARTED_is_rejected(bridge, tmp_path, monkeypatch):
    completed_process = subprocess.CompletedProcess

    class IdleChildNotStarted:
        @staticmethod
        def run(args, **kwargs):
            return completed_process(args, 2, stdout="", stderr="")

    # Stub only this module's child call; exercise the actual read-only test oracle.
    monkeypatch.setitem(globals(), "subprocess", IdleChildNotStarted)
    with pytest.raises(AssertionError, match="idle runner emitted no JSON report"):
        test_run_idle_protocol_once_writes_no_claim_or_done_file(bridge, tmp_path)


# -- fable-5 foreman call 2026-10-01 00:03:10Z (inventory option a): the sweep's --apply takes the same mutex ------

def _stale_claim(root: Path) -> None:
    claim_task(agent="fable-5", task_id="team/stale", summary="stale", bridge_root=root,
               now_utc=datetime.now(timezone.utc) - timedelta(hours=1))


def _sweep(root: Path, *args: str) -> int:
    return sweep_cli.main(["--bridge-root", str(root), "--max-age-seconds", "60", *args])


def _done(root: Path) -> list:
    return sorted((root / "work_queue" / "done").glob("*.json"))


def test_a_sweep_apply_waits_for_the_root_mutex_and_refuses_without_archiving(bridge, capsys):
    _stale_claim(bridge)
    before = _tree(bridge)
    holder = _python_holder(bridge)
    try:
        started = time.monotonic()
        code = _sweep(bridge, "--apply")
        waited = time.monotonic() - started
        err = capsys.readouterr().err
    finally:
        holder.release()
    assert code == 1 and _tree(bridge) == before                    # the stale claim stays; nothing archived
    assert 0.4 <= waited < 5 and "sweep refused: runtime-root mutex: runtime-root mutex busy" in err
    assert _sweep(bridge, "--apply") == 0 and _claims(bridge) == [] and len(_done(bridge)) == 1  # positive twin


def test_the_sweep_archives_while_another_process_finds_the_root_mutex_busy(bridge, monkeypatch):
    # fable-5 A1: the archive itself runs inside the mutex, not after a released acquisition.
    _stale_claim(bridge)
    seen = []
    real = sweep_cli.archive_stale_claims

    def probing_archive(**kwargs):
        seen.append(_probe(bridge))
        return real(**kwargs)

    monkeypatch.setattr(sweep_cli, "archive_stale_claims", probing_archive)
    assert _sweep(bridge, "--apply") == 0 and _claims(bridge) == [] and len(_done(bridge)) == 1
    assert seen == ["busy"] and _probe(bridge) == "free"


def test_the_sweep_dry_run_takes_no_mutex_and_writes_nothing(bridge, capsys):
    _stale_claim(bridge)
    before = _tree(bridge)
    holder = _python_holder(bridge)
    try:
        started = time.monotonic()
        assert _sweep(bridge, "--json") == 0 and time.monotonic() - started < 0.4
    finally:
        holder.release()
    report = json.loads(capsys.readouterr().out)
    assert report["applied"] is False and [row["task_id"] for row in report["archived"]] == ["team/stale"]
    assert _tree(bridge) == before


def test_an_abandoned_root_mutex_refuses_the_sweep_once_and_is_released(bridge, capsys):
    _stale_claim(bridge)
    before = _tree(bridge)
    holder = _python_holder(bridge)
    kernel32, witness = _open_witness(bridge)
    try:
        holder.process.kill()                                        # ends while holding: the mutex is abandoned
        holder.process.wait(timeout=20)
        assert _sweep(bridge, "--apply") == 1 and _tree(bridge) == before
        assert "abandoned" in capsys.readouterr().err
        _python_holder(bridge).release()     # another process takes the SAME object at once: the refusal released it
    finally:
        kernel32.CloseHandle(witness)
    assert _sweep(bridge, "--apply") == 0 and _claims(bridge) == []   # the positive twin once nothing holds the root


def test_a_relative_root_is_refused_for_sweep_apply_and_the_dry_run_still_reads(bridge, monkeypatch, capsys):
    _stale_claim(bridge)
    before = _tree(bridge)
    monkeypatch.chdir(bridge.parent)
    relative = ["--bridge-root", bridge.name, "--max-age-seconds", "60"]
    assert sweep_cli.main([*relative, "--apply"]) == 1 and _tree(bridge) == before
    assert "sweep refused: runtime-root mutex: runtime root" in capsys.readouterr().err
    assert sweep_cli.main(relative) == 0 and _tree(bridge) == before     # the dry run reads the same relative root
    assert _sweep(bridge, "--apply") == 0 and _claims(bridge) == []       # the absolute twin


# -- QB (RCO2 35B5527F): releasing or closing the root mutex fails AFTER the writer applied its change -------------

class _Fn:
    """One wrapped kernel32 function: calls go through ``hook``; argtypes/restype set on it reach the real one."""

    def __init__(self, real, hook) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_hook", hook)

    def __call__(self, *args):
        return self._hook(self._real, *args)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value) -> None:
        setattr(self._real, name, value)


class _CleanupFailingKernel32:
    """The real kernel32 for the port; after the REAL ReleaseMutex/CloseHandle succeeded, the port is told that the
    release (step "release") or the close after a release (step "close") failed. No kernel object stays owned or
    open, so the next holder takes the mutex at once."""

    def __init__(self, real, step: str | None) -> None:
        self._real, self._step, self._released, self.calls = real, step, False, []
        self.ReleaseMutex = _Fn(real.ReleaseMutex, self._release)
        self.CloseHandle = _Fn(real.CloseHandle, self._close)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def _release(self, real, handle):
        rc = real(handle)
        self._released = bool(rc)
        self.calls.append(("ReleaseMutex", rc))
        return 0 if self._step == "release" else rc

    def _close(self, real, handle):
        rc = real(handle)
        self.calls.append(("CloseHandle", rc))
        return 0 if self._step == "close" and self._released else rc


def _cleanup_failing(monkeypatch, step: str | None) -> list:
    from tools import bridge_v2_queue_ports_windows as ports
    real_factory = ports._kernel32
    made: list = []

    def factory():
        made.append(_CleanupFailingKernel32(real_factory(), step))
        return made[-1]

    monkeypatch.setattr(ports, "_kernel32", factory)
    return made


@pytest.mark.parametrize("step", ["release", "close"])
def test_a_mutex_cleanup_failure_after_an_applied_claim_is_reported_as_applied_never_as_success(
        bridge, monkeypatch, capsys, step):
    made = _cleanup_failing(monkeypatch, step)
    code = _cli(bridge, *CLAIM)
    report = json.loads(capsys.readouterr().out)
    assert code == wq_cli.MUTEX_CLEANUP_EXIT_CODE == 4
    assert report["ok"] is False and report["applied"] is True and report["outcome"] == "applied_mutex_cleanup_failed"
    assert report["decision"] == "claimed" and report["claim"]["task_id"] == "team/s2"   # the applied result is kept
    assert report["errors"][0].startswith("the change WAS applied; then the runtime-root mutex cleanup failed: OSError")
    assert ("release failed" if step == "release" else "handle close failed") in report["errors"][0]
    assert len(_claims(bridge)) == 1 and [c for c in made[0].calls if c[0] == "ReleaseMutex"] == [("ReleaseMutex", 1)]
    _python_holder(bridge).release()                                 # nothing left held: another process takes it


def test_the_same_kernel32_with_no_cleanup_failure_is_a_plain_success(bridge, monkeypatch, capsys):
    made = _cleanup_failing(monkeypatch, None)
    assert _cli(bridge, *CLAIM) == 0 and len(_claims(bridge)) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True and "applied" not in report and "outcome" not in report
    assert ("ReleaseMutex", 1) in made[0].calls


def test_a_writer_error_stays_the_reported_error_when_the_cleanup_also_fails(bridge, monkeypatch, capsys):
    assert _cli(bridge, *CLAIM) == 0
    before = _tree(bridge)
    capsys.readouterr()
    _cleanup_failing(monkeypatch, "release")
    code = _cli(bridge, "release", "--agent", "codex-tools-1", "--task-id", "team/s2")   # not the owner
    assert code not in (0, wq_cli.MUTEX_CLEANUP_EXIT_CODE)
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "work_queue_error" and "applied" not in report and _tree(bridge) == before


def test_a_refusal_before_the_write_is_still_a_refusal_with_the_cleanup_wrapper(bridge, monkeypatch, capsys):
    _cleanup_failing(monkeypatch, "release")
    holder = _python_holder(bridge)
    try:
        assert _cli(bridge, *CLAIM) == 1 and _claims(bridge) == []
    finally:
        holder.release()
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "work_queue_error" and "applied" not in report
    assert report["errors"] == ["runtime-root mutex: runtime-root mutex busy"]


@pytest.mark.parametrize("step", ["release", "close"])
def test_a_mutex_cleanup_failure_after_an_applied_sweep_reports_the_archive_and_exits_4(
        bridge, monkeypatch, capsys, step):
    _stale_claim(bridge)
    _cleanup_failing(monkeypatch, step)
    code = _sweep(bridge, "--apply", "--json")
    report = json.loads(capsys.readouterr().out)
    assert code == 4 and report["applied"] is True and report["outcome"] == "applied_mutex_cleanup_failed"
    assert [row["task_id"] for row in report["archived"]] == ["team/stale"] and report["mutex_cleanup_error"]
    assert _claims(bridge) == [] and len(_done(bridge)) == 1        # archived once, never retried
    _python_holder(bridge).release()
