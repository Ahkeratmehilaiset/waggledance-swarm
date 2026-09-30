# SPDX-License-Identifier: BUSL-1.1
"""S2 (RCO2 21:24:58Z; Lead 0b33855f): the legacy Python claims writer takes the v2 runtime-root mutex first.

tools/work_queue.py claim, release and heartbeat now run inside NamedMutexPort().hold(mutex_name(root)), the same
Windows named mutex every v2 writer takes first, so a v2 participant holding it excludes the legacy write: the CLI
waits a bounded time and refuses without writing. Fixture roots only; the lane environment is scrubbed.

Scope stated, not claimed: only this CLI's writer commands participate. Other direct callers of
waggledance.core.work_queue (tools/work_queue_sweep_stale.py --apply archives and unlinks claims without the mutex)
and the legacy PowerShell writers (Fable's slice) are not covered here, so a complete queue snapshot still cannot
prove a lane idle. The mutex exists only on Windows; elsewhere nothing is excluded and nothing is claimed. A writer
given a relative --bridge-root is refused (the mutex name needs the absolute root). run_idle_protocol_once.py
mutates no claim or done file (pinned below).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from tools import work_queue as wq_cli
from tools.bridge_v2_queue_transactions import mutex_name

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


def test_an_abandoned_root_mutex_refuses_once_and_is_never_silently_recovered(bridge, capsys):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    holder = _python_holder(bridge)
    # A second open handle keeps the named object alive, so the holder's death abandons it (without one the
    # object would be destroyed with its last handle and simply created anew).
    witness = kernel32.OpenMutexW(0x00100000, False, mutex_name(bridge))      # SYNCHRONIZE only
    assert witness, "could not open the holder's mutex"
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
    assert wq_cli.main(["--bridge-root", bridge.name, "--json", *CLAIM]) != 0 and _claims(bridge) == []
    assert "runtime-root mutex: runtime root" in capsys.readouterr().out
    assert wq_cli.main(["--bridge-root", bridge.name, "--json", "list"]) == 0
    assert _cli(bridge, *CLAIM) == 0 and len(_claims(bridge)) == 1  # the absolute twin


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
    subprocess.run([sys.executable, str(REPO / "tools" / "run_idle_protocol_once.py"), "--bridge-root", str(bridge),
                    "--from-agent", "fable-5", "--to", "codex-lead-1", "--json",
                    "--scratch-dir", str(tmp_path / "scratch")], cwd=str(REPO), env=_env(), capture_output=True,
                   text=True, timeout=120)
    assert _tree(bridge) == before                                   # a reader only: no participation needed
