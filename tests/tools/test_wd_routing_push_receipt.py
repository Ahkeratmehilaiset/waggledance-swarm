# SPDX-License-Identifier: BUSL-1.1
"""F26 S3 push receipt: fake Git ports only (no git, network or production path), plus hostile persistence twins."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import tools.wd_routing_push_receipt as pr  # noqa: E402

ATTEMPT = "a" * 64
TASK = "codex-lead-1/f26-push-receipt"
WORKER = "claude-rco-2"
BRANCH = "claude-rco-2/push-receipt-fixture"
COMMIT = "1" * 40
TREE = "2" * 40
OBSERVED = "2026-10-01T16:20:00+03:00"
REF = "refs/heads/" + BRANCH


class FakeGit:
    """Answers the three expected commands; anything else fails the test."""

    def __init__(self, head=COMMIT + "\n", tree=TREE + "\n", remote=COMMIT + "\t" + REF + "\n", codes=None, raises=None):
        self.answers = {"local_head": head, "tree": tree, "ls_remote": remote}
        self.codes = codes or {}
        self.raises = raises or {}
        self.calls = []

    def run(self, args):
        assert type(args) is tuple and all(type(a) is str for a in args)
        if args == ("rev-parse", "--verify", "--end-of-options", REF + "^{commit}"):
            step = "local_head"
        elif args[:3] == ("rev-parse", "--verify", "--end-of-options") and args[3].endswith("^{tree}"):
            step = "tree"
        elif args == ("ls-remote", "--refs", "origin", REF):
            step = "ls_remote"
        else:
            raise AssertionError("unexpected git call %r" % (args,))
        self.calls.append(step)
        if step in self.raises:
            raise self.raises[step]
        return self.codes.get(step, 0), self.answers[step], ""


def build(git=None, **over):
    fields = dict(attempt_id=ATTEMPT, task_id=TASK, worker=WORKER, branch=BRANCH, observed_remote_utc=OBSERVED)
    fields.update(over)
    return pr.build_receipt(git=git or FakeGit(), **fields)


def refused(reason, **kwargs):
    with pytest.raises(pr.PushReceiptRefused) as caught:
        build(**kwargs)
    assert caught.value.reason == reason


# --- producer --------------------------------------------------------------------------------------------

def test_exactly_one_matching_remote_line_gives_a_lane_observed_receipt():
    git = FakeGit()
    receipt = build(git)
    assert git.calls == ["local_head", "tree", "ls_remote"]
    assert receipt == dict(receipt, schema=pr.SCHEMA, attempt_id=ATTEMPT, task_id=TASK, worker=WORKER, branch=BRANCH,
                           commit=COMMIT, tree=TREE, remote_ref=REF, ls_remote_sha=COMMIT,
                           observed_remote_utc="2026-10-01T13:20:00.000000Z",
                           evidence="lane_observed_not_authorship", authority="none")
    assert set(receipt) == pr.RECEIPT_KEYS and pr.validate_receipt(receipt) is receipt
    assert build(FakeGit())["receipt_digest"] == receipt["receipt_digest"]   # same evidence, same digest


@pytest.mark.parametrize("remote, reason", [
    ("", "remote_ref_absent"),
    (COMMIT + "\t" + REF + "\n" + COMMIT + "\t" + REF + "\n", "ls_remote_ambiguous"),
    ("3" * 40 + "\t" + REF + "\n", "remote_moved"),
    (COMMIT + "\trefs/heads/other\n", "ls_remote_ref_mismatch"),
    (COMMIT + " " + REF + "\n", "ls_remote_malformed"),
    ("A" * 40 + "\t" + REF + "\n", "ls_remote_malformed"),           # uppercase hex is not a git sha
    (COMMIT + "\t" + REF + "\textra\n", "ls_remote_malformed"),
])
def test_a_remote_that_does_not_name_exactly_the_local_head_gives_no_receipt(remote, reason):
    refused(reason, git=FakeGit(remote=remote))


@pytest.mark.parametrize("step", ["local_head", "tree", "ls_remote"])
def test_a_failed_git_command_gives_no_receipt_and_stops(step):
    git = FakeGit(codes={step: 128})
    with pytest.raises(pr.PushReceiptRefused) as caught:
        build(git)
    assert caught.value.reason == "git_failed:" + step and git.calls[-1] == step   # no later command, no retry


@pytest.mark.parametrize("over, reason", [
    ({"head": "short\n"}, "git_output_malformed:local_head"),
    ({"head": COMMIT + "\n" + COMMIT + "\n"}, "git_output_malformed:local_head"),
    ({"head": COMMIT + "\r\n"}, "git_output_malformed:local_head"),
    ({"tree": "x" * 40 + "\n"}, "git_output_malformed:tree"),
])
def test_malformed_git_output_gives_no_receipt(over, reason):
    refused(reason, git=FakeGit(**over))


def test_a_port_error_is_a_refusal_and_cancellation_propagates():
    refused("git_port_error:ls_remote:OSError", git=FakeGit(raises={"ls_remote": OSError("network down")}))
    with pytest.raises(KeyboardInterrupt):
        build(FakeGit(raises={"ls_remote": KeyboardInterrupt()}))


def test_a_port_returning_the_wrong_shape_is_refused():
    class Odd(FakeGit):
        def run(self, args):
            return [0, COMMIT + "\n", ""]
    refused("git_port_malformed:local_head", git=Odd())


class Lying(str):
    def __eq__(self, other):
        return True
    __hash__ = str.__hash__


@pytest.mark.parametrize("over, reason", [
    ({"attempt_id": "A" * 64}, "attempt_id_invalid"),
    ({"attempt_id": Lying("a" * 64)}, "attempt_id_invalid"),
    ({"task_id": ""}, "task_id_invalid"),
    ({"worker": "Claude-RCO-2"}, "worker_invalid"),
    ({"branch": "a/../b"}, "branch_invalid"),
    ({"branch": "refs/heads/x"}, "branch_invalid"),
    ({"branch": "x.lock"}, "branch_invalid"),
    ({"branch": "-x"}, "branch_invalid"),
    ({"observed_remote_utc": "2026-10-01T16:20:00"}, "observed_remote_utc_invalid"),
    ({"observed_remote_utc": "yesterday"}, "observed_remote_utc_invalid"),
    ({"observed_remote_utc": 1}, "observed_remote_utc_invalid"),
])
def test_malformed_identity_or_time_never_reaches_git(over, reason):
    git = FakeGit()
    refused(reason, git=git, **over)
    assert git.calls == []


class HookStr(str):
    """A str whose own hooks must never run inside the validator."""

    calls: list = []

    def __eq__(self, other):
        HookStr.calls.append("eq")
        raise RuntimeError("caller hook ran")

    def __ne__(self, other):
        HookStr.calls.append("ne")
        raise RuntimeError("caller hook ran")

    def __hash__(self):
        HookStr.calls.append("hash")
        raise RuntimeError("caller hook ran")


@pytest.mark.parametrize("field", sorted(pr.RECEIPT_KEYS))
def test_t1_a_hooked_str_value_in_any_field_is_refused_before_its_hooks_run(field):
    HookStr.calls.clear()
    receipt = build()
    receipt[field] = HookStr(receipt[field])
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.validate_receipt(receipt)
    expected = "observed_remote_utc_invalid" if field == "observed_remote_utc" else "receipt_malformed"
    assert caught.value.reason == expected and HookStr.calls == []


class ArmedKey(str):
    """A str key whose hash/equality hooks are armed only after it sits in the dict."""

    armed = False
    calls: list = []

    def __hash__(self):
        if ArmedKey.armed:
            ArmedKey.calls.append("hash")
            raise RuntimeError("caller hook ran")
        return str.__hash__(self)

    def __eq__(self, other):
        if ArmedKey.armed:
            ArmedKey.calls.append("eq")
            raise RuntimeError("caller hook ran")
        return str.__eq__(self, other)


@pytest.mark.parametrize("name", ["schema", "not-a-field"])
def test_t1_a_hooked_str_key_is_refused_before_its_hash_or_equality_runs(name):
    receipt = build()
    value = receipt.pop("schema")
    ArmedKey.armed, ArmedKey.calls = False, []
    receipt[ArmedKey(name)] = value
    ArmedKey.armed = True
    try:
        with pytest.raises(pr.PushReceiptRefused) as caught:
            pr.validate_receipt(receipt)
    finally:
        ArmedKey.armed = False
    assert caught.value.reason == "receipt_malformed" and ArmedKey.calls == []


def test_t1_valid_controls_still_validate_and_persist_unchanged(approved):
    root, folder = approved
    receipt = build()
    assert pr.validate_receipt(dict(receipt)) == receipt
    assert pr.persist_receipt(receipt, folder, approved_root=root)["status"] == "created"


# --- persistence ------------------------------------------------------------------------------------------

@pytest.fixture
def approved(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "_forbidden_roots", lambda: [])   # pytest's tmp_path lives under TEMP
    folder = tmp_path / "approved" / "push-receipts"
    folder.mkdir(parents=True)
    return tmp_path / "approved", folder


def test_a_receipt_is_created_once_and_an_identical_retry_is_a_no_op(approved):
    root, folder = approved
    receipt = build()
    first = pr.persist_receipt(receipt, folder, approved_root=root)
    second = pr.persist_receipt(build(), folder, approved_root=root)
    assert (first["status"], second["status"]) == ("created", "unchanged") and first["path"] == second["path"]
    path = Path(first["path"])
    assert path.name == receipt["receipt_digest"] + ".json"
    assert pr.load_receipt(path) == receipt
    assert sorted(p.name for p in folder.iterdir()) == [path.name]   # no temporary file left behind


def test_other_bytes_under_the_same_name_conflict(approved):
    root, folder = approved
    receipt = build()
    (folder / (receipt["receipt_digest"] + ".json")).write_bytes(b'{"tampered": true}\n')
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert caught.value.reason == "receipt_conflict"
    assert sorted(p.name for p in folder.iterdir()) == [receipt["receipt_digest"] + ".json"]


@pytest.mark.parametrize("change, reason", [
    (lambda r: r.update(commit="3" * 40), "receipt_malformed"),        # ls_remote_sha no longer equals commit
    (lambda r: r.update(tree="3" * 40), "receipt_digest_mismatch"),
    (lambda r: r.update(extra=1), "receipt_malformed"),
    (lambda r: r.update(authority="lead"), "receipt_malformed"),
    (lambda r: r.update(observed_remote_utc="2026-10-01T13:20:00Z"), "receipt_malformed"),
])
def test_a_tampered_or_malformed_receipt_is_never_persisted(approved, change, reason):
    root, folder = approved
    receipt = build()
    change(receipt)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert caught.value.reason == reason and list(folder.iterdir()) == []


def test_directory_boundaries_are_enforced(approved, tmp_path):
    root, folder = approved
    receipt = build()
    outside = tmp_path / "outside"
    outside.mkdir()
    cases = [(outside, root, "directory_outside_approved_root"),
             (Path(str(folder) + "\\..\\push-receipts"), root, "directory_invalid"),
             (Path("relative/dir"), root, "directory_invalid"),
             (str(folder), root, "directory_invalid"),
             (folder / "missing", root, "directory_missing")]
    for directory, approved_root, reason in cases:
        with pytest.raises(pr.PushReceiptRefused) as caught:
            pr.persist_receipt(receipt, directory, approved_root=approved_root)
        assert caught.value.reason == reason, (directory, caught.value.reason)


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction")
def test_a_junction_anywhere_in_the_directory_path_is_refused(approved, tmp_path):
    import _winapi
    root, folder = approved
    real = tmp_path / "elsewhere"
    real.mkdir()
    junction = root / "via-junction"
    _winapi.CreateJunction(str(real), str(junction))
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(build(), junction, approved_root=root)
    assert caught.value.reason == "path_has_link_or_reparse" and list(real.iterdir()) == []


def test_a_volatile_temporary_directory_is_refused_by_default(tmp_path):
    folder = tmp_path / "receipts"            # tmp_path is under tempfile.gettempdir(): CLAUDE.md rule 1
    folder.mkdir()
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(build(), folder, approved_root=tmp_path)
    assert caught.value.reason == "directory_volatile" and list(folder.iterdir()) == []


@pytest.mark.parametrize("content, reason", [
    (lambda r: (json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n").replace('"schema"', '"schema":"x","schema"', 1),
     "receipt_file_malformed"),                                    # duplicate key, refused (never last-wins)
    (lambda r: json.dumps(r, sort_keys=True) + "\n", "receipt_file_not_canonical"),
    (lambda r: "x" * (pr.MAX_RECEIPT_BYTES + 1), "receipt_file_oversized"),
])
def test_load_receipt_refuses_non_canonical_duplicate_or_oversized_files(tmp_path, content, reason):
    receipt = build()
    path = tmp_path / (receipt["receipt_digest"] + ".json")
    path.write_text(content(receipt), encoding="ascii")
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.load_receipt(path)
    assert caught.value.reason == reason


def test_load_receipt_refuses_a_valid_receipt_under_another_name(approved):
    root, folder = approved
    path = Path(pr.persist_receipt(build(), folder, approved_root=root)["path"])
    renamed = folder / ("b" * 64 + ".json")
    path.rename(renamed)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.load_receipt(renamed)
    assert caught.value.reason == "receipt_name_mismatch"


# --- F1-F7 (fable-5 review of 2b500a51) ------------------------------------------------------------------

@pytest.mark.parametrize("stamp", ["0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00"])
def test_f1_an_out_of_range_offset_time_is_a_stable_refusal(stamp):
    refused("observed_remote_utc_invalid", observed_remote_utc=stamp)
    receipt = build()
    receipt["observed_remote_utc"] = stamp
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.validate_receipt(receipt)
    assert caught.value.reason == "observed_remote_utc_invalid"


windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows handle containment")


@windows_only
def test_f2_no_component_can_be_renamed_or_swapped_while_publishing(approved, tmp_path, monkeypatch):
    root, folder = approved
    outside = tmp_path / "outside"
    outside.mkdir()
    real_write, attempts = os.write, []

    def swap_then_write(descriptor, data):
        for victim in (folder, root):   # the leaf and an ancestor, while the handles are held
            try:
                os.rename(victim, victim.with_name(victim.name + "-moved"))
                attempts.append("renamed " + victim.name)
            except OSError as exc:
                attempts.append(exc.winerror)
        return real_write(descriptor, data)

    monkeypatch.setattr(pr.os, "write", swap_then_write)
    result = pr.persist_receipt(build(), folder, approved_root=root)
    assert attempts == [32, 32]                          # ERROR_SHARING_VIOLATION: the lock held
    assert Path(result["path"]).parent == folder and (folder / Path(result["path"]).name).is_file()
    assert list(outside.iterdir()) == []
    os.rename(folder, folder.with_name("after"))         # released afterwards


@windows_only
def test_e1_a_disguised_path_subclass_is_refused_and_writes_nothing(approved, tmp_path):
    # RCO1 E1: str()/fspath() name the approved directory while parts/anchor name a directory outside it.
    from pathlib import WindowsPath
    root, folder = approved
    outside = tmp_path / "outside"
    outside.mkdir()

    class Disguised(WindowsPath):
        def __str__(self):
            return str(WindowsPath(folder))

        def __fspath__(self):
            return str(WindowsPath(folder))

    for directory, approved_root in ((Disguised(outside), root), (folder, Disguised(root))):
        with pytest.raises(pr.PushReceiptRefused) as caught:
            pr.persist_receipt(build(), directory, approved_root=approved_root)
        assert caught.value.reason == "directory_invalid"
    assert list(outside.iterdir()) == [] and list(folder.iterdir()) == []
    path = Path(pr.persist_receipt(build(), folder, approved_root=root)["path"])   # plain Path control
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.load_receipt(Disguised(path))
    assert caught.value.reason == "receipt_path_invalid" and pr.load_receipt(path)["schema"] == pr.SCHEMA


@windows_only
def test_e2_an_existing_name_that_is_a_link_is_a_conflict_never_read_through(approved, tmp_path):
    # RCO1 E2: the collision read goes through the held directory handle without following a reparse
    # point, so a link (even to identical bytes) under the receipt's name is never reported unchanged.
    root, folder = approved
    receipt = build()
    target = tmp_path / "identical.json"
    target.write_bytes(pr.canonical_bytes(receipt) + b"\n")
    try:
        os.symlink(target, folder / (receipt["receipt_digest"] + ".json"))
    except OSError:
        pytest.skip("symlink creation not permitted for this token")
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert caught.value.reason == "receipt_conflict"


@windows_only
def test_e2_a_link_swapped_in_just_before_the_collision_read_is_a_conflict(approved, tmp_path, monkeypatch):
    # RCO1 E2 race: the receipt name holds the identical regular file; right before it is read (after the
    # e755 lstat, or before the handle-relative open now) it is swapped for a link to identical bytes.
    root, folder = approved
    receipt = build()
    data = pr.canonical_bytes(receipt) + b"\n"
    final = folder / (receipt["receipt_digest"] + ".json")
    final.write_bytes(data)
    decoy = tmp_path / "decoy.json"
    decoy.write_bytes(data)
    swapped = []

    def swap():
        if not swapped:
            swapped.append(True)
            final.unlink()
            os.symlink(decoy, final)

    real_lstat = os.lstat

    def lstat_then_swap(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if Path(path) == final:
            swap()
        return info

    monkeypatch.setattr(pr.os, "lstat", lstat_then_swap)
    if hasattr(pr, "_read_relative"):
        real_read = pr._read_relative
        monkeypatch.setattr(pr, "_read_relative", lambda directory, name: (swap(), real_read(directory, name))[1])
    try:
        with pytest.raises(pr.PushReceiptRefused) as caught:
            pr.persist_receipt(receipt, folder, approved_root=root)
    except OSError:
        pytest.skip("symlink creation not permitted for this token")
    assert swapped == [True] and caught.value.reason == "receipt_conflict"


@windows_only
def test_e2_the_collision_read_never_reopens_the_name_by_path(approved, monkeypatch):
    root, folder = approved
    receipt = build()
    pr.persist_receipt(receipt, folder, approved_root=root)
    monkeypatch.setattr(pr, "_read_bounded", lambda path: (_ for _ in ()).throw(AssertionError("path read")))
    assert pr.persist_receipt(receipt, folder, approved_root=root)["status"] == "unchanged"


@windows_only
def test_c1_a_self_referencing_junction_component_is_opened_as_itself(approved):
    # Only FILE_FLAG_OPEN_REPARSE_POINT opens a looping junction as itself (refused as a reparse point);
    # following it fails with ERROR_CANT_RESOLVE_FILENAME instead.
    root, folder = approved
    loop = root / "loop"
    loop.mkdir()
    assert _set_junction_in_place(loop, loop)
    try:
        with pytest.raises(pr.PushReceiptRefused) as caught:
            pr.persist_receipt(build(), loop, approved_root=root)
        assert caught.value.reason == "path_has_link_or_reparse"
    finally:
        os.rmdir(loop)


@windows_only
def test_m1_only_the_target_directory_is_opened_for_adding_files(approved, monkeypatch):
    # A non-elevated token cannot open C:\ with FILE_ADD_FILE; ancestors take traverse + attributes only.
    root, folder = approved
    real, opened = pr._kernel32.CreateFileW, []

    def record(path, access, *rest):
        opened.append((path, access))
        return real(path, access, *rest)

    monkeypatch.setattr(pr._kernel32, "CreateFileW", record)
    assert pr.persist_receipt(build(), folder, approved_root=root)["status"] == "created"
    assert opened[0][0] == "C:\\" and opened[-1] == (str(folder), pr._DIR_ACCESS)
    assert all(access == 0x20 | 0x80 | 0x100000 for _path, access in opened[:-1])


def _set_junction_in_place(directory: Path, target: Path) -> bool:
    """FSCTL_SET_REPARSE_POINT (mount point) on an existing EMPTY directory, as another writer would."""
    import ctypes
    import struct
    from ctypes import wintypes
    k = pr._kernel32
    k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    handle = k.CreateFileW(str(directory), 0x40000000, 0x7, None, 3, 0x02000000 | 0x00200000, None)
    assert handle not in (None, pr._INVALID_HANDLE)
    try:
        name = ("\\??\\" + str(target)).encode("utf-16-le")
        body = struct.pack("<HHHH", 0, len(name), len(name) + 2, 0) + name + b"\0\0\0\0"
        data = struct.pack("<IHH", 0xA0000003, len(body), 0) + body
        return bool(k.DeviceIoControl(handle, 0x900A4, data, len(data), None, 0, ctypes.byref(wintypes.DWORD()), None))
    finally:
        k.CloseHandle(handle)


@windows_only
def test_f2_residual_a_junction_set_in_place_on_the_held_empty_directory_writes_nothing(approved, tmp_path,
                                                                                      monkeypatch):
    # The named residual, measured: the held empty leaf CAN be turned into a junction in place (the leaf
    # stays write-shared for the link), but the create relative to the held handle is then refused by NTFS
    # (STATUS_REPARSE_POINT_NOT_RESOLVED), so no name and no byte lands anywhere.
    root, folder = approved
    outside = tmp_path / "outside"
    outside.mkdir()
    real_create, junction = pr._create_relative, []

    def race(directory, name):
        junction.append(_set_junction_in_place(folder, outside))
        return real_create(directory, name)

    monkeypatch.setattr(pr, "_create_relative", race)
    try:
        with pytest.raises(pr.PushReceiptRefused) as caught:
            pr.persist_receipt(build(), folder, approved_root=root)
        assert junction == [True] and caught.value.reason == "receipt_create_failed:c0000280"
        assert list(outside.iterdir()) == []
    finally:
        os.rmdir(folder)   # removes the junction only


@windows_only
def test_f2_a_temporary_file_outside_the_held_directory_is_refused_and_removed(approved, monkeypatch):
    root, folder = approved
    real = pr._final_path
    monkeypatch.setattr(pr, "_final_path", lambda h: "C:\\elsewhere\\x.tmp" if real(h).endswith(".tmp") else real(h))
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(build(), folder, approved_root=root)
    assert caught.value.reason == "directory_drifted" and list(folder.iterdir()) == []   # delete-on-close


@windows_only
def test_f2_a_component_held_for_deletion_by_another_process_refuses(approved):
    root, folder = approved
    other = pr._kernel32.CreateFileW(str(folder), 0x10000 | 0x80, 0x7, None, 3, 0x02000000, None)   # DELETE access
    assert other not in (None, pr._INVALID_HANDLE)
    try:
        with pytest.raises(pr.PushReceiptRefused) as caught:
            pr.persist_receipt(build(), folder, approved_root=root)
        assert caught.value.reason == "directory_lock_failed:32" and list(folder.iterdir()) == []
    finally:
        pr._kernel32.CloseHandle(other)


def test_f2_without_windows_handles_persistence_fails_closed(approved, monkeypatch):
    root, folder = approved
    monkeypatch.setattr(pr, "_WINDOWS", False)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(build(), folder, approved_root=root)
    assert caught.value.reason == "platform_unsupported" and list(folder.iterdir()) == []


@windows_only
def test_f3_a_close_failure_after_publishing_keeps_the_status_and_reports_it(approved, monkeypatch):
    root, folder = approved
    real_close = os.close

    def close_then_fail(descriptor):
        real_close(descriptor)
        raise OSError(5, "close failed")

    monkeypatch.setattr(pr.os, "close", close_then_fail)
    result = pr.persist_receipt(build(), folder, approved_root=root)
    assert (result["status"], result["cleanup"]) == ("created", "close_failed:OSError")
    assert [p.name for p in folder.iterdir()] == [Path(result["path"]).name]


@windows_only
def test_f4_a_close_failure_never_replaces_a_conflict(approved, monkeypatch):
    root, folder = approved
    receipt = build()
    (folder / (receipt["receipt_digest"] + ".json")).write_bytes(b"{}\n")
    real_close = os.close

    def close_then_fail(descriptor):
        real_close(descriptor)
        raise OSError(5, "close failed")

    monkeypatch.setattr(pr.os, "close", close_then_fail)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert (caught.value.reason, caught.value.cleanup) == ("receipt_conflict", "close_failed:OSError")
    assert [p.name for p in folder.iterdir()] == [receipt["receipt_digest"] + ".json"]


@windows_only
def test_f5_an_8dot3_alias_of_a_volatile_root_is_still_refused(tmp_path, monkeypatch):
    import ctypes
    volatile = tmp_path / "LongVolatileRootName"
    folder = volatile / "receipts"
    folder.mkdir(parents=True)
    buffer = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(volatile), buffer, 1024) or buffer.value.lower() == str(volatile).lower():
        pytest.skip("no 8.3 names on this volume")
    monkeypatch.setattr(pr, "_forbidden_roots", lambda: [Path(buffer.value)])
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(build(), folder, approved_root=tmp_path)
    assert caught.value.reason == "directory_volatile" and list(folder.iterdir()) == []


@windows_only
def test_f5_a_directory_given_through_an_8dot3_alias_is_refused(approved):
    import ctypes
    root, _folder = approved
    folder = root / "LongReceiptDirectoryName"
    folder.mkdir()
    buffer = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(folder), buffer, 1024) or buffer.value.lower() == str(folder).lower():
        pytest.skip("no 8.3 names on this volume")
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(build(), Path(buffer.value), approved_root=Path(buffer.value).parent)
    assert caught.value.reason == "path_has_link_or_reparse" and list(folder.iterdir()) == []


@windows_only
def test_a_file_in_place_of_the_directory_is_refused(approved):
    root, folder = approved
    plain = folder / "plain-file"
    plain.write_bytes(b"")
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(build(), plain, approved_root=root)
    assert caught.value.reason == "directory_missing" and [p.name for p in folder.iterdir()] == ["plain-file"]


@windows_only
@pytest.mark.parametrize("answer", [0, None, 10 ** 6, True])
def test_f6_a_write_without_progress_is_refused_not_spun(approved, monkeypatch, answer):
    root, folder = approved
    calls = []

    def stuck(descriptor, data):
        calls.append(1)
        if len(calls) > 5:
            raise AssertionError("spun")
        return answer

    monkeypatch.setattr(pr.os, "write", stuck)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(build(), folder, approved_root=root)
    assert caught.value.reason == "receipt_write_stalled" and len(calls) == 1 and list(folder.iterdir()) == []


@windows_only
@pytest.mark.parametrize("close_fails", [False, True])
def test_f7_cancellation_propagates_even_when_cleanup_fails(approved, monkeypatch, close_fails):
    root, folder = approved
    real_close = os.close

    def cancel(descriptor):
        raise KeyboardInterrupt

    def close(descriptor):
        real_close(descriptor)
        if close_fails:
            raise OSError(5, "close failed")

    monkeypatch.setattr(pr.os, "fsync", cancel)
    monkeypatch.setattr(pr.os, "close", close)
    with pytest.raises(KeyboardInterrupt) as caught:
        pr.persist_receipt(build(), folder, approved_root=root)
    notes = getattr(caught.value, "__notes__", [])
    assert notes == (["push receipt cleanup: close_failed:OSError"] if close_fails else [])
    assert list(folder.iterdir()) == []                  # no temporary file, no receipt


def test_the_module_reads_no_clock_environment_network_or_process():
    source = Path(pr.__file__).read_text(encoding="utf-8")
    for forbidden in ("datetime.now", "utcnow", "time.time", "os.environ", "subprocess", "socket", "urllib",
                      "time.sleep"):
        assert forbidden not in source, forbidden


# --- every close is checked: cleanup is never "ok" when a closure is unknown (RCO1, Lead 18:27Z) ----------

def _close_handle_false(monkeypatch):
    """Every CloseHandle really closes, then reports FALSE (no handle leaks)."""
    real = pr._kernel32.CloseHandle

    def close_then_false(handle):
        real(handle)
        return False

    monkeypatch.setattr(pr._kernel32, "CloseHandle", close_then_false)


def _read_close_fails(monkeypatch):
    """Only the descriptor close inside _read_relative raises (after a real close)."""
    real_close, real_read, inside = os.close, pr._read_relative, []

    def close(descriptor):
        real_close(descriptor)
        if inside:
            raise OSError(5, "read close failed")

    def read(directory, name):
        inside.append(True)
        try:
            return real_read(directory, name)
        finally:
            inside.pop()

    monkeypatch.setattr(pr.os, "close", close)
    monkeypatch.setattr(pr, "_read_relative", read)


@windows_only
def test_close_a_clean_publish_reports_ok(approved):
    root, folder = approved
    assert pr.persist_receipt(build(), folder, approved_root=root)["cleanup"] == "ok"


@windows_only
def test_close_a_false_directory_close_after_publishing_is_reported_and_keeps_the_receipt(approved, monkeypatch):
    root, folder = approved
    _close_handle_false(monkeypatch)
    result = pr.persist_receipt(build(), folder, approved_root=root)
    assert (result["status"], result["cleanup"]) == ("created", "close_failed:CloseHandle")
    assert [p.name for p in folder.iterdir()] == [Path(result["path"]).name]   # published fact not undone


@windows_only
def test_close_a_failed_read_close_on_an_identical_retry_is_reported(approved, monkeypatch):
    root, folder = approved
    receipt = build()
    pr.persist_receipt(receipt, folder, approved_root=root)
    _read_close_fails(monkeypatch)
    result = pr.persist_receipt(receipt, folder, approved_root=root)
    assert (result["status"], result["cleanup"]) == ("unchanged", "close_failed:OSError")


@windows_only
@pytest.mark.parametrize("fault, expected", [(_read_close_fails, "close_failed:OSError"),
                                             (_close_handle_false, "close_failed:CloseHandle")])
def test_close_a_close_failure_on_a_conflict_is_recorded_and_never_replaces_it(approved, monkeypatch, fault, expected):
    root, folder = approved
    receipt = build()
    (folder / (receipt["receipt_digest"] + ".json")).write_bytes(b"{}\n")
    fault(monkeypatch)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert (caught.value.reason, caught.value.cleanup) == ("receipt_conflict", expected)
    assert caught.value.__notes__[0].startswith("push receipt cleanup: " + expected)


@windows_only
def test_close_a_false_close_during_cancellation_is_noted_and_the_cancellation_propagates(approved, monkeypatch):
    root, folder = approved

    def cancel(descriptor):
        raise KeyboardInterrupt

    monkeypatch.setattr(pr.os, "fsync", cancel)
    _close_handle_false(monkeypatch)
    with pytest.raises(KeyboardInterrupt) as caught:
        pr.persist_receipt(build(), folder, approved_root=root)
    assert caught.value.__notes__[0].startswith("push receipt cleanup: close_failed:CloseHandle")
    assert list(folder.iterdir()) == []


@windows_only
def test_close_a_false_close_while_refusing_the_lock_is_reported_on_the_refusal(approved, monkeypatch):
    root, folder = approved
    _close_handle_false(monkeypatch)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(build(), folder / "missing", approved_root=root)
    assert (caught.value.reason, caught.value.cleanup) == ("directory_missing", "close_failed:CloseHandle")


@windows_only
def test_close_a_false_close_of_the_collision_link_handle_is_reported(approved, tmp_path, monkeypatch):
    # The existing name is a link, so _read_relative closes its own handle (not a descriptor).
    root, folder = approved
    receipt = build()
    decoy = tmp_path / "decoy.json"
    decoy.write_bytes(pr.canonical_bytes(receipt) + b"\n")
    try:
        os.symlink(decoy, folder / (receipt["receipt_digest"] + ".json"))
    except OSError:
        pytest.skip("symlink creation not permitted for this token")
    real_close, real_read, inside = pr._kernel32.CloseHandle, pr._read_relative, []

    def close(handle):
        result = real_close(handle)
        return False if inside else result

    def read(directory, name):
        inside.append(True)
        try:
            return real_read(directory, name)
        finally:
            inside.pop()

    monkeypatch.setattr(pr._kernel32, "CloseHandle", close)
    monkeypatch.setattr(pr, "_read_relative", read)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert (caught.value.reason, caught.value.cleanup) == ("receipt_conflict", "close_failed:CloseHandle")


# --- Fable F1 (f2c0f815) adopted: every close path counted on a real C: NTFS fixture -----------------------
# Real directories under the claimed audit dir (never TEMP, no _forbidden_roots bypass). Every fake does the
# REAL close first and only then reports FALSE, so nothing leaks; a surviving directory handle (no delete
# sharing) would make the held folder refuse a rename, which ``_f1_released`` reports as False.

_F1_REPO = Path(__file__).resolve().parents[2]
_F1_FIXTURES = _F1_REPO / ".codex-audit" / "rco1-receipt-f1-adoption-20261001" / "fx"


@pytest.fixture
def ntfs_c():
    import shutil
    import uuid
    if os.name != "nt" or _F1_REPO.drive.upper() != "C:":
        pytest.skip("needs a real NTFS checkout on drive C:")
    base = _F1_FIXTURES / uuid.uuid4().hex[:8]
    folder = base / "a" / "r"
    folder.mkdir(parents=True)
    yield base / "a", folder
    shutil.rmtree(base, ignore_errors=True)


class _F1Closes:
    """CloseHandle wrapper: the real close always runs; FALSE is reported where ``fail(handle)``; every call is
    counted, so a skipped, leaked or retried close is visible."""

    def __init__(self, monkeypatch, fail=lambda handle: False):
        self.real, self.calls, self.fail = pr._kernel32.CloseHandle, [], fail
        monkeypatch.setattr(pr._kernel32, "CloseHandle", self)

    def __call__(self, handle):
        ok = self.real(handle)
        self.calls.append(handle)
        return False if self.fail(handle) else ok


def _f1_capture_locks(monkeypatch):
    real, locked = pr._lock_directory, []

    def lock(folder):
        handles = real(folder)
        locked.extend(handles)
        return handles

    monkeypatch.setattr(pr, "_lock_directory", lock)
    return locked


def _f1_released(folder):
    """True when no handle on the folder survives: a held directory refuses rename (no delete sharing)."""
    moved = folder.with_name("moved")
    try:
        os.rename(folder, moved)
    except OSError:
        return False
    os.rename(moved, folder)
    return True


def _f1_osfhandle_refuses(monkeypatch, mode):
    real, raised = pr.msvcrt.open_osfhandle, []

    def open_osfhandle(handle, flags):
        if flags == mode:
            raised.append(OSError(24, "too many open files"))
            raise raised[-1]
        return real(handle, flags)

    monkeypatch.setattr(pr.msvcrt, "open_osfhandle", open_osfhandle)
    return raised


@windows_only
def test_f1_clean_publish_and_retry_report_ok_and_release_every_handle(ntfs_c, monkeypatch):
    root, folder = ntfs_c
    receipt = build()
    closes, locked = _F1Closes(monkeypatch), _f1_capture_locks(monkeypatch)
    first = pr.persist_receipt(receipt, folder, approved_root=root)
    again = pr.persist_receipt(receipt, folder, approved_root=root)
    assert (first["status"], first["cleanup"], again["status"], again["cleanup"]) == ("created", "ok", "unchanged", "ok")
    # two calls: a closed handle value can be reused, so compare as multisets (each lock closed as often as opened)
    assert sorted(h for h in closes.calls if h in set(locked)) == sorted(locked) and _f1_released(folder)


@windows_only
def test_f1_every_false_close_is_still_attempted_exactly_once_and_reported(ntfs_c, monkeypatch):
    root, folder = ntfs_c
    closes, locked = _F1Closes(monkeypatch, fail=lambda h: True), _f1_capture_locks(monkeypatch)
    result = pr.persist_receipt(build(), folder, approved_root=root)
    assert (result["status"], result["cleanup"]) == ("created", "close_failed:CloseHandle")
    # every lock handle closed once, although the first close already "failed" (no stop, no skip, no retry)
    assert len(locked) >= 3 and all(closes.calls.count(h) == 1 for h in locked)
    assert [p.name for p in folder.iterdir()] == [Path(result["path"]).name] and _f1_released(folder)


@windows_only
def test_f1_false_closes_on_a_conflict_list_one_failure_per_handle_and_close_all(ntfs_c, monkeypatch):
    root, folder = ntfs_c
    receipt = build()
    (folder / (receipt["receipt_digest"] + ".json")).write_bytes(b"{}\n")
    closes, locked = _F1Closes(monkeypatch, fail=lambda h: True), _f1_capture_locks(monkeypatch)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert (caught.value.reason, caught.value.cleanup) == ("receipt_conflict", "close_failed:CloseHandle")
    notes = caught.value.__notes__
    assert len(notes) == 1 and notes[0].count("close_failed:CloseHandle") == len(locked)
    assert all(closes.calls.count(h) == 1 for h in locked) and _f1_released(folder)


@windows_only
def test_f1_write_open_osfhandle_refusal_closes_the_temp_once_reports_it_and_keeps_the_primary(ntfs_c, monkeypatch):
    root, folder = ntfs_c
    raised = _f1_osfhandle_refuses(monkeypatch, os.O_WRONLY)
    closes, locked = _F1Closes(monkeypatch, fail=lambda h: True), _f1_capture_locks(monkeypatch)
    with pytest.raises(OSError) as caught:
        pr.persist_receipt(build(), folder, approved_root=root)
    assert caught.value is raised[0]                                       # the primary object, unchanged
    extra = [h for h in closes.calls if h not in locked]
    assert len(extra) == 1                                                # the temporary handle, closed once
    assert caught.value.__notes__ == [
        "push receipt cleanup: " + ", ".join(["close_failed:CloseHandle"] * (1 + len(locked)))]
    assert list(folder.iterdir()) == [] and _f1_released(folder)          # delete-on-close removed the temp


@windows_only
def test_f1_read_open_osfhandle_refusal_closes_the_read_handle_once_and_records_it(ntfs_c, monkeypatch):
    root, folder = ntfs_c
    receipt = build()
    pr.persist_receipt(receipt, folder, approved_root=root)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    raised = _f1_osfhandle_refuses(monkeypatch, os.O_RDONLY)
    closes, locked = _F1Closes(monkeypatch, fail=lambda h: True), _f1_capture_locks(monkeypatch)
    with pytest.raises(OSError) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert caught.value is raised[0]
    extra = [h for h in closes.calls if h not in locked]
    assert len(extra) == 1                                                # the read handle (the temp is a descriptor)
    # N1, pinned as it works: _read_relative notes its own failure, then persist_receipt notes the directory closes
    assert caught.value.__notes__ == [
        "push receipt cleanup: close_failed:CloseHandle",
        "push receipt cleanup: " + ", ".join(["close_failed:CloseHandle"] * len(locked))]
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before and _f1_released(folder)


@windows_only
def test_f1_read_open_osfhandle_refusal_with_true_closes_adds_no_cleanup_note(ntfs_c, monkeypatch):
    root, folder = ntfs_c
    receipt = build()
    pr.persist_receipt(receipt, folder, approved_root=root)
    raised = _f1_osfhandle_refuses(monkeypatch, os.O_RDONLY)
    with pytest.raises(OSError) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert caught.value is raised[0] and getattr(caught.value, "__notes__", []) == [] and _f1_released(folder)


@windows_only
def test_f1_a_cancellation_inside_the_read_closes_everything_once_and_propagates(ntfs_c, monkeypatch):
    root, folder = ntfs_c
    receipt = build()
    pr.persist_receipt(receipt, folder, approved_root=root)

    def cancel(descriptor, size):
        raise KeyboardInterrupt

    monkeypatch.setattr(pr.os, "read", cancel)
    closes, locked = _F1Closes(monkeypatch, fail=lambda h: True), _f1_capture_locks(monkeypatch)
    with pytest.raises(KeyboardInterrupt) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert all(closes.calls.count(h) == 1 for h in locked)
    assert caught.value.__notes__ == ["push receipt cleanup: " + ", ".join(["close_failed:CloseHandle"] * len(locked))]
    assert len(list(folder.iterdir())) == 1 and _f1_released(folder)       # published receipt not undone


def _f1_read_close_raises(monkeypatch):
    """Only the descriptor close inside _read_relative raises, after a real close."""
    real_close, real_read, inside = os.close, pr._read_relative, []

    def close(descriptor):
        real_close(descriptor)
        if inside:
            raise OSError(5, "read close failed")

    def read(directory, name):
        inside.append(True)
        try:
            return real_read(directory, name)
        finally:
            inside.pop()

    monkeypatch.setattr(pr.os, "close", close)
    monkeypatch.setattr(pr, "_read_relative", read)


@windows_only
def test_f1_a_known_read_failure_still_closes_every_lock_on_an_identical_retry(ntfs_c, monkeypatch):
    root, folder = ntfs_c
    receipt = build()
    pr.persist_receipt(receipt, folder, approved_root=root)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    _f1_read_close_raises(monkeypatch)
    closes, locked = _F1Closes(monkeypatch), _f1_capture_locks(monkeypatch)
    result = pr.persist_receipt(receipt, folder, approved_root=root)
    assert (result["status"], result["cleanup"]) == ("unchanged", "close_failed:OSError")
    assert all(closes.calls.count(h) == 1 for h in locked) and _f1_released(folder)
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == before


@windows_only
def test_f1_a_known_read_failure_on_a_conflict_still_closes_every_lock(ntfs_c, monkeypatch):
    root, folder = ntfs_c
    receipt = build()
    (folder / (receipt["receipt_digest"] + ".json")).write_bytes(b"{}\n")
    _f1_read_close_raises(monkeypatch)
    closes, locked = _F1Closes(monkeypatch), _f1_capture_locks(monkeypatch)
    with pytest.raises(pr.PushReceiptRefused) as caught:
        pr.persist_receipt(receipt, folder, approved_root=root)
    assert (caught.value.reason, caught.value.cleanup) == ("receipt_conflict", "close_failed:OSError")
    assert caught.value.__notes__ == ["push receipt cleanup: close_failed:OSError"]
    assert all(closes.calls.count(h) == 1 for h in locked) and _f1_released(folder)
