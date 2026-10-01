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


def test_the_module_reads_no_clock_environment_network_or_process():
    source = Path(pr.__file__).read_text(encoding="utf-8")
    for forbidden in ("datetime.now", "utcnow", "time.time", "os.environ", "subprocess", "socket", "urllib",
                      "time.sleep"):
        assert forbidden not in source, forbidden
