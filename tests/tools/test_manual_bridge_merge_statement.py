# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/manual_bridge_merge_statement.py (MANUAL-A statement slice).

Evidence classes:
* ``unit_mock`` tests inject fake git / ssh-keygen runners.  They prove the
  contract logic only and are never a real cryptographic proof.
* ``test_real_ssh_keygen_rejects_garbage_signature`` runs the real binary
  (negative only).  Positive real-SSH verification is a named skip (NOT_RUN):
  it needs operator-supplied public evidence; no agent creates key material.

The public keys below are synthetic byte strings in SSH wire format.  No
private key exists for them; nothing here generates, reads or stores keys.
"""
from __future__ import annotations

import ast
import base64
import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest

from tools import manual_bridge_merge_statement as mms
from tools.manual_bridge_merge_statement import (
    ALLOWED_SIGNERS_PATH,
    LedgerError,
    NonceLedger,
    RunResult,
    StatementError,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "manual_bridge_merge_statement.py"
BASE = "b" * 40
HEAD = "a" * 40
NOW = datetime(2026, 10, 5, 6, 0, 0, tzinfo=timezone.utc)
EXPIRY = "2026-10-05T07:00:00Z"
NONCE = "0123456789abcdef0123456789abcdef"
DIFF = "d" * 64
PATHS = ("tools/manual_bridge_merge_statement.py", "tests/tools/test_manual_bridge_merge_statement.py")
SORTED_PATHS = tuple(sorted(PATHS))


def ssh_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


def synthetic_key_blob(key_type: str = "ssh-ed25519", application: bytes = b"ssh:") -> bytes:
    blob = ssh_string(key_type.encode("ascii")) + ssh_string(bytes(range(32)))
    if key_type == "sk-ssh-ed25519@openssh.com":
        blob += ssh_string(application)
    return blob


def anchor_line(key_type: str = "ssh-ed25519", blob: bytes | None = None, *,
                principal: str = mms.PRINCIPAL, options: str = mms.ANCHOR_OPTIONS,
                comment: str = "operator-public-synthetic") -> str:
    blob = synthetic_key_blob(key_type) if blob is None else blob
    line = f"{principal} {options} {key_type} {base64.b64encode(blob).decode('ascii')}"
    return f"{line} {comment}" if comment else line


def anchor_bytes(key_type: str = "ssh-ed25519") -> bytes:
    return ("# operator-supplied public line\n" + anchor_line(key_type) + "\n").encode("ascii")


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\x00" + data, usedforsecurity=False).hexdigest()


class FakeGit:
    """unit_mock git: answers only the four commands the loader may run."""

    def __init__(self, data: bytes | None, *, commit: str = BASE, blob_sha: str | None = None,
                 kind: bytes = b"blob\n", commit_ok: bool = True, content: bytes | None = None):
        self.data = data
        self.commit = commit
        self.blob_sha = blob_sha if blob_sha is not None else (git_blob_sha(data) if data is not None else None)
        self.kind = kind
        self.commit_ok = commit_ok
        self.content = content
        self.calls: list[list[str]] = []
        self.envs: list[dict] = []

    def __call__(self, argv, *, input_bytes, timeout, env):
        self.calls.append(list(argv))
        self.envs.append(dict(env or {}))
        args = list(argv[3:])
        if args[:3] == ["rev-parse", "--verify", "--quiet"] and args[3].endswith("^{commit}"):
            if self.commit_ok and args[3] == f"{self.commit}^{{commit}}":
                return RunResult(0, (self.commit + "\n").encode(), b"")
            return RunResult(1, b"", b"")
        if args[:3] == ["rev-parse", "--verify", "--quiet"]:
            if self.blob_sha is None or args[3] != f"{self.commit}:{ALLOWED_SIGNERS_PATH}":
                return RunResult(128, b"", b"fatal")
            return RunResult(0, (self.blob_sha + "\n").encode(), b"")
        if args[:2] == ["cat-file", "-t"]:
            return RunResult(0, self.kind, b"")
        if args[:2] == ["cat-file", "blob"]:
            return RunResult(0, self.content if self.content is not None else self.data, b"")
        raise AssertionError(f"unexpected git call {argv}")


def load_anchor(key_type: str = "ssh-ed25519", **kwargs) -> mms.TrustAnchor:
    fake = FakeGit(anchor_bytes(key_type), **kwargs)
    return mms.load_trust_anchor(repo_root=ROOT, trusted_commit=BASE, runner=fake)


def make_statement(anchor: mms.TrustAnchor | None = None, **overrides) -> mms.Statement:
    anchor = anchor or load_anchor()
    fields = dict(
        pull_request=1763,
        head_sha=HEAD,
        base_sha=BASE,
        diff_digest_sha256=DIFF,
        exact_paths=SORTED_PATHS,
        batch_id="mma-20261005-statement",
        batch_order=1,
        dependencies=(),
        expires_at_utc=EXPIRY,
        nonce=NONCE,
        allowed_signers_blob_sha=anchor.blob_sha,
        key_fingerprint=anchor.fingerprint,
    )
    fields.update(overrides)
    return mms.build_statement(**fields)


def good_line(anchor: mms.TrustAnchor) -> bytes:
    return (f'Good "{mms.NAMESPACE}" signature for {mms.PRINCIPAL} with {anchor.key_label} key '
            f"{anchor.fingerprint}\n").encode("ascii")


SIGNATURE = b"-----BEGIN SSH SIGNATURE-----\nU1NIU0lHAAAAAQ==\n-----END SSH SIGNATURE-----\n"


class FakeSsh:
    """unit_mock ssh-keygen runner (never a real cryptographic check)."""

    def __init__(self, stdout: bytes, returncode: int = 0, exc: BaseException | None = None):
        self.stdout = stdout
        self.returncode = returncode
        self.exc = exc
        self.calls: list[dict] = []

    def __call__(self, argv, *, input_bytes, timeout, env):
        anchor_copy = Path(argv[4])
        self.calls.append({
            "argv": list(argv),
            "stdin": input_bytes,
            "env": dict(env or {}),
            "anchor_copy": anchor_copy,
            "anchor_copy_bytes": anchor_copy.read_bytes(),
            "sig_copy_bytes": Path(argv[10]).read_bytes(),
        })
        if self.exc is not None:
            raise self.exc
        return RunResult(self.returncode, self.stdout, b"")


def fake_keygen(tmp_path: Path) -> Path:
    path = tmp_path / "ssh-keygen-fake.exe"
    path.write_bytes(b"not a binary")
    return path


def raw_json(statement: mms.Statement) -> dict:
    return json.loads(mms.canonical_statement_bytes(statement))


def encode(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=True, separators=(",", ":")) + "\n").encode()


# --- T01 canonical statement ----------------------------------------------------


def test_field_order_and_constants_match_operator_contract():
    assert mms.FIELD_ORDER == (
        "schema", "namespace", "principal", "purpose", "repository", "pull_request",
        "head_sha", "base_sha", "diff_digest_sha256", "exact_paths", "merge_method",
        "batch_id", "batch_order", "dependencies", "operation_scope", "expires_at_utc",
        "nonce", "allowed_signers_path", "allowed_signers_blob_sha", "key_fingerprint",
    )
    assert mms.NAMESPACE == "waggledance-manual-merge-a"
    assert mms.PRINCIPAL == "operator@waggledance"
    assert mms.PURPOSE == "manual-merge-receipt"
    assert mms.ALLOWED_KEY_TYPES == ("sk-ssh-ed25519@openssh.com", "ssh-ed25519")
    assert not hasattr(mms, "MAX_STATEMENT_LIFETIME_HOURS")


def test_canonical_roundtrip_exact_bytes():
    statement = make_statement()
    data = mms.canonical_statement_bytes(statement)
    assert data.startswith(b'{"schema":"wd.manual-merge-a.statement.v1","namespace":')
    assert data.endswith(b"}\n") and data.count(b"\n") == 1 and b"\r" not in data
    assert list(json.loads(data)) == list(mms.FIELD_ORDER)
    assert mms.parse_statement(data) == statement


@pytest.mark.parametrize("mutate", [
    lambda d: b"\xef\xbb\xbf" + d,
    lambda d: d.replace(b"\n", b"\r\n"),
    lambda d: d[:-1],
    lambda d: d + b"\n",
    lambda d: b" " + d,
    lambda d: d.replace(b'"schema":', b'"schema": ', 1),
    lambda d: d.replace(b'"pull_request":1763', b'"pull_request":1763.0'),
    lambda d: d.replace(b'"pull_request":1763', b'"pull_request":1.763e3'),
    lambda d: d.replace(b"tools/manual", b"tools\\/manual"),
])
def test_rejects_non_canonical_bytes(mutate):
    data = mms.canonical_statement_bytes(make_statement())
    with pytest.raises(StatementError) as err:
        mms.parse_statement(mutate(data))
    assert err.value.reason in {"non_canonical_bytes", "invalid_field:pull_request"}


def test_rejects_reordered_fields():
    obj = raw_json(make_statement())
    reordered = {"namespace": obj["namespace"], **{k: v for k, v in obj.items() if k != "namespace"}}
    with pytest.raises(StatementError) as err:
        mms.parse_statement(encode(reordered))
    assert err.value.reason == "non_canonical_bytes"


def test_rejects_duplicate_missing_and_extra_keys():
    data = mms.canonical_statement_bytes(make_statement())
    duplicate = data.replace(b'{"schema":', b'{"schema":"x","schema":', 1)
    with pytest.raises(StatementError) as err:
        mms.parse_statement(duplicate)
    assert err.value.reason == "duplicate_key"
    obj = raw_json(make_statement())
    missing = {k: v for k, v in obj.items() if k != "nonce"}
    with pytest.raises(StatementError) as err:
        mms.parse_statement(encode(missing))
    assert err.value.reason == "missing_field:nonce"
    extra = dict(obj, approval="yes")
    with pytest.raises(StatementError) as err:
        mms.parse_statement(encode(extra))
    assert err.value.reason == "unexpected_field:approval"


def test_rejects_json_constants():
    data = mms.canonical_statement_bytes(make_statement())
    with pytest.raises(StatementError) as err:
        mms.parse_statement(data.replace(b'"batch_order":1', b'"batch_order":NaN'))
    assert err.value.reason == "non_canonical_bytes"


@pytest.mark.parametrize("field,value,reason", [
    ("pull_request", True, "invalid_field:pull_request"),
    ("pull_request", "1763", "invalid_field:pull_request"),
    ("pull_request", 0, "invalid_field:pull_request"),
    ("head_sha", "A" * 40, "invalid_field:head_sha"),
    ("head_sha", "a" * 39, "invalid_field:head_sha"),
    ("head_sha", BASE, "invalid_field:head_sha"),
    ("base_sha", "b" * 41, "invalid_field:base_sha"),
    ("diff_digest_sha256", "d" * 63, "invalid_field:diff_digest_sha256"),
    ("nonce", "0123", "invalid_field:nonce"),
    ("nonce", "0123456789ABCDEF0123456789ABCDEF", "invalid_field:nonce"),
    ("key_fingerprint", "MD5:00", "invalid_field:key_fingerprint"),
    ("allowed_signers_blob_sha", "c" * 39, "invalid_field:allowed_signers_blob_sha"),
    ("batch_id", "batch-1", "invalid_field:batch_id"),
    ("batch_order", 0, "invalid_field:batch_order"),
    ("batch_order", False, "invalid_field:batch_order"),
    ("dependencies", (1763,), "invalid_field:dependencies"),
    ("dependencies", (5, 4), "invalid_field:dependencies"),
    ("dependencies", (4, 4), "invalid_field:dependencies"),
    ("dependencies", (True,), "invalid_field:dependencies"),
    ("expires_at_utc", "2026-10-05T07:00:00", "invalid_field:expires_at_utc"),
    ("expires_at_utc", "2026-10-05T07:00:00+00:00", "invalid_field:expires_at_utc"),
    ("expires_at_utc", "2026-10-05 07:00:00Z", "invalid_field:expires_at_utc"),
    ("expires_at_utc", "2026-02-30T07:00:00Z", "invalid_field:expires_at_utc"),
    ("expires_at_utc", "2026-12-31T23:59:60Z", "invalid_field:expires_at_utc"),
    ("expires_at_utc", "2026-10-05T07:00:00.5Z", "invalid_field:expires_at_utc"),
])
def test_rejects_wrong_field_values(field, value, reason):
    with pytest.raises(StatementError) as err:
        make_statement(**{field: value})
    assert err.value.reason == reason


@pytest.mark.parametrize("field,value", [
    ("schema", "wd.manual-merge-a.statement.v2"),
    ("namespace", "waggledance-manual-merge-a-enroll"),
    ("principal", "operator@other"),
    ("purpose", "approval"),
    ("repository", "someone/else"),
    ("merge_method", "merge"),
    ("merge_method", "rebase"),
    ("operation_scope", "merge-and-delete-branch"),
    ("allowed_signers_path", "ops/security/other.allowed_signers"),
])
def test_rejects_changed_constant_fields_on_parse(field, value):
    obj = raw_json(make_statement())
    obj[field] = value
    with pytest.raises(StatementError) as err:
        mms.parse_statement(encode(obj))
    assert err.value.reason == f"invalid_field:{field}"


@pytest.mark.parametrize("path", [
    "/etc/passwd", "C:/x.py", "tools\\x.py", "../x.py", "tools/../x.py", "./x.py",
    "tools//x.py", "tools/x.py.", "tools/x.py ", "PROGRA~1/x.py", "tools/x\x00.py",
    "tools/x\x07.py", "tools/x:stream", "tools/x?.py", "",
])
def test_rejects_unsafe_paths(path):
    with pytest.raises(StatementError) as err:
        make_statement(exact_paths=(path,))
    assert err.value.reason == "invalid_path"


def test_rejects_unsorted_duplicate_and_empty_path_lists():
    for paths in (tuple(reversed(SORTED_PATHS)), (SORTED_PATHS[0], SORTED_PATHS[0]), ()):
        with pytest.raises(StatementError) as err:
            make_statement(exact_paths=paths)
        assert err.value.reason == "invalid_field:exact_paths"


def test_statement_that_changes_allowed_signers_is_refused():
    with pytest.raises(StatementError) as err:
        make_statement(exact_paths=tuple(sorted((ALLOWED_SIGNERS_PATH, *SORTED_PATHS))))
    assert err.value.reason == "allowed_signers_changed"


# --- T03 trust anchor -------------------------------------------------------------


def test_anchor_is_read_only_from_trusted_commit_blob(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "evil.git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "evil.index"))
    fake = FakeGit(anchor_bytes())
    anchor = mms.load_trust_anchor(repo_root=ROOT, trusted_commit=BASE, runner=fake)
    assert [call[3:] for call in fake.calls] == [
        ["rev-parse", "--verify", "--quiet", f"{BASE}^{{commit}}"],
        ["rev-parse", "--verify", "--quiet", f"{BASE}:{ALLOWED_SIGNERS_PATH}"],
        ["cat-file", "-t", anchor.blob_sha],
        ["cat-file", "blob", anchor.blob_sha],
    ]
    assert all(not key.upper().startswith("GIT_") for env in fake.envs for key in env)
    assert anchor.blob_sha == git_blob_sha(anchor_bytes())
    assert anchor.key_type == "ssh-ed25519" and anchor.key_label == "ED25519"
    assert anchor.fingerprint == mms.key_fingerprint(synthetic_key_blob())
    assert mms.FINGERPRINT_RE.fullmatch(anchor.fingerprint)


def test_sk_ed25519_anchor_parses_with_sk_label():
    anchor = load_anchor("sk-ssh-ed25519@openssh.com")
    assert anchor.key_label == "ED25519-SK"


@pytest.mark.parametrize("kwargs,reason", [
    ({"commit_ok": False}, "anchor_base_unknown"),
    ({"blob_sha": None}, "anchor_missing"),
    ({"kind": b"tree\n"}, "anchor_invalid"),
    ({"content": anchor_bytes() + b"# tampered\n"}, "anchor_integrity_mismatch"),
])
def test_anchor_loading_refusals(kwargs, reason):
    data = None if kwargs.get("blob_sha", "") is None else anchor_bytes()
    fake = FakeGit(data, **kwargs)
    with pytest.raises(StatementError) as err:
        mms.load_trust_anchor(repo_root=ROOT, trusted_commit=BASE, runner=fake)
    assert err.value.reason == reason


def test_anchor_requires_exact_trusted_commit_and_absolute_root():
    fake = FakeGit(anchor_bytes())
    for commit in ("B" * 40, "b" * 39, "HEAD", f"{BASE}~1"):
        with pytest.raises(StatementError) as err:
            mms.load_trust_anchor(repo_root=ROOT, trusted_commit=commit, runner=fake)
        assert err.value.reason == "anchor_base_unknown"
    with pytest.raises(StatementError) as err:
        mms.load_trust_anchor(repo_root=Path("relative"), trusted_commit=BASE, runner=fake)
    assert err.value.reason == "anchor_base_unknown"


def _blob_with(type_name: bytes, key: bytes, tail: bytes = b"") -> bytes:
    return ssh_string(type_name) + ssh_string(key) + tail


@pytest.mark.parametrize("text,reason", [
    (anchor_line() + "\n" + anchor_line() + "\n", "anchor_invalid"),
    ("# only a comment\n", "anchor_invalid"),
    (anchor_line(principal="someone@else") + "\n", "anchor_invalid"),
    (anchor_line(principal="operator@waggledance,other") + "\n", "anchor_invalid"),
    (anchor_line(options='namespaces="waggledance-manual-merge-a-enroll"') + "\n", "anchor_invalid"),
    (anchor_line(options='namespaces="waggledance-manual-merge-a",valid-before="20991231"') + "\n", "anchor_invalid"),
    (anchor_line(options="cert-authority") + "\n", "anchor_invalid"),
    (anchor_line(comment="").replace(" " + mms.ANCHOR_OPTIONS, "") + "\n", "anchor_invalid"),
    (anchor_line("sk-ecdsa-sha2-nistp256@openssh.com", blob=b"x") + "\n", "anchor_key_type_not_allowed"),
    (anchor_line("ecdsa-sha2-nistp256", blob=b"x") + "\n", "anchor_key_type_not_allowed"),
    (anchor_line("ssh-rsa", blob=b"x") + "\n", "anchor_key_type_not_allowed"),
    (anchor_line("ssh-ed25519-cert-v01@openssh.com", blob=b"x") + "\n", "anchor_key_type_not_allowed"),
    (anchor_line("ssh-ed25519", blob=_blob_with(b"ssh-rsa", bytes(32))) + "\n", "anchor_invalid"),
    (anchor_line("ssh-ed25519", blob=_blob_with(b"ssh-ed25519", bytes(31))) + "\n", "anchor_invalid"),
    (anchor_line("ssh-ed25519", blob=_blob_with(b"ssh-ed25519", bytes(32), b"\x00")) + "\n", "anchor_invalid"),
    (anchor_line("sk-ssh-ed25519@openssh.com",
                 blob=_blob_with(b"sk-ssh-ed25519@openssh.com", bytes(32), ssh_string(b"web:x"))) + "\n",
     "anchor_invalid"),
    (anchor_line(comment="caf\u00e9"), "anchor_invalid"),
])
def test_anchor_line_rules(text, reason):
    with pytest.raises(StatementError) as err:
        mms.parse_allowed_signers(text.encode("utf-8"))
    assert err.value.reason == reason


@pytest.mark.parametrize("data", [
    b"\xef\xbb\xbf" + anchor_bytes(),
    anchor_bytes().replace(b"\n", b"\r\n"),
    anchor_bytes().replace(b"AAAA", b"AA!A", 1),
    b"",
])
def test_anchor_byte_level_rules(data):
    with pytest.raises(StatementError) as err:
        mms.parse_allowed_signers(data)
    assert err.value.reason == "anchor_invalid"


# --- T04 binding and expiry -----------------------------------------------------


def _bind(statement, anchor, **overrides):
    kwargs = dict(anchor=anchor, expected_head_sha=HEAD, expected_base_sha=BASE,
                  live_changed_paths=list(SORTED_PATHS), now_utc=NOW)
    kwargs.update(overrides)
    mms.check_statement_binding(statement, **kwargs)


def test_binding_accepts_exact_facts():
    anchor = load_anchor()
    _bind(make_statement(anchor), anchor)


def test_binding_refusals():
    anchor = load_anchor()
    statement = make_statement(anchor)
    other_anchor = mms.TrustAnchor(**{**anchor.__dict__, "trusted_commit": "c" * 40})
    with pytest.raises(StatementError) as err:
        _bind(statement, other_anchor)
    assert err.value.reason == "anchor_not_from_statement_base"
    with pytest.raises(StatementError) as err:
        _bind(statement, anchor, expected_base_sha="c" * 40)
    assert err.value.reason == "base_mismatch"
    with pytest.raises(StatementError) as err:
        _bind(statement, anchor, expected_head_sha="e" * 40)
    assert err.value.reason == "signed_head_stale"
    blob_anchor = mms.TrustAnchor(**{**anchor.__dict__, "blob_sha": "f" * 40})
    with pytest.raises(StatementError) as err:
        _bind(statement, blob_anchor)
    assert err.value.reason == "anchor_blob_mismatch"
    fp_anchor = mms.TrustAnchor(**{**anchor.__dict__, "fingerprint": "SHA256:" + "A" * 43})
    with pytest.raises(StatementError) as err:
        _bind(statement, fp_anchor)
    assert err.value.reason == "key_fingerprint_mismatch"
    with pytest.raises(StatementError) as err:
        _bind(statement, anchor, live_changed_paths=[*SORTED_PATHS, ALLOWED_SIGNERS_PATH])
    assert err.value.reason == "allowed_signers_changed"
    with pytest.raises(StatementError) as err:
        _bind(statement, anchor, expected_head_sha="HEAD")
    assert err.value.reason == "invalid_live_fact"


@pytest.mark.parametrize("now,reason", [
    (datetime(2026, 10, 5, 7, 0, 0, tzinfo=timezone.utc), "statement_expired"),
    (datetime(2026, 10, 5, 8, 0, 0, tzinfo=timezone.utc), "statement_expired"),
    (datetime(2026, 10, 5, 6, 0, 0), "invalid_clock"),
    (datetime(2026, 10, 5, 9, 0, 0, tzinfo=timezone(timedelta(hours=3))), "invalid_clock"),
])
def test_expiry_refusals(now, reason):
    with pytest.raises(StatementError) as err:
        mms.check_statement_expiry(make_statement(), now_utc=now)
    assert err.value.reason == reason


def test_far_future_absolute_expiry_has_no_implicit_cap():
    statement = make_statement(expires_at_utc="2099-12-31T23:59:59Z")
    mms.check_statement_expiry(statement, now_utc=NOW)


# --- T02 signature verification (unit_mock unless named real) ---------------------


def test_unit_mock_good_signature_records_mock_evidence(tmp_path):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    fake = FakeSsh(good_line(anchor))
    keygen = fake_keygen(tmp_path)
    result = mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE,
                                            anchor=anchor, ssh_keygen=keygen, runner=fake)
    call = fake.calls[0]
    assert call["argv"][1:4] == ["-Y", "verify", "-f"]
    assert call["argv"][5:10] == ["-I", "operator@waggledance", "-n", "waggledance-manual-merge-a", "-s"]
    assert call["stdin"] == data and call["anchor_copy_bytes"] == anchor.data
    assert call["sig_copy_bytes"] == SIGNATURE
    assert not call["anchor_copy"].exists(), "temp copies must be removed"
    allowed_env = {"SystemRoot", "WINDIR", "PATH"} if sys.platform == "win32" else {"PATH"}
    assert set(call["env"]) <= allowed_env
    assert result.evidence_class == "unit_mock"
    assert result.verifier_argv[4] == "<anchor-temp-copy>" and result.verifier_argv[10] == "<signature-temp-copy>"
    assert result.statement_sha256 == hashlib.sha256(data).hexdigest()
    assert result.key_fingerprint == anchor.fingerprint


def test_unit_mock_sk_label_is_required_for_sk_anchor(tmp_path):
    anchor = load_anchor("sk-ssh-ed25519@openssh.com")
    data = mms.canonical_statement_bytes(make_statement(anchor))
    wrong = good_line(anchor).replace(b"ED25519-SK", b"ED25519")
    with pytest.raises(StatementError) as err:
        mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                       ssh_keygen=fake_keygen(tmp_path), runner=FakeSsh(wrong))
    assert err.value.reason == "signature_output_unexpected"


@pytest.mark.parametrize("transform", [
    lambda line: line.replace(b"waggledance-manual-merge-a", b"waggledance-manual-merge-a-enroll"),
    lambda line: line.replace(b"operator@waggledance", b"operator@else"),
    lambda line: line.replace(b"SHA256:", b"SHA256:X", 1),
    lambda line: line + b"Good extra\n",
    lambda line: b"",
    lambda line: b"\n",
    lambda line: line.replace(b"Good", b"G\xc3\xb6od"),
])
def test_unit_mock_unexpected_verifier_output_refused(tmp_path, transform):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    with pytest.raises(StatementError) as err:
        mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                       ssh_keygen=fake_keygen(tmp_path), runner=FakeSsh(transform(good_line(anchor))))
    assert err.value.reason == "signature_output_unexpected"


def test_unit_mock_windows_crlf_good_line_is_accepted(tmp_path):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    line = good_line(anchor).replace(b"\n", b"\r\n")
    result = mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                            ssh_keygen=fake_keygen(tmp_path), runner=FakeSsh(line))
    assert result.evidence_class == "unit_mock"


@pytest.mark.parametrize("fake,reason", [
    (FakeSsh(b"", returncode=255), "signature_invalid"),
    (FakeSsh(b"Good", returncode=1), "signature_invalid"),
    (FakeSsh(b"", exc=subprocess.TimeoutExpired("ssh-keygen", 30)), "verifier_timeout"),
    (FakeSsh(b"", exc=FileNotFoundError("missing")), "verifier_unavailable"),
])
def test_unit_mock_verifier_failures_refuse(tmp_path, fake, reason):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    with pytest.raises(StatementError) as err:
        mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                       ssh_keygen=fake_keygen(tmp_path), runner=fake)
    assert err.value.reason == reason


@pytest.mark.parametrize("signature", [
    b"", b"not armored", SIGNATURE.replace(b"SSH SIGNATURE", b"PGP SIGNATURE"),
    SIGNATURE + "\u00e9".encode("utf-8"), b"-----BEGIN SSH SIGNATURE-----\n" + b"A" * 20000 + b"\n-----END SSH SIGNATURE-----\n",
])
def test_signature_armor_is_required(tmp_path, signature):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    fake = FakeSsh(good_line(anchor))
    with pytest.raises(StatementError) as err:
        mms.verify_statement_signature(statement_bytes=data, signature_bytes=signature, anchor=anchor,
                                       ssh_keygen=fake_keygen(tmp_path), runner=fake)
    assert err.value.reason == "signature_malformed"
    assert fake.calls == []


def test_verifier_path_rules(tmp_path):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    with pytest.raises(StatementError) as err:
        mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                       ssh_keygen=Path("ssh-keygen"), runner=FakeSsh(good_line(anchor)))
    assert err.value.reason == "verifier_unavailable"
    with pytest.raises(StatementError) as err:
        mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                       ssh_keygen=tmp_path / "absent-ssh-keygen.exe")
    assert err.value.reason == "verifier_unavailable"


def test_non_canonical_statement_is_never_sent_to_verifier(tmp_path):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor)).replace(b"\n", b"\r\n")
    fake = FakeSsh(good_line(anchor))
    with pytest.raises(StatementError):
        mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                       ssh_keygen=fake_keygen(tmp_path), runner=fake)
    assert fake.calls == []


def _real_ssh_keygen() -> Path | None:
    candidates = [Path(r"C:\Windows\System32\OpenSSH\ssh-keygen.exe")] if sys.platform == "win32" else []
    found = shutil.which("ssh-keygen")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_absolute() and candidate.is_file():
            return candidate
    return None


def test_real_ssh_keygen_rejects_garbage_signature():
    """Real binary, negative only: a garbage SSHSIG over a synthetic public key fails."""
    keygen = _real_ssh_keygen()
    if keygen is None:
        pytest.skip("NOT_RUN: ssh-keygen binary not found on this host")
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    with pytest.raises(StatementError) as err:
        mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                       ssh_keygen=keygen)
    assert err.value.reason == "signature_invalid"


def test_real_positive_ssh_verification_not_run():
    pytest.skip("NOT_RUN/UNKNOWN: positive real-SSH verification needs operator-supplied public "
                "evidence in the exact namespace; agents create, read and store no key material")


def test_verify_statement_full_flow_unit_mock_consumes_no_nonce(tmp_path):
    anchor_data = anchor_bytes()
    git = FakeGit(anchor_data)
    anchor = mms.load_trust_anchor(repo_root=ROOT, trusted_commit=BASE, runner=git)
    data = mms.canonical_statement_bytes(make_statement(anchor))
    ledger_root = tmp_path / "ledger"
    ledger_root.mkdir()
    verified = mms.verify_statement(
        statement_bytes=data, signature_bytes=SIGNATURE, repo_root=ROOT, trusted_commit=BASE,
        expected_head_sha=HEAD, live_changed_paths=list(SORTED_PATHS), now_utc=NOW,
        ssh_keygen=fake_keygen(tmp_path), runner=FakeSsh(good_line(anchor)), git_runner=FakeGit(anchor_data),
    )
    assert verified.statement.nonce == NONCE
    assert verified.verification.evidence_class == "unit_mock"
    assert list(ledger_root.iterdir()) == []


def test_verify_statement_refuses_stale_head_before_running_verifier(tmp_path):
    anchor_data = anchor_bytes()
    anchor = mms.load_trust_anchor(repo_root=ROOT, trusted_commit=BASE, runner=FakeGit(anchor_data))
    data = mms.canonical_statement_bytes(make_statement(anchor))
    fake = FakeSsh(good_line(anchor))
    with pytest.raises(StatementError) as err:
        mms.verify_statement(
            statement_bytes=data, signature_bytes=SIGNATURE, repo_root=ROOT, trusted_commit=BASE,
            expected_head_sha="e" * 40, live_changed_paths=list(SORTED_PATHS), now_utc=NOW,
            ssh_keygen=fake_keygen(tmp_path), runner=fake, git_runner=FakeGit(anchor_data),
        )
    assert err.value.reason == "signed_head_stale"
    assert fake.calls == []


# --- T05 nonce ledger -----------------------------------------------------------

SHA = "5" * 64


def _ledger(tmp_path: Path, **kwargs) -> NonceLedger:
    root = tmp_path / "nonces"
    root.mkdir(exist_ok=True)
    return NonceLedger(root, clock=lambda: NOW, **kwargs)


def _reserve(ledger: NonceLedger, nonce: str = NONCE, sha: str = SHA):
    return ledger.reserve(nonce=nonce, statement_sha256=sha, pull_request=1763, head_sha=HEAD,
                          base_sha=BASE, batch_id="mma-20261005-statement", evidence={"phase": "admission"})


def test_new_nonce_format():
    nonces = {mms.new_nonce() for _ in range(32)}
    assert len(nonces) == 32 and all(mms.NONCE_RE.fullmatch(n) for n in nonces)


def test_ledger_happy_path_and_canonical_history(tmp_path):
    ledger = _ledger(tmp_path)
    assert _reserve(ledger).to_state == "reserved"
    assert ledger.in_flight() == (NONCE,)
    ledger.transition(nonce=NONCE, to_state="merge_started", statement_sha256=SHA)
    ledger.transition(nonce=NONCE, to_state="executed", statement_sha256=SHA, evidence={"merge_commit": "c" * 40})
    assert ledger.state(NONCE) == "executed" and ledger.in_flight() == ()
    history = ledger.history(NONCE)
    assert [r.to_state for r in history] == ["reserved", "merge_started", "executed"]
    assert [r.from_state for r in history] == [None, "reserved", "merge_started"]
    raw = (ledger._root / f"{NONCE}.jsonl").read_bytes()
    for line in raw[:-1].split(b"\n"):
        assert line + b"\n" == mms._canonical_record(json.loads(line))


@pytest.mark.parametrize("path", [
    ["refused_before_effect"],
    ["merge_started", "executed"],
    ["merge_started", "indeterminate", "reconciled_merged"],
    ["merge_started", "indeterminate", "reconciled_not_merged"],
    [],
    ["merge_started"],
    ["merge_started", "indeterminate"],
])
def test_nonce_reuse_refused_in_every_state(tmp_path, path):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    for state in path:
        ledger.transition(nonce=NONCE, to_state=state, statement_sha256=SHA)
    with pytest.raises(LedgerError) as err:
        _reserve(ledger)
    assert err.value.reason == "nonce_reused"


@pytest.mark.parametrize("path", [[], ["merge_started"], ["merge_started", "indeterminate"]])
def test_second_nonce_refused_while_one_is_in_flight(tmp_path, path):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    for state in path:
        ledger.transition(nonce=NONCE, to_state=state, statement_sha256=SHA)
    with pytest.raises(LedgerError) as err:
        _reserve(ledger, nonce="f" * 32)
    assert err.value.reason == "ledger_in_flight"


def test_new_nonce_allowed_after_terminal_state(tmp_path):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    ledger.transition(nonce=NONCE, to_state="refused_before_effect", statement_sha256=SHA)
    assert _reserve(ledger, nonce="f" * 32).to_state == "reserved"


@pytest.mark.parametrize("path,target", [
    ([], "executed"),
    ([], "indeterminate"),
    ([], "reserved"),
    (["refused_before_effect"], "merge_started"),
    (["merge_started", "executed"], "indeterminate"),
    (["merge_started", "indeterminate"], "merge_started"),
    (["merge_started", "indeterminate"], "executed"),
    ([], "approved"),
])
def test_illegal_transitions_refused(tmp_path, path, target):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    for state in path:
        ledger.transition(nonce=NONCE, to_state=state, statement_sha256=SHA)
    with pytest.raises(LedgerError) as err:
        ledger.transition(nonce=NONCE, to_state=target, statement_sha256=SHA)
    assert err.value.reason == "ledger_transition_invalid"


def test_transition_requires_same_statement_and_known_nonce(tmp_path):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    with pytest.raises(LedgerError) as err:
        ledger.transition(nonce=NONCE, to_state="merge_started", statement_sha256="6" * 64)
    assert err.value.reason == "ledger_statement_mismatch"
    with pytest.raises(LedgerError) as err:
        ledger.transition(nonce="f" * 32, to_state="merge_started", statement_sha256=SHA)
    assert err.value.reason == "nonce_unknown"


def _rewrite_first_record(path: Path, **changes):
    record = json.loads(path.read_bytes().split(b"\n")[0])
    record.update(changes)
    path.write_bytes(mms._canonical_record(record))


@pytest.mark.parametrize("corrupt", [
    lambda p: p.write_bytes(p.read_bytes()[:-1]),
    lambda p: p.write_bytes(b"{not json}\n"),
    lambda p: p.write_bytes(p.read_bytes().replace(b'","', b'", "', 1)),
    lambda p: _rewrite_first_record(p, seq=1),
    lambda p: _rewrite_first_record(p, seq=True),
    lambda p: _rewrite_first_record(p, to="merge_started"),
    lambda p: _rewrite_first_record(p, nonce="f" * 32),
    lambda p: _rewrite_first_record(p, extra=1),
    lambda p: _rewrite_first_record(p, statement_sha256="X"),
    lambda p: p.write_bytes(b""),
])
def test_malformed_ledger_refuses(tmp_path, corrupt):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    corrupt(ledger._root / f"{NONCE}.jsonl")
    with pytest.raises(LedgerError) as err:
        ledger.state(NONCE)
    assert err.value.reason == "ledger_unreadable"
    with pytest.raises(LedgerError):
        _reserve(ledger, nonce="f" * 32)


def test_unexpected_entry_in_ledger_root_refuses(tmp_path):
    ledger = _ledger(tmp_path)
    (ledger._root / "notes.txt").write_text("x", encoding="ascii")
    with pytest.raises(LedgerError) as err:
        ledger.in_flight()
    assert err.value.reason == "ledger_unexpected_entry"


def test_ledger_root_must_be_absolute_existing_directory(tmp_path):
    for root in (Path("relative-ledger"), tmp_path / "missing"):
        with pytest.raises(LedgerError) as err:
            NonceLedger(root)
        assert err.value.reason == "ledger_root_invalid"


@pytest.mark.parametrize("evidence", [{"Bad-Key": 1}, {"ok": 1.5}, {"ok": "x" * 600}, {"ok": "caf\u00e9"}, {"ok": [1]}])
def test_ledger_evidence_is_validated(tmp_path, evidence):
    ledger = _ledger(tmp_path)
    with pytest.raises(LedgerError) as err:
        ledger.reserve(nonce=NONCE, statement_sha256=SHA, pull_request=1763, head_sha=HEAD,
                       base_sha=BASE, batch_id="mma-20261005-statement", evidence=evidence)
    assert err.value.reason == "ledger_evidence_invalid"
    assert list(ledger._root.iterdir()) == []


def test_ledger_rejects_bad_reservation_fields(tmp_path):
    ledger = _ledger(tmp_path)
    for kwargs, reason in (
        ({"nonce": "XYZ"}, "invalid_field:nonce"),
        ({"statement_sha256": "1" * 63}, "invalid_field:statement_sha256"),
        ({"pull_request": True}, "invalid_field:pull_request"),
        ({"head_sha": "A" * 40}, "invalid_field:head_sha"),
        ({"batch_id": "x"}, "invalid_field:batch_id"),
    ):
        args = dict(nonce=NONCE, statement_sha256=SHA, pull_request=1763, head_sha=HEAD,
                    base_sha=BASE, batch_id="mma-20261005-statement")
        args.update(kwargs)
        with pytest.raises(LedgerError) as err:
            ledger.reserve(**args)
        assert err.value.reason == reason


def test_ledger_lock_timeout_when_lock_is_held(tmp_path):
    ledger = _ledger(tmp_path, lock_timeout_seconds=0.3)
    held = ledger._acquire()
    try:
        with pytest.raises(LedgerError) as err:
            _ledger(tmp_path, lock_timeout_seconds=0.3).in_flight()
        assert err.value.reason == "ledger_lock_timeout"
    finally:
        ledger._release(held)
    assert ledger.in_flight() == ()


_RACE_SCRIPT = r"""
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tools.manual_bridge_merge_statement import NonceLedger, StatementError
ledger = NonceLedger(Path(sys.argv[2]), lock_timeout_seconds=20)
try:
    ledger.reserve(nonce=sys.argv[3], statement_sha256="5" * 64, pull_request=1763,
                   head_sha="a" * 40, base_sha="b" * 40, batch_id="mma-20261005-statement")
    print("WON")
except StatementError as exc:
    print("LOST " + exc.reason)
"""


def test_parallel_reserve_has_exactly_one_winner(tmp_path):
    root = tmp_path / "race"
    root.mkdir()
    procs = [
        subprocess.Popen([sys.executable, "-c", _RACE_SCRIPT, str(ROOT), str(root), NONCE],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(4)
    ]
    outputs = [p.communicate(timeout=60)[0].decode().strip() for p in procs]
    assert sorted(outputs).count("WON") == 1, outputs
    assert all(o == "WON" or o in {"LOST nonce_reused", "LOST ledger_in_flight"} for o in outputs), outputs


# --- T07 ledger hardening (Tools SR1-SR4, reproduced before the fix) -------------


def _junction(link: Path, target: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows junction case; POSIX symlink cases cover links there")
    done = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, timeout=30, check=False)
    if done.returncode != 0:
        pytest.skip(f"UNKNOWN: junction could not be created on this host (exit {done.returncode})")


def _symlink(link: Path, target: Path, *, directory: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"UNKNOWN: symlink creation not permitted on this host ({type(exc).__name__})")


def _listing(path: Path) -> list[tuple[str, int]]:
    return sorted((p.name, p.stat().st_size) for p in path.iterdir())


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -1, -0.5, 600.5, 10**400, True, "30", None])
def test_ledger_timeout_must_be_finite_nonnegative_and_bounded(tmp_path, value):
    with pytest.raises(LedgerError) as err:
        _ledger(tmp_path, lock_timeout_seconds=value)
    assert err.value.reason == "ledger_timeout_invalid"
    assert list((tmp_path / "nonces").iterdir()) == []


@pytest.mark.parametrize("value", [0, 0.0, 0.3, 30, 600])
def test_ledger_timeout_valid_twins_are_accepted(tmp_path, value):
    assert _reserve(_ledger(tmp_path, lock_timeout_seconds=value)).to_state == "reserved"


def test_ledger_zero_timeout_still_refuses_when_busy(tmp_path):
    holder = _ledger(tmp_path)
    held = holder._acquire()
    try:
        with pytest.raises(LedgerError) as err:
            _ledger(tmp_path, lock_timeout_seconds=0).in_flight()
        assert err.value.reason == "ledger_lock_timeout"
    finally:
        holder._release(held)


def test_ledger_junction_root_is_refused_and_target_untouched(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    _junction(tmp_path / "jroot", target)
    with pytest.raises(LedgerError) as err:
        NonceLedger(tmp_path / "jroot", clock=lambda: NOW)
    assert err.value.reason == "ledger_root_alias"
    assert list(target.iterdir()) == []


def test_ledger_junction_ancestor_is_refused_and_target_untouched(tmp_path):
    target = tmp_path / "real"
    (target / "ledger").mkdir(parents=True)
    _junction(tmp_path / "janc", target)
    with pytest.raises(LedgerError) as err:
        NonceLedger(tmp_path / "janc" / "ledger", clock=lambda: NOW)
    assert err.value.reason == "ledger_root_alias"
    assert list((target / "ledger").iterdir()) == []


def test_ledger_symlink_root_is_refused(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    _symlink(tmp_path / "sroot", target, directory=True)
    with pytest.raises(LedgerError) as err:
        NonceLedger(tmp_path / "sroot", clock=lambda: NOW)
    assert err.value.reason == "ledger_root_alias"


def test_ledger_root_replaced_after_construction_is_refused(tmp_path):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    (tmp_path / "nonces").rename(tmp_path / "moved")
    (tmp_path / "nonces").mkdir()
    for call in (lambda: _reserve(ledger, nonce="f" * 32), ledger.in_flight, lambda: ledger.state(NONCE),
                 lambda: ledger.transition(nonce=NONCE, to_state="merge_started", statement_sha256=SHA)):
        with pytest.raises(LedgerError) as err:
            call()
        assert err.value.reason == "ledger_root_changed"
    assert list((tmp_path / "nonces").iterdir()) == []


def test_ledger_hardlinked_nonce_transition_is_refused_without_outside_write(tmp_path):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.replace(ledger._root / f"{NONCE}.jsonl", outside / "victim.jsonl")
    os.link(outside / "victim.jsonl", ledger._root / f"{NONCE}.jsonl")
    before = (outside / "victim.jsonl").read_bytes()
    for call in (lambda: ledger.transition(nonce=NONCE, to_state="merge_started", statement_sha256=SHA),
                 lambda: ledger.history(NONCE), ledger.in_flight):
        with pytest.raises(LedgerError) as err:
            call()
        assert err.value.reason == "ledger_entry_alias"
    assert (outside / "victim.jsonl").read_bytes() == before


def test_ledger_hardlinked_terminal_nonce_blocks_new_reserve(tmp_path):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    ledger.transition(nonce=NONCE, to_state="refused_before_effect", statement_sha256=SHA)
    os.link(ledger._root / f"{NONCE}.jsonl", tmp_path / "alias.jsonl")
    before = _listing(ledger._root)
    with pytest.raises(LedgerError) as err:
        _reserve(ledger, nonce="f" * 32)
    assert err.value.reason == "ledger_entry_alias"
    assert _listing(ledger._root) == before


def test_ledger_hardlinked_lock_is_refused(tmp_path):
    root = tmp_path / "nonces"
    root.mkdir()
    (tmp_path / "outside.lock").write_bytes(b"")
    os.link(tmp_path / "outside.lock", root / mms.LEDGER_LOCK_NAME)
    ledger = NonceLedger(root, clock=lambda: NOW)
    for call in (lambda: _reserve(ledger), ledger.in_flight, lambda: ledger.state(NONCE)):
        with pytest.raises(LedgerError) as err:
            call()
        assert err.value.reason == "ledger_entry_alias"
    assert sorted(p.name for p in root.iterdir()) == [mms.LEDGER_LOCK_NAME]


@pytest.mark.parametrize("leaf", ["nonce", "lock"])
def test_ledger_symlinked_leaf_is_refused(tmp_path, leaf):
    ledger = _ledger(tmp_path)
    if leaf == "nonce":
        _reserve(ledger)
        ledger.transition(nonce=NONCE, to_state="refused_before_effect", statement_sha256=SHA)
        real = tmp_path / "real.jsonl"
        os.replace(ledger._root / f"{NONCE}.jsonl", real)
        name = f"{NONCE}.jsonl"
    else:
        real = tmp_path / "real.lock"
        real.write_bytes(b"")
        name = mms.LEDGER_LOCK_NAME
    before = real.read_bytes()
    _symlink(ledger._root / name, real)
    with pytest.raises(LedgerError) as err:
        _reserve(ledger, nonce="f" * 32)
    assert err.value.reason in {"ledger_entry_alias", "ledger_entry_unusable", "ledger_unexpected_entry"}
    assert real.read_bytes() == before
    assert not (ledger._root / ("f" * 32 + ".jsonl")).exists()


class _BarrierLedger(NonceLedger):
    """Runs one foreign operation right after the real lock release (fixture hook only)."""

    hook = None

    def _release(self, fd):
        super()._release(fd)
        hook, _BarrierLedger.hook = _BarrierLedger.hook, None
        if hook is not None:
            hook()


def test_reserve_returns_its_own_record_despite_a_later_writer(tmp_path):
    other = _ledger(tmp_path)
    _BarrierLedger.hook = lambda: other.transition(nonce=NONCE, to_state="merge_started", statement_sha256=SHA)
    record = _reserve(_BarrierLedger(tmp_path / "nonces", clock=lambda: NOW))
    assert (record.seq, record.from_state, record.to_state) == (0, None, "reserved")
    assert [r.to_state for r in other.history(NONCE)] == ["reserved", "merge_started"]


def test_transition_returns_its_own_record_despite_a_later_writer(tmp_path):
    other = _ledger(tmp_path)
    _reserve(other)
    _BarrierLedger.hook = lambda: other.transition(nonce=NONCE, to_state="executed", statement_sha256=SHA)
    record = _BarrierLedger(tmp_path / "nonces", clock=lambda: NOW).transition(
        nonce=NONCE, to_state="merge_started", statement_sha256=SHA)
    assert (record.seq, record.from_state, record.to_state) == (1, "reserved", "merge_started")
    assert [r.to_state for r in other.history(NONCE)] == ["reserved", "merge_started", "executed"]


def test_history_and_state_read_under_the_lock(tmp_path):
    holder = _ledger(tmp_path)
    _reserve(holder)
    held = holder._acquire()
    try:
        reader = _ledger(tmp_path, lock_timeout_seconds=0.2)
        for call in (lambda: reader.history(NONCE), lambda: reader.state(NONCE)):
            with pytest.raises(LedgerError) as err:
                call()
            assert err.value.reason == "ledger_lock_timeout"
    finally:
        holder._release(held)
    assert holder.state(NONCE) == "reserved"


def test_reading_an_empty_ledger_creates_no_file(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.state(NONCE) is None and ledger.history(NONCE) == ()
    assert list(ledger._root.iterdir()) == []


def _write_file(tmp_path: Path) -> tuple[int, Path]:
    path = tmp_path / "out.bin"
    return os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600), path


def test_write_all_continues_after_short_writes(tmp_path):
    fd, path = _write_file(tmp_path)
    try:
        mms._write_all(fd, b"0123456789", write=lambda f, view: os.write(f, bytes(view[:3])))
    finally:
        os.close(fd)
    assert path.read_bytes() == b"0123456789"


@pytest.mark.parametrize("fake", [
    lambda f, view: 0,
    lambda f, view: len(view) + 1,
    lambda f, view: True,
    lambda f, view: None,
    lambda f, view: (_ for _ in ()).throw(OSError(28, "No space left on device")),
])
def test_write_all_refuses_zero_bogus_or_failed_writes(tmp_path, fake):
    fd, _ = _write_file(tmp_path)
    try:
        with pytest.raises(LedgerError) as err:
            mms._write_all(fd, b"0123456789", write=fake)
    finally:
        os.close(fd)
    assert err.value.reason == "ledger_write_failed"


def _short_then_stuck(monkeypatch):
    real = mms._write_all

    def patched(fd, data, write=os.write):
        calls = []

        def fake(f, view):
            calls.append(len(view))
            return os.write(f, bytes(view[: len(view) // 2])) if len(calls) == 1 else 0
        real(fd, data, write=fake)

    monkeypatch.setattr(mms, "_write_all", patched)


def test_reserve_short_write_refuses_and_partial_ledger_stays_refused(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    _short_then_stuck(monkeypatch)
    with pytest.raises(LedgerError) as err:
        _reserve(ledger)
    assert err.value.reason == "ledger_write_failed"
    monkeypatch.undo()
    data = (ledger._root / f"{NONCE}.jsonl").read_bytes()
    assert data and not data.endswith(b"\n")
    for call in (lambda: ledger.state(NONCE), lambda: _reserve(ledger), lambda: _reserve(ledger, nonce="f" * 32)):
        with pytest.raises(LedgerError) as err:
            call()
        assert err.value.reason in {"ledger_unreadable", "nonce_reused"}
    assert (ledger._root / f"{NONCE}.jsonl").read_bytes() == data


def test_transition_short_write_refuses_and_is_not_retried(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    _short_then_stuck(monkeypatch)
    with pytest.raises(LedgerError) as err:
        ledger.transition(nonce=NONCE, to_state="merge_started", statement_sha256=SHA)
    assert err.value.reason == "ledger_write_failed"
    monkeypatch.undo()
    with pytest.raises(LedgerError) as err:
        ledger.state(NONCE)
    assert err.value.reason == "ledger_unreadable"


def test_reserve_with_chunked_writes_commits_the_canonical_record(tmp_path, monkeypatch):
    real = mms._write_all
    monkeypatch.setattr(mms, "_write_all", lambda fd, data, write=os.write: real(
        fd, data, write=lambda f, view: os.write(f, bytes(view[:7]))))
    record = _reserve(_ledger(tmp_path))
    monkeypatch.undo()
    raw = (tmp_path / "nonces" / f"{NONCE}.jsonl").read_bytes()
    assert record.to_state == "reserved" and raw == mms._canonical_record(json.loads(raw))


# --- T08 provenance and cleanup (Tools SR5, SR7) ---------------------------------


def _verified(anchor: mms.TrustAnchor, tmp_path: Path, **verification_changes) -> mms.VerifiedStatement:
    data = mms.canonical_statement_bytes(make_statement(anchor))
    verification = mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                                  ssh_keygen=fake_keygen(tmp_path), runner=FakeSsh(good_line(anchor)))
    verification = dataclasses.replace(verification, **verification_changes)
    return mms.VerifiedStatement(statement=mms.parse_statement(data), statement_sha256=mms.statement_sha256(data),
                                 anchor=anchor, verification=verification)


def test_anchor_and_verifier_provenance_are_recorded_separately(tmp_path):
    anchor = load_anchor()
    assert anchor.provenance == mms.EVIDENCE_UNIT_MOCK
    verified = _verified(anchor, tmp_path)
    assert verified.verification.evidence_class == mms.EVIDENCE_UNIT_MOCK
    assert verified.verification.anchor_provenance == mms.EVIDENCE_UNIT_MOCK


def test_hand_built_anchor_defaults_to_unverified(tmp_path):
    loaded = load_anchor()
    hand_built = mms.TrustAnchor(**{f.name: getattr(loaded, f.name) for f in dataclasses.fields(loaded)
                                    if f.name != "provenance"})
    assert hand_built.provenance == mms.ANCHOR_UNVERIFIED
    assert _verified(hand_built, tmp_path).verification.anchor_provenance == mms.ANCHOR_UNVERIFIED


@pytest.mark.parametrize("anchor_label,ssh_label,anchor_copy_label", [
    (mms.EVIDENCE_UNIT_MOCK, mms.EVIDENCE_UNIT_MOCK, mms.EVIDENCE_UNIT_MOCK),
    (mms.EVIDENCE_UNIT_MOCK, mms.EVIDENCE_SUBPROCESS_SSH, mms.EVIDENCE_UNIT_MOCK),  # mocked git, "real" ssh
    (mms.ANCHOR_SUBPROCESS_GIT, mms.EVIDENCE_UNIT_MOCK, mms.ANCHOR_SUBPROCESS_GIT),
    (mms.ANCHOR_UNVERIFIED, mms.EVIDENCE_SUBPROCESS_SSH, mms.ANCHOR_UNVERIFIED),
    (mms.ANCHOR_SUBPROCESS_GIT, mms.EVIDENCE_SUBPROCESS_SSH, mms.EVIDENCE_UNIT_MOCK),
])
def test_any_mocked_or_unverified_provenance_refuses(tmp_path, anchor_label, ssh_label, anchor_copy_label):
    anchor = dataclasses.replace(load_anchor(), provenance=anchor_label)
    verified = _verified(anchor, tmp_path, evidence_class=ssh_label, anchor_provenance=anchor_copy_label)
    with pytest.raises(StatementError) as err:
        mms.require_genuine_provenance(verified)
    assert err.value.reason == "provenance_not_genuine"


def test_genuine_labels_still_need_binding_and_a_verified_statement(tmp_path):
    anchor = dataclasses.replace(load_anchor(), provenance=mms.ANCHOR_SUBPROCESS_GIT)
    # Labels only (synthetic fixture): the check is necessary, never sufficient.
    good = _verified(anchor, tmp_path, evidence_class=mms.EVIDENCE_SUBPROCESS_SSH,
                     anchor_provenance=mms.ANCHOR_SUBPROCESS_GIT)
    mms.require_genuine_provenance(good)
    for bad in (dataclasses.replace(good, statement_sha256="0" * 64),
                dataclasses.replace(good, anchor=dataclasses.replace(anchor, blob_sha="0" * 40)),
                dataclasses.replace(good, verification=dataclasses.replace(good.verification, key_fingerprint="x")),
                good.verification, None):
        with pytest.raises(StatementError) as err:
            mms.require_genuine_provenance(bad)
        assert err.value.reason == "provenance_not_genuine"


class _FailingRmtree:
    def __init__(self):
        self.real = shutil.rmtree
        self.paths: list[Path] = []

    def __call__(self, path, *args, **kwargs):
        self.paths.append(Path(path))
        raise PermissionError(13, "injected cleanup failure", str(path))

    def cleanup(self):
        for path in self.paths:
            self.real(path)


def test_cleanup_failure_refuses_an_otherwise_good_verification(tmp_path, monkeypatch):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    failing = _FailingRmtree()
    monkeypatch.setattr(mms.shutil, "rmtree", failing)
    try:
        with pytest.raises(StatementError) as err:
            mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                           ssh_keygen=fake_keygen(tmp_path), runner=FakeSsh(good_line(anchor)))
    finally:
        monkeypatch.undo()
        failing.cleanup()
    assert err.value.reason == "verifier_cleanup_failed"
    assert "PermissionError" in err.value.detail and len(failing.paths) == 1


@pytest.mark.parametrize("fake,reason", [
    (FakeSsh(b"", returncode=1), "signature_invalid"),
    (FakeSsh(b"Bad line\n"), "signature_output_unexpected"),
    (FakeSsh(b"", exc=subprocess.TimeoutExpired("ssh-keygen", 30)), "verifier_timeout"),
    (FakeSsh(b"", exc=FileNotFoundError("missing")), "verifier_unavailable"),
])
def test_cleanup_failure_is_recorded_without_masking_the_verify_error(tmp_path, monkeypatch, fake, reason):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    failing = _FailingRmtree()
    monkeypatch.setattr(mms.shutil, "rmtree", failing)
    try:
        with pytest.raises(StatementError) as err:
            mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                           ssh_keygen=fake_keygen(tmp_path), runner=fake)
    finally:
        monkeypatch.undo()
        failing.cleanup()
    assert err.value.reason == reason
    assert err.value.cleanup_failure is not None and "PermissionError" in err.value.cleanup_failure
    assert any("not removed" in note for note in getattr(err.value, "__notes__", []))


def test_successful_cleanup_leaves_no_failure_record(tmp_path):
    anchor = load_anchor()
    data = mms.canonical_statement_bytes(make_statement(anchor))
    fake = FakeSsh(b"", returncode=1)
    with pytest.raises(StatementError) as err:
        mms.verify_statement_signature(statement_bytes=data, signature_bytes=SIGNATURE, anchor=anchor,
                                       ssh_keygen=fake_keygen(tmp_path), runner=fake)
    assert err.value.cleanup_failure is None and not hasattr(err.value, "__notes__")
    assert not fake.calls[0]["anchor_copy"].parent.exists()


# --- T09 post-open aliases and cleanup errors (Tools 383E7B04 SHA1, SHA2) ---------


class _OsProxy:
    """Module-local ``os`` stand-in: overrides a few calls and delegates the rest."""

    def __init__(self, **overrides):
        self.__dict__.update(overrides)

    def __getattr__(self, name):
        return getattr(os, name)


def _close_then_fail(message):
    def close(fd):
        os.close(fd)
        raise OSError(5, message)
    return close


def _fsync_fails(fd):
    raise OSError(28, "ORIGINAL injected fsync")


def _unlock_then_fail(monkeypatch):
    """Really unlock, then report a secondary unlock failure (both platforms)."""
    if sys.platform == "win32":
        import msvcrt

        real = msvcrt.locking

        def locking(fd, mode, nbytes):
            real(fd, mode, nbytes)
            if mode == msvcrt.LK_UNLCK:
                raise OSError(13, "SECONDARY injected unlock")

        monkeypatch.setattr(msvcrt, "locking", locking)
    else:
        import fcntl

        real = fcntl.flock

        def flock(fd, operation):
            real(fd, operation)
            if operation == fcntl.LOCK_UN:
                raise OSError(13, "SECONDARY injected unlock")

        monkeypatch.setattr(fcntl, "flock", flock)


def _write(ledger: NonceLedger, operation: str):
    if operation == "reserve":
        return _reserve(ledger)
    return ledger.transition(nonce=NONCE, to_state="merge_started", statement_sha256=SHA)


def _ready(tmp_path: Path, operation: str) -> NonceLedger:
    ledger = _ledger(tmp_path)
    if operation == "transition":
        _reserve(ledger)
    return ledger


@pytest.mark.parametrize("operation", ["reserve", "transition"])
def test_hardlink_added_after_open_refuses_before_any_write(tmp_path, monkeypatch, operation):
    ledger = _ready(tmp_path, operation)
    nonce_file, alias = ledger._root / f"{NONCE}.jsonl", tmp_path / "post-open-alias"
    real = NonceLedger._commit
    seen = {}

    def link_then_commit(self, nonce, fd, prior, record):
        os.link(nonce_file, alias)
        seen["nlink"], seen["bytes"] = os.fstat(fd).st_nlink, alias.stat().st_size
        return real(self, nonce, fd, prior, record)

    monkeypatch.setattr(NonceLedger, "_commit", link_then_commit)
    with pytest.raises(LedgerError) as err:
        _write(ledger, operation)
    monkeypatch.undo()
    assert err.value.reason == "ledger_entry_alias"
    assert seen["nlink"] == 2 and alias.stat().st_size == seen["bytes"]
    with pytest.raises(LedgerError) as err:
        ledger.history(NONCE)
    assert err.value.reason == "ledger_entry_alias"


@pytest.mark.parametrize("operation", ["reserve", "transition"])
def test_hardlink_added_during_the_write_refuses_instead_of_reporting_success(tmp_path, monkeypatch, operation):
    ledger = _ready(tmp_path, operation)
    alias = tmp_path / "late-alias"
    real = mms._fsync

    def fsync_then_link(fd):
        real(fd)
        os.link(ledger._root / f"{NONCE}.jsonl", alias)

    monkeypatch.setattr(mms, "_fsync", fsync_then_link)
    with pytest.raises(LedgerError) as err:
        _write(ledger, operation)
    monkeypatch.undo()
    assert err.value.reason == "ledger_entry_alias"
    # The written line stays for reconciliation; nothing retries or reports success.
    assert alias.read_bytes().count(b"\n") == (1 if operation == "reserve" else 2)


def test_unaliased_writes_check_the_leaf_at_open_before_write_and_before_success(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    checked = []
    real = mms._require_same_plain_leaf
    monkeypatch.setattr(mms, "_require_same_plain_leaf", lambda fd, path: (checked.append(path.name), real(fd, path))[1])
    assert _reserve(ledger).to_state == "reserved"
    assert ledger.transition(nonce=NONCE, to_state="merge_started", statement_sha256=SHA).seq == 1
    monkeypatch.undo()
    assert checked.count(f"{NONCE}.jsonl") == 6


def test_root_change_seen_just_before_the_write_leaves_the_nonce_file_empty(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    real_check, real_commit = NonceLedger._check_root, NonceLedger._commit
    changed = []

    def check(self):
        if changed:
            raise LedgerError("ledger_root_changed", "fixture")
        real_check(self)

    def commit(self, nonce, fd, prior, record):
        changed.append(True)
        return real_commit(self, nonce, fd, prior, record)

    monkeypatch.setattr(NonceLedger, "_check_root", check)
    monkeypatch.setattr(NonceLedger, "_commit", commit)
    with pytest.raises(LedgerError) as err:
        _reserve(ledger)
    monkeypatch.undo()
    assert err.value.reason == "ledger_root_changed"
    assert (ledger._root / f"{NONCE}.jsonl").stat().st_size == 0


def test_fsync_failure_alone_keeps_its_reason_without_cleanup_notes(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(mms, "os", _OsProxy(fsync=_fsync_fails))
    with pytest.raises(LedgerError) as err:
        _reserve(ledger)
    monkeypatch.undo()
    assert err.value.reason == "ledger_write_failed" and err.value.__cause__.errno == 28
    assert err.value.cleanup_failure is None and not hasattr(err.value, "__notes__")


@pytest.mark.parametrize("operation", ["reserve", "transition"])
def test_close_failures_after_an_fsync_failure_never_replace_it(tmp_path, monkeypatch, operation):
    ledger = _ready(tmp_path, operation)
    monkeypatch.setattr(mms, "os", _OsProxy(fsync=_fsync_fails, close=_close_then_fail("SECONDARY injected close")))
    with pytest.raises(LedgerError) as err:
        _write(ledger, operation)
    monkeypatch.undo()
    assert err.value.reason == "ledger_write_failed" and err.value.__cause__.errno == 28
    assert err.value.cleanup_failure == "nonce file close OSError; lock close OSError"
    assert any("ledger cleanup also failed" in note for note in err.value.__notes__)


def test_unlock_failure_after_an_fsync_failure_never_replaces_it(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(mms, "os", _OsProxy(fsync=_fsync_fails))
    _unlock_then_fail(monkeypatch)
    with pytest.raises(LedgerError) as err:
        _reserve(ledger)
    monkeypatch.undo()
    assert err.value.reason == "ledger_write_failed"
    assert err.value.cleanup_failure == "lock unlock PermissionError"
    with pytest.raises(LedgerError) as again:
        _reserve(ledger)
    assert again.value.reason == "nonce_reused"


def test_close_failure_after_a_committed_reserve_refuses_visibly_and_is_not_retried(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(mms, "os", _OsProxy(close=_close_then_fail("ONLY injected close")))
    with pytest.raises(LedgerError) as err:
        _reserve(ledger)
    monkeypatch.undo()
    assert err.value.reason == "ledger_cleanup_failed"
    assert err.value.detail == "nonce file close OSError; lock close OSError"
    assert ledger.state(NONCE) == "reserved"
    for nonce, reason in ((NONCE, "nonce_reused"), ("f" * 32, "ledger_in_flight")):
        with pytest.raises(LedgerError) as again:
            _reserve(ledger, nonce=nonce)
        assert again.value.reason == reason


def test_unlock_failure_after_a_good_history_read_refuses_visibly(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    _reserve(ledger)
    assert [r.to_state for r in ledger.history(NONCE)] == ["reserved"]
    _unlock_then_fail(monkeypatch)
    with pytest.raises(LedgerError) as err:
        ledger.history(NONCE)
    monkeypatch.undo()
    assert err.value.reason == "ledger_cleanup_failed" and err.value.detail == "lock unlock PermissionError"
    assert ledger.state(NONCE) == "reserved"


def test_read_failure_keeps_its_reason_when_file_and_lock_closes_also_fail(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    _reserve(ledger)

    def read_fails(fd, size):
        raise OSError(5, "ORIGINAL injected read")

    monkeypatch.setattr(mms, "os", _OsProxy(read=read_fails, close=_close_then_fail("SECONDARY injected close")))
    with pytest.raises(LedgerError) as err:
        ledger.history(NONCE)
    monkeypatch.undo()
    assert err.value.reason == "ledger_unreadable"
    assert err.value.cleanup_failure == "nonce file close OSError; lock close OSError"


def test_root_failure_after_locking_keeps_its_reason_when_unlock_also_fails(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    real = NonceLedger._check_root
    calls = []

    def check(self):
        calls.append(True)
        if len(calls) == 2:  # the re-check made once the lock is held
            raise LedgerError("ledger_root_changed", "fixture")
        real(self)

    monkeypatch.setattr(NonceLedger, "_check_root", check)
    _unlock_then_fail(monkeypatch)
    with pytest.raises(LedgerError) as err:
        _reserve(ledger)
    monkeypatch.undo()
    assert err.value.reason == "ledger_root_changed"
    assert err.value.cleanup_failure == "lock unlock PermissionError"
    assert not (ledger._root / f"{NONCE}.jsonl").exists()
    assert _reserve(ledger).to_state == "reserved"


def test_lock_timeout_keeps_its_reason_when_the_lock_close_also_fails(tmp_path, monkeypatch):
    holder = _ledger(tmp_path)
    held = holder._acquire()
    try:
        waiter = _ledger(tmp_path, lock_timeout_seconds=0)
        monkeypatch.setattr(mms, "os", _OsProxy(close=_close_then_fail("SECONDARY injected close")))
        with pytest.raises(LedgerError) as err:
            _reserve(waiter)
        monkeypatch.undo()
    finally:
        holder._release(held)
    assert err.value.reason == "ledger_lock_timeout"
    assert err.value.cleanup_failure == "lock close OSError"


def test_an_interruption_during_cleanup_is_not_converted(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)

    def close(fd):
        os.close(fd)
        raise KeyboardInterrupt

    monkeypatch.setattr(mms, "os", _OsProxy(close=close))
    with pytest.raises(KeyboardInterrupt):
        _reserve(ledger)
    monkeypatch.undo()
    assert ledger.state(NONCE) == "reserved"


def _interrupt_after_closing(interrupt, name):
    """Really open and close; raise ``interrupt`` right after the file ``name`` is closed."""
    names = {}

    def open_(path, flags, mode=0o777):
        fd = os.open(path, flags, mode)
        names[fd] = Path(path).name
        return fd

    def close(fd):
        closed = names.pop(fd, None)
        os.close(fd)
        if closed == name:
            raise interrupt("INJECTED cleanup interrupt")

    return {"open": open_, "close": close}


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("closing", [f"{NONCE}.jsonl", mms.LEDGER_LOCK_NAME])
def test_interrupted_close_after_fsync_failure_replaces_it(tmp_path, monkeypatch, interrupt, closing):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(mms, "os", _OsProxy(fsync=_fsync_fails, **_interrupt_after_closing(interrupt, closing)))
    with pytest.raises(interrupt) as err:
        _reserve(ledger)
    monkeypatch.undo()
    assert err.value.__cause__ is None and not hasattr(err.value, "__notes__")
    primary = err.value.__context__  # implicit chaining only: the interrupted step noted nothing
    assert type(primary) is LedgerError and primary.reason == "ledger_write_failed"
    assert primary.__cause__.errno == 28
    assert primary.cleanup_failure is None and not hasattr(primary, "__notes__")
    assert ledger.state(NONCE) == "reserved"  # the written bytes stay for reconciliation
    with pytest.raises(LedgerError) as again:
        _reserve(ledger)
    assert again.value.reason == "nonce_reused"


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("closing", [f"{NONCE}.jsonl", mms.LEDGER_LOCK_NAME])
def test_interrupted_close_after_healthy_write_replaces_success(tmp_path, monkeypatch, interrupt, closing):
    ledger = _ledger(tmp_path)
    monkeypatch.setattr(mms, "os", _OsProxy(**_interrupt_after_closing(interrupt, closing)))
    with pytest.raises(interrupt) as err:
        _reserve(ledger)
    monkeypatch.undo()
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert not hasattr(err.value, "__notes__")
    assert ledger.state(NONCE) == "reserved"  # committed, yet the caller never received the record
    with pytest.raises(LedgerError) as again:
        _reserve(ledger)
    assert again.value.reason == "nonce_reused"


# --- T06 module hygiene ---------------------------------------------------------

_ALLOWED_IMPORTS = {
    "__future__", "base64", "dataclasses", "datetime", "hashlib", "json", "math", "os", "pathlib", "re",
    "secrets", "shutil", "stat", "struct", "subprocess", "sys", "tempfile", "time", "typing", "msvcrt", "fcntl",
}


def test_module_imports_only_stdlib_and_no_gate_bridge_or_receipt_code():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= _ALLOWED_IMPORTS, names - _ALLOWED_IMPORTS
    text = MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in ("gh pr", "events.jsonl", "write_receipt", "check_bridge", "idle_consensus", "--admin"):
        assert forbidden not in text


def test_module_top_level_has_no_side_effect_statements():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    allowed = (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign, ast.FunctionDef, ast.ClassDef)
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        assert isinstance(node, allowed), ast.dump(node)[:120]


def test_import_in_fresh_interpreter_creates_no_files(tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
         "import tools.manual_bridge_merge_statement as m; print(m.NAMESPACE)", str(ROOT)],
        cwd=tmp_path, capture_output=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.decode().strip() == "waggledance-manual-merge-a"
    assert sorted(p.name for p in tmp_path.iterdir()) == before
