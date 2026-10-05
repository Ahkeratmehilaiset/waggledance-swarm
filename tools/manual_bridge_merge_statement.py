# SPDX-License-Identifier: BUSL-1.1
"""Signed operator statement for the manual (a)-class merge route (MANUAL-A).

This module is the first slice of the manual merge + MAGMA receipt route
(operator decision 8A480508, Lead plan F9D23F10).  It contains only:

* the canonical statement contract (fixed field order, canonical UTF-8 bytes
  without BOM, exact constants for namespace / principal / purpose);
* loading the ``allowed_signers`` trust anchor exclusively from the trusted
  base commit (``git rev-parse <base>:<path>`` + ``git cat-file blob``), never
  from a PR head or a worktree file;
* ``ssh-keygen -Y verify`` invocation with an argument list (no shell) and the
  exact statement bytes on stdin;
* a one-time nonce ledger (exclusive create per nonce, OS file lock, explicit
  state machine, no retry).

It does not merge, write receipts, read or write the bridge, call GitHub or
call any existing gate code.  Importing it has no side effects.

Honest limits (also in the route documentation):

* ``ssh-keygen -Y verify`` cannot tell a passphrase-protected ``ssh-ed25519``
  key from an unprotected one, and whether the FIDO user-presence flag is
  enforced for ``sk-ssh-ed25519@openssh.com`` signatures is UNKNOWN on this
  host.
* Unit tests use an injected runner; their results are ``unit_mock`` evidence,
  never a real cryptographic proof.
* Any self-check inside this file detects accidental drift only; it is not a
  barrier against a modified verifier.  The operator runs the route only from a
  clean checkout of the trusted main tip.
* The nonce ledger is local state: deleting it is not cryptographically
  prevented.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence

STATEMENT_SCHEMA = "wd.manual-merge-a.statement.v1"
NAMESPACE = "waggledance-manual-merge-a"
PRINCIPAL = "operator@waggledance"
PURPOSE = "manual-merge-receipt"
REPOSITORY = "Ahkeratmehilaiset/waggledance-swarm"
ALLOWED_SIGNERS_PATH = "ops/security/manual-merge.allowed_signers"
MERGE_METHOD = "squash"
OPERATION_SCOPE = "merge-single-pr"

FIELD_ORDER: tuple[str, ...] = (
    "schema",
    "namespace",
    "principal",
    "purpose",
    "repository",
    "pull_request",
    "head_sha",
    "base_sha",
    "diff_digest_sha256",
    "exact_paths",
    "merge_method",
    "batch_id",
    "batch_order",
    "dependencies",
    "operation_scope",
    "expires_at_utc",
    "nonce",
    "allowed_signers_path",
    "allowed_signers_blob_sha",
    "key_fingerprint",
)

# ed25519-sk (FIDO2, touch) is preferred; ssh-ed25519 is the operator's
# passphrase fallback.  No other key algorithm is admitted.
ALLOWED_KEY_TYPES: tuple[str, ...] = ("sk-ssh-ed25519@openssh.com", "ssh-ed25519")
KEY_TYPE_LABELS: Mapping[str, str] = {
    "sk-ssh-ed25519@openssh.com": "ED25519-SK",
    "ssh-ed25519": "ED25519",
}
ANCHOR_OPTIONS = f'namespaces="{NAMESPACE}"'
SSH_VERIFY_TIMEOUT_SECONDS = 30.0
GIT_TIMEOUT_SECONDS = 30.0
MAX_ANCHOR_BYTES = 16 * 1024
MAX_SIGNATURE_BYTES = 16 * 1024
MAX_STATEMENT_BYTES = 1024 * 1024
MAX_PATHS = 10000
MAX_DEPENDENCIES = 100
MAX_PR_NUMBER = 10**7
MAX_BATCH_ORDER = 1000

SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
NONCE_RE = re.compile(r"[0-9a-f]{32}")
BATCH_ID_RE = re.compile(r"mma-[0-9]{8}-[a-z0-9-]{1,40}")
EXPIRY_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z")
FINGERPRINT_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
SHORT_NAME_RE = re.compile(r"~[0-9]")
WINDOWS_RESERVED_PATH_CHARS = frozenset('<>:"|?*\\')
SIGNATURE_BEGIN = b"-----BEGIN SSH SIGNATURE-----"
SIGNATURE_END = b"-----END SSH SIGNATURE-----"


class StatementError(ValueError):
    """Fail-closed refusal with a stable reason code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: bytes
    stderr: bytes


# runner(argv, *, input_bytes, timeout, env) -> RunResult.  Production uses the
# subprocess runner below; tests inject deterministic fakes (unit/mock only).
Runner = Callable[..., RunResult]


def _subprocess_runner(
    argv: Sequence[str],
    *,
    input_bytes: bytes | None,
    timeout: float,
    env: Mapping[str, str] | None,
) -> RunResult:
    completed = subprocess.run(  # noqa: S603 - argument list, never a shell
        list(argv),
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        env=dict(env) if env is not None else None,
        shell=False,
        check=False,
    )
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class Statement:
    schema: str
    namespace: str
    principal: str
    purpose: str
    repository: str
    pull_request: int
    head_sha: str
    base_sha: str
    diff_digest_sha256: str
    exact_paths: tuple[str, ...]
    merge_method: str
    batch_id: str
    batch_order: int
    dependencies: tuple[int, ...]
    operation_scope: str
    expires_at_utc: str
    nonce: str
    allowed_signers_path: str
    allowed_signers_blob_sha: str
    key_fingerprint: str

    def to_ordered_mapping(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for name in FIELD_ORDER:
            value = getattr(self, name)
            values[name] = list(value) if isinstance(value, tuple) else value
        return values


@dataclass(frozen=True)
class TrustAnchor:
    trusted_commit: str
    path: str
    blob_sha: str
    data_sha256: str
    data: bytes
    key_type: str
    key_label: str
    fingerprint: str


@dataclass(frozen=True)
class VerificationResult:
    statement_sha256: str
    signature_sha256: str
    trusted_commit: str
    anchor_blob_sha: str
    anchor_data_sha256: str
    key_type: str
    key_fingerprint: str
    verifier_argv: tuple[str, ...]
    verifier_returncode: int
    stdout_sha256: str
    stderr_sha256: str
    good_line: str
    ssh_keygen_path: str
    ssh_keygen_sha256: str | None
    # "subprocess_ssh_keygen" only when the default subprocess runner ran the
    # given binary; "unit_mock" whenever a runner was injected.
    evidence_class: str


@dataclass(frozen=True)
class VerifiedStatement:
    statement: Statement
    statement_sha256: str
    anchor: TrustAnchor
    verification: VerificationResult


# --- statement contract -------------------------------------------------------


def new_nonce() -> str:
    return secrets.token_hex(16)


def build_statement(
    *,
    pull_request: int,
    head_sha: str,
    base_sha: str,
    diff_digest_sha256: str,
    exact_paths: Sequence[str],
    batch_id: str,
    batch_order: int,
    dependencies: Sequence[int],
    expires_at_utc: str,
    nonce: str,
    allowed_signers_blob_sha: str,
    key_fingerprint: str,
) -> Statement:
    """Build a validated statement; the constant fields are fixed here."""
    if isinstance(exact_paths, (str, bytes)) or not isinstance(exact_paths, Sequence):
        raise StatementError("invalid_field:exact_paths", "must be a sequence of paths")
    if isinstance(dependencies, (str, bytes)) or not isinstance(dependencies, Sequence):
        raise StatementError("invalid_field:dependencies", "must be a sequence of ints")
    statement = Statement(
        schema=STATEMENT_SCHEMA,
        namespace=NAMESPACE,
        principal=PRINCIPAL,
        purpose=PURPOSE,
        repository=REPOSITORY,
        pull_request=pull_request,
        head_sha=head_sha,
        base_sha=base_sha,
        diff_digest_sha256=diff_digest_sha256,
        exact_paths=tuple(exact_paths),
        merge_method=MERGE_METHOD,
        batch_id=batch_id,
        batch_order=batch_order,
        dependencies=tuple(dependencies),
        operation_scope=OPERATION_SCOPE,
        expires_at_utc=expires_at_utc,
        nonce=nonce,
        allowed_signers_path=ALLOWED_SIGNERS_PATH,
        allowed_signers_blob_sha=allowed_signers_blob_sha,
        key_fingerprint=key_fingerprint,
    )
    validate_statement(statement)
    return statement


def canonical_statement_bytes(statement: Statement) -> bytes:
    validate_statement(statement)
    text = json.dumps(
        statement.to_ordered_mapping(),
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def statement_sha256(statement_bytes: bytes) -> str:
    return hashlib.sha256(statement_bytes).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StatementError("duplicate_key", key)
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise StatementError("non_canonical_bytes", f"JSON constant {token} is not allowed")


def parse_statement(data: bytes) -> Statement:
    """Parse canonical statement bytes; any alternative representation refuses."""
    if not isinstance(data, (bytes, bytearray)):
        raise StatementError("non_canonical_bytes", "statement must be bytes")
    data = bytes(data)
    if not data or len(data) > MAX_STATEMENT_BYTES:
        raise StatementError("non_canonical_bytes", "empty or oversized statement")
    if data.startswith(b"\xef\xbb\xbf"):
        raise StatementError("non_canonical_bytes", "BOM is not allowed")
    if b"\r" in data:
        raise StatementError("non_canonical_bytes", "CR is not allowed")
    if not data.endswith(b"\n") or data.endswith(b"\n\n") or data.count(b"\n") != 1:
        raise StatementError("non_canonical_bytes", "exactly one trailing LF is required")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StatementError("non_canonical_bytes", "invalid UTF-8") from exc
    try:
        decoded = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except StatementError:
        raise
    except ValueError as exc:
        raise StatementError("non_canonical_bytes", "invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise StatementError("non_canonical_bytes", "statement must be a JSON object")
    for name in FIELD_ORDER:
        if name not in decoded:
            raise StatementError(f"missing_field:{name}")
    for name in decoded:
        if name not in FIELD_ORDER:
            raise StatementError(f"unexpected_field:{name}")
    exact_paths = decoded["exact_paths"]
    dependencies = decoded["dependencies"]
    if not isinstance(exact_paths, list):
        raise StatementError("invalid_field:exact_paths", "must be a JSON array")
    if not isinstance(dependencies, list):
        raise StatementError("invalid_field:dependencies", "must be a JSON array")
    statement = Statement(
        **{
            name: tuple(decoded[name]) if name in ("exact_paths", "dependencies") else decoded[name]
            for name in FIELD_ORDER
        }
    )
    validate_statement(statement)
    if canonical_statement_bytes(statement) != data:
        raise StatementError("non_canonical_bytes", "re-serialization differs from input")
    return statement


def _is_int(value: Any) -> bool:
    return type(value) is int


def _require_exact(statement: Statement, name: str, expected: str) -> None:
    if getattr(statement, name) != expected or type(getattr(statement, name)) is not str:
        raise StatementError(f"invalid_field:{name}", f"must be exactly {expected!r}")


def _require_hex(statement: Statement, name: str, pattern: re.Pattern[str]) -> None:
    value = getattr(statement, name)
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise StatementError(f"invalid_field:{name}", "malformed lowercase hex value")


def validate_repo_path(path: Any) -> str:
    """Return ``path`` when it is a safe repo-relative POSIX path, else refuse."""
    if type(path) is not str or not path or len(path) > 4096:
        raise StatementError("invalid_path", "path must be a non-empty string")
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        raise StatementError("invalid_path", "control character in path")
    if any(char in WINDOWS_RESERVED_PATH_CHARS for char in path):
        raise StatementError("invalid_path", "reserved character in path")
    if path.startswith("/"):
        raise StatementError("invalid_path", "absolute path")
    for segment in path.split("/"):
        if segment in ("", ".", ".."):
            raise StatementError("invalid_path", "empty or dot segment")
        if segment != segment.strip() or segment.endswith("."):
            raise StatementError("invalid_path", "segment ends with space or dot")
        if SHORT_NAME_RE.search(segment):
            raise StatementError("invalid_path", "short-name style segment")
    return path


def validate_statement(statement: Statement) -> None:
    if not isinstance(statement, Statement):
        raise StatementError("invalid_statement", "expected a Statement")
    _require_exact(statement, "schema", STATEMENT_SCHEMA)
    _require_exact(statement, "namespace", NAMESPACE)
    _require_exact(statement, "principal", PRINCIPAL)
    _require_exact(statement, "purpose", PURPOSE)
    _require_exact(statement, "repository", REPOSITORY)
    _require_exact(statement, "merge_method", MERGE_METHOD)
    _require_exact(statement, "operation_scope", OPERATION_SCOPE)
    _require_exact(statement, "allowed_signers_path", ALLOWED_SIGNERS_PATH)
    if not _is_int(statement.pull_request) or not 1 <= statement.pull_request <= MAX_PR_NUMBER:
        raise StatementError("invalid_field:pull_request", "must be an int in range")
    _require_hex(statement, "head_sha", SHA1_RE)
    _require_hex(statement, "base_sha", SHA1_RE)
    if statement.head_sha == statement.base_sha:
        raise StatementError("invalid_field:head_sha", "head equals base")
    _require_hex(statement, "diff_digest_sha256", SHA256_RE)
    _require_hex(statement, "allowed_signers_blob_sha", SHA1_RE)
    _require_hex(statement, "nonce", NONCE_RE)
    paths = statement.exact_paths
    if type(paths) is not tuple or not paths or len(paths) > MAX_PATHS:
        raise StatementError("invalid_field:exact_paths", "must be a non-empty bounded list")
    for path in paths:
        validate_repo_path(path)
    if list(paths) != sorted(set(paths)):
        raise StatementError("invalid_field:exact_paths", "must be sorted and unique")
    if ALLOWED_SIGNERS_PATH in paths:
        raise StatementError("allowed_signers_changed", "the PR changes the trust anchor")
    if type(statement.batch_id) is not str or BATCH_ID_RE.fullmatch(statement.batch_id) is None:
        raise StatementError("invalid_field:batch_id", "malformed batch id")
    if not _is_int(statement.batch_order) or not 1 <= statement.batch_order <= MAX_BATCH_ORDER:
        raise StatementError("invalid_field:batch_order", "must be an int in range")
    dependencies = statement.dependencies
    if type(dependencies) is not tuple or len(dependencies) > MAX_DEPENDENCIES:
        raise StatementError("invalid_field:dependencies", "must be a bounded list")
    for dependency in dependencies:
        if not _is_int(dependency) or not 1 <= dependency <= MAX_PR_NUMBER:
            raise StatementError("invalid_field:dependencies", "must contain PR ints")
    if list(dependencies) != sorted(set(dependencies)):
        raise StatementError("invalid_field:dependencies", "must be sorted and unique")
    if statement.pull_request in dependencies:
        raise StatementError("invalid_field:dependencies", "a PR cannot depend on itself")
    parse_expiry(statement.expires_at_utc)
    if type(statement.key_fingerprint) is not str or FINGERPRINT_RE.fullmatch(
        statement.key_fingerprint
    ) is None:
        raise StatementError("invalid_field:key_fingerprint", "malformed SHA256 fingerprint")


def parse_expiry(value: Any) -> datetime:
    """Parse ``YYYY-MM-DDTHH:MM:SSZ`` as an aware UTC datetime (real calendar time)."""
    if type(value) is not str:
        raise StatementError("invalid_field:expires_at_utc", "must be a string")
    match = EXPIRY_RE.fullmatch(value)
    if match is None:
        raise StatementError("invalid_field:expires_at_utc", "must be YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime(*(int(part) for part in match.groups()), tzinfo=timezone.utc)
    except ValueError as exc:
        raise StatementError("invalid_field:expires_at_utc", "not a real calendar time") from exc


def _require_utc_clock(now_utc: Any) -> datetime:
    if (
        not isinstance(now_utc, datetime)
        or now_utc.tzinfo is None
        or now_utc.utcoffset() != timedelta(0)
    ):
        raise StatementError("invalid_clock", "now_utc must be an aware UTC datetime")
    return now_utc


def check_statement_expiry(statement: Statement, *, now_utc: datetime) -> None:
    """Refuse unless the absolute expiry is strictly later than ``now_utc``.

    There is no implicit maximum lifetime: the operator's explicit absolute
    expiration is the contract.  Callers pass the real clock; tests inject it.
    """
    now = _require_utc_clock(now_utc)
    if parse_expiry(statement.expires_at_utc) <= now:
        raise StatementError("statement_expired", statement.expires_at_utc)


def check_statement_binding(
    statement: Statement,
    *,
    anchor: TrustAnchor,
    expected_head_sha: str,
    expected_base_sha: str,
    live_changed_paths: Sequence[str],
    now_utc: datetime,
) -> None:
    """Bind the statement to the trusted anchor and the caller's live PR facts."""
    validate_statement(statement)
    if not isinstance(anchor, TrustAnchor):
        raise StatementError("anchor_missing", "no trusted anchor")
    for label, value in (("expected_head_sha", expected_head_sha), ("expected_base_sha", expected_base_sha)):
        if type(value) is not str or SHA1_RE.fullmatch(value) is None:
            raise StatementError("invalid_live_fact", label)
    if isinstance(live_changed_paths, (str, bytes)) or not isinstance(live_changed_paths, Sequence):
        raise StatementError("invalid_live_fact", "live_changed_paths")
    if statement.base_sha != anchor.trusted_commit:
        raise StatementError("anchor_not_from_statement_base", anchor.trusted_commit)
    if statement.base_sha != expected_base_sha:
        raise StatementError("base_mismatch", expected_base_sha)
    if statement.head_sha != expected_head_sha:
        raise StatementError("signed_head_stale", expected_head_sha)
    if statement.allowed_signers_blob_sha != anchor.blob_sha:
        raise StatementError("anchor_blob_mismatch", anchor.blob_sha)
    if statement.key_fingerprint != anchor.fingerprint:
        raise StatementError("key_fingerprint_mismatch", anchor.fingerprint)
    if ALLOWED_SIGNERS_PATH in live_changed_paths:
        raise StatementError("allowed_signers_changed", "the live PR diff changes the trust anchor")
    check_statement_expiry(statement, now_utc=now_utc)


# --- trust anchor -------------------------------------------------------------


def _git_env() -> dict[str, str]:
    # GIT_DIR / GIT_INDEX_FILE etc. would override ``git -C``; drop them all.
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}


def _run_git(
    runner: Runner,
    git_executable: str,
    repo_root: Path,
    args: Sequence[str],
) -> RunResult:
    argv = [git_executable, "-C", str(repo_root), *args]
    try:
        result = runner(argv, input_bytes=None, timeout=GIT_TIMEOUT_SECONDS, env=_git_env())
    except subprocess.TimeoutExpired as exc:
        raise StatementError("git_unavailable", "git timed out") from exc
    except OSError as exc:
        raise StatementError("git_unavailable", type(exc).__name__) from exc
    if not isinstance(result, RunResult):
        raise StatementError("git_unavailable", "runner returned an unexpected result")
    return result


def _read_ssh_string(blob: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(blob):
        raise StatementError("anchor_invalid", "truncated key blob")
    (length,) = struct.unpack(">I", blob[offset : offset + 4])
    start = offset + 4
    end = start + length
    if end > len(blob):
        raise StatementError("anchor_invalid", "truncated key blob")
    return blob[start:end], end


def _validate_key_blob(key_type: str, blob: bytes) -> None:
    inner_type, offset = _read_ssh_string(blob, 0)
    if inner_type != key_type.encode("ascii"):
        raise StatementError("anchor_invalid", "key blob type differs from the line type")
    public_key, offset = _read_ssh_string(blob, offset)
    if len(public_key) != 32:
        raise StatementError("anchor_invalid", "ed25519 public key must be 32 bytes")
    if key_type == "sk-ssh-ed25519@openssh.com":
        application, offset = _read_ssh_string(blob, offset)
        if (
            not application.startswith(b"ssh:")
            or len(application) > 255
            or any(byte < 0x21 or byte > 0x7E for byte in application)
        ):
            raise StatementError("anchor_invalid", "invalid FIDO application string")
    if offset != len(blob):
        raise StatementError("anchor_invalid", "trailing bytes in key blob")


def key_fingerprint(blob: bytes) -> str:
    digest = hashlib.sha256(blob).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def parse_allowed_signers(data: bytes) -> tuple[str, str]:
    """Return ``(key_type, fingerprint)`` for the single admitted signer line."""
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > MAX_ANCHOR_BYTES:
        raise StatementError("anchor_invalid", "empty or oversized anchor")
    data = bytes(data)
    if data.startswith(b"\xef\xbb\xbf") or b"\r" in data or b"\x00" in data:
        raise StatementError("anchor_invalid", "BOM, CR or NUL in anchor")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise StatementError("anchor_invalid", "anchor must be ASCII") from exc
    signer_lines = [
        line for line in text.split("\n") if line.strip() and not line.startswith("#")
    ]
    if len(signer_lines) != 1:
        raise StatementError("anchor_invalid", "exactly one signer line is required")
    parts = signer_lines[0].split(" ")
    if len(parts) < 4 or any(part == "" for part in parts[:4]):
        raise StatementError("anchor_invalid", "malformed signer line")
    principal, options, key_type, key_base64 = parts[:4]
    comment = " ".join(parts[4:])
    if principal != PRINCIPAL:
        raise StatementError("anchor_invalid", "principal differs from the operator principal")
    if options != ANCHOR_OPTIONS:
        raise StatementError("anchor_invalid", "options must be exactly the operator namespace")
    if key_type not in ALLOWED_KEY_TYPES:
        raise StatementError("anchor_key_type_not_allowed", key_type)
    if comment and any(ord(char) < 0x20 or ord(char) > 0x7E for char in comment):
        raise StatementError("anchor_invalid", "invalid comment")
    try:
        blob = base64.b64decode(key_base64.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise StatementError("anchor_invalid", "key is not base64") from exc
    _validate_key_blob(key_type, blob)
    return key_type, key_fingerprint(blob)


def load_trust_anchor(
    *,
    repo_root: Path,
    trusted_commit: str,
    runner: Runner | None = None,
    git_executable: str = "git",
) -> TrustAnchor:
    """Load ``allowed_signers`` only from ``trusted_commit`` (never a worktree file)."""
    if type(trusted_commit) is not str or SHA1_RE.fullmatch(trusted_commit) is None:
        raise StatementError("anchor_base_unknown", "trusted commit must be a 40-hex sha")
    if not isinstance(repo_root, Path) or not repo_root.is_absolute():
        raise StatementError("anchor_base_unknown", "repo_root must be an absolute Path")
    run = runner if runner is not None else _subprocess_runner
    commit = _run_git(
        run, git_executable, repo_root, ["rev-parse", "--verify", "--quiet", f"{trusted_commit}^{{commit}}"]
    )
    if commit.returncode != 0 or commit.stdout.strip() != trusted_commit.encode("ascii"):
        raise StatementError("anchor_base_unknown", trusted_commit)
    resolved = _run_git(
        run, git_executable, repo_root, ["rev-parse", "--verify", "--quiet", f"{trusted_commit}:{ALLOWED_SIGNERS_PATH}"]
    )
    blob_sha = resolved.stdout.strip().decode("ascii", "replace")
    if resolved.returncode != 0 or SHA1_RE.fullmatch(blob_sha) is None:
        raise StatementError("anchor_missing", ALLOWED_SIGNERS_PATH)
    kind = _run_git(run, git_executable, repo_root, ["cat-file", "-t", blob_sha])
    if kind.returncode != 0 or kind.stdout.strip() != b"blob":
        raise StatementError("anchor_invalid", "trust anchor is not a blob")
    content = _run_git(run, git_executable, repo_root, ["cat-file", "blob", blob_sha])
    if content.returncode != 0:
        raise StatementError("anchor_missing", "cat-file failed")
    data = content.stdout
    header = b"blob " + str(len(data)).encode("ascii") + b"\x00"
    if hashlib.sha1(header + data).hexdigest() != blob_sha:  # noqa: S324 - git object id
        raise StatementError("anchor_integrity_mismatch", blob_sha)
    key_type, fingerprint = parse_allowed_signers(data)
    return TrustAnchor(
        trusted_commit=trusted_commit,
        path=ALLOWED_SIGNERS_PATH,
        blob_sha=blob_sha,
        data_sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        key_type=key_type,
        key_label=KEY_TYPE_LABELS[key_type],
        fingerprint=fingerprint,
    )


# --- signature verification ---------------------------------------------------


def _ssh_env() -> dict[str, str]:
    if sys.platform == "win32":
        system_root = os.environ.get("SystemRoot") or os.environ.get("SYSTEMROOT") or r"C:\Windows"
        return {
            "SystemRoot": system_root,
            "WINDIR": system_root,
            "PATH": os.path.join(system_root, "System32"),
        }
    return {"PATH": "/usr/bin:/bin"}


def _validate_signature_armor(signature_bytes: Any) -> bytes:
    if not isinstance(signature_bytes, (bytes, bytearray)):
        raise StatementError("signature_malformed", "signature must be bytes")
    data = bytes(signature_bytes)
    if not data or len(data) > MAX_SIGNATURE_BYTES:
        raise StatementError("signature_malformed", "empty or oversized signature")
    try:
        data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise StatementError("signature_malformed", "signature must be ASCII") from exc
    stripped = data.strip()
    if not stripped.startswith(SIGNATURE_BEGIN) or not stripped.endswith(SIGNATURE_END):
        raise StatementError("signature_malformed", "not an armored SSH signature")
    return data


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def verify_statement_signature(
    *,
    statement_bytes: bytes,
    signature_bytes: bytes,
    anchor: TrustAnchor,
    ssh_keygen: Path,
    runner: Runner | None = None,
    timeout_seconds: float = SSH_VERIFY_TIMEOUT_SECONDS,
) -> VerificationResult:
    """Run ``ssh-keygen -Y verify`` over the exact canonical statement bytes."""
    parse_statement(statement_bytes)
    signature = _validate_signature_armor(signature_bytes)
    if not isinstance(anchor, TrustAnchor):
        raise StatementError("anchor_missing", "no trusted anchor")
    if not isinstance(ssh_keygen, Path) or not ssh_keygen.is_absolute():
        raise StatementError("verifier_unavailable", "ssh-keygen must be an absolute Path")
    if runner is None and not ssh_keygen.is_file():
        raise StatementError("verifier_unavailable", "ssh-keygen binary not found")
    run = runner if runner is not None else _subprocess_runner
    temp_dir = Path(tempfile.mkdtemp(prefix="wd-manual-merge-a-"))
    try:
        anchor_copy = temp_dir / "allowed_signers"
        signature_copy = temp_dir / "statement.sig"
        anchor_copy.write_bytes(anchor.data)
        signature_copy.write_bytes(signature)
        argv = [
            str(ssh_keygen),
            "-Y",
            "verify",
            "-f",
            str(anchor_copy),
            "-I",
            PRINCIPAL,
            "-n",
            NAMESPACE,
            "-s",
            str(signature_copy),
        ]
        try:
            result = run(argv, input_bytes=statement_bytes, timeout=timeout_seconds, env=_ssh_env())
        except subprocess.TimeoutExpired as exc:
            raise StatementError("verifier_timeout", "ssh-keygen timed out") from exc
        except OSError as exc:
            raise StatementError("verifier_unavailable", type(exc).__name__) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    if not isinstance(result, RunResult):
        raise StatementError("verifier_unavailable", "runner returned an unexpected result")
    if result.returncode != 0:
        raise StatementError("signature_invalid", f"ssh-keygen exit {result.returncode}")
    expected = (
        f'Good "{NAMESPACE}" signature for {PRINCIPAL} with {anchor.key_label} key '
        f"{anchor.fingerprint}"
    )
    try:
        stdout_text = result.stdout.decode("ascii")
    except UnicodeDecodeError as exc:
        raise StatementError("signature_output_unexpected", "non-ASCII verifier output") from exc
    lines = [line for line in stdout_text.replace("\r\n", "\n").split("\n") if line != ""]
    if lines != [expected]:
        raise StatementError("signature_output_unexpected", "verifier output is not the exact Good line")
    placeholder_argv = tuple(
        "<anchor-temp-copy>" if arg == str(anchor_copy)
        else "<signature-temp-copy>" if arg == str(signature_copy)
        else arg
        for arg in argv
    )
    return VerificationResult(
        statement_sha256=statement_sha256(statement_bytes),
        signature_sha256=hashlib.sha256(signature).hexdigest(),
        trusted_commit=anchor.trusted_commit,
        anchor_blob_sha=anchor.blob_sha,
        anchor_data_sha256=anchor.data_sha256,
        key_type=anchor.key_type,
        key_fingerprint=anchor.fingerprint,
        verifier_argv=placeholder_argv,
        verifier_returncode=result.returncode,
        stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
        stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
        good_line=expected,
        ssh_keygen_path=str(ssh_keygen),
        ssh_keygen_sha256=_file_sha256(ssh_keygen),
        evidence_class="unit_mock" if runner is not None else "subprocess_ssh_keygen",
    )


def verify_statement(
    *,
    statement_bytes: bytes,
    signature_bytes: bytes,
    repo_root: Path,
    trusted_commit: str,
    expected_head_sha: str,
    live_changed_paths: Sequence[str],
    now_utc: datetime,
    ssh_keygen: Path,
    runner: Runner | None = None,
    git_runner: Runner | None = None,
    git_executable: str = "git",
) -> VerifiedStatement:
    """Full stateless verification (preview-safe: no nonce is consumed)."""
    statement = parse_statement(statement_bytes)
    anchor = load_trust_anchor(
        repo_root=repo_root,
        trusted_commit=trusted_commit,
        runner=git_runner,
        git_executable=git_executable,
    )
    check_statement_binding(
        statement,
        anchor=anchor,
        expected_head_sha=expected_head_sha,
        expected_base_sha=trusted_commit,
        live_changed_paths=live_changed_paths,
        now_utc=now_utc,
    )
    verification = verify_statement_signature(
        statement_bytes=statement_bytes,
        signature_bytes=signature_bytes,
        anchor=anchor,
        ssh_keygen=ssh_keygen,
        runner=runner,
    )
    return VerifiedStatement(
        statement=statement,
        statement_sha256=verification.statement_sha256,
        anchor=anchor,
        verification=verification,
    )


# --- one-time nonce ledger ----------------------------------------------------

LEDGER_SCHEMA = "wd.manual-merge-a.nonce-ledger.v1"
LEDGER_LOCK_NAME = "ledger.lock"
NONCE_STATES: tuple[str, ...] = (
    "reserved",
    "refused_before_effect",
    "merge_started",
    "executed",
    "indeterminate",
    "reconciled_merged",
    "reconciled_not_merged",
)
ALLOWED_TRANSITIONS: Mapping[str, tuple[str, ...]] = {
    "reserved": ("refused_before_effect", "merge_started"),
    "merge_started": ("executed", "indeterminate"),
    "indeterminate": ("reconciled_merged", "reconciled_not_merged"),
}
TERMINAL_STATES = frozenset(
    {"refused_before_effect", "executed", "reconciled_merged", "reconciled_not_merged"}
)
IN_FLIGHT_STATES = frozenset({"reserved", "merge_started", "indeterminate"})
EVIDENCE_KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
RECORD_KEYS = frozenset(
    {"schema", "seq", "nonce", "from", "to", "ts_utc", "statement_sha256", "pull_request",
     "head_sha", "base_sha", "batch_id", "evidence"}
)


class LedgerError(StatementError):
    """Nonce ledger refusal (subclass so callers can treat both alike)."""


def _canonical_record(record: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(record), sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _validate_evidence(evidence: Any) -> dict[str, Any]:
    if evidence is None:
        return {}
    if not isinstance(evidence, Mapping) or len(evidence) > 64:
        raise LedgerError("ledger_evidence_invalid", "evidence must be a small mapping")
    clean: dict[str, Any] = {}
    for key, value in evidence.items():
        if type(key) is not str or EVIDENCE_KEY_RE.fullmatch(key) is None:
            raise LedgerError("ledger_evidence_invalid", "bad evidence key")
        if value is None or type(value) in (bool, int):
            clean[key] = value
        elif type(value) is str and len(value) <= 512 and all(0x20 <= ord(c) <= 0x7E for c in value):
            clean[key] = value
        else:
            raise LedgerError("ledger_evidence_invalid", f"bad evidence value for {key}")
    return clean


@dataclass(frozen=True)
class LedgerRecord:
    seq: int
    nonce: str
    from_state: str | None
    to_state: str
    ts_utc: str
    statement_sha256: str
    pull_request: int
    head_sha: str
    base_sha: str
    batch_id: str
    evidence: Mapping[str, Any]


class NonceLedger:
    """Append-only, one file per nonce; exclusive create; OS file lock.

    ``reserve`` refuses a nonce that has ever been seen (any state) and refuses
    while any other nonce is still in flight (one unresolved effect at a time).
    Verification alone (``verify_statement``) never touches the ledger.
    """

    def __init__(
        self,
        root: Path,
        *,
        lock_timeout_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(root, Path) or not root.is_absolute():
            raise LedgerError("ledger_root_invalid", "root must be an absolute Path")
        if root.is_symlink() or not root.is_dir():
            raise LedgerError("ledger_root_invalid", "root must be an existing real directory")
        self._root = root
        self._lock_timeout = float(lock_timeout_seconds)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- locking --------------------------------------------------------------

    def _acquire(self) -> int:
        path = self._root / LEDGER_LOCK_NAME
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        deadline = time.monotonic() + self._lock_timeout
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise LedgerError("ledger_lock_timeout", "ledger lock busy") from None
                time.sleep(0.05)

    @staticmethod
    def _release(fd: int) -> None:
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    # -- reading --------------------------------------------------------------

    def _path(self, nonce: str) -> Path:
        if type(nonce) is not str or NONCE_RE.fullmatch(nonce) is None:
            raise LedgerError("invalid_field:nonce", "malformed nonce")
        return self._root / f"{nonce}.jsonl"

    def _read(self, nonce: str) -> tuple[LedgerRecord, ...]:
        path = self._path(nonce)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return ()
        except OSError as exc:
            raise LedgerError("ledger_unreadable", type(exc).__name__) from exc
        if not data.endswith(b"\n"):
            raise LedgerError("ledger_unreadable", "truncated ledger")
        records: list[LedgerRecord] = []
        for seq, line in enumerate(data[:-1].split(b"\n")):
            try:
                raw = json.loads(line.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
            except (ValueError, StatementError) as exc:
                raise LedgerError("ledger_unreadable", "malformed ledger line") from exc
            if not isinstance(raw, dict) or set(raw) != RECORD_KEYS:
                raise LedgerError("ledger_unreadable", "unexpected ledger record keys")
            if _canonical_record(raw) != line + b"\n":
                raise LedgerError("ledger_unreadable", "non-canonical ledger line")
            previous = records[-1] if records else None
            expected_from = previous.to_state if previous else None
            if (
                type(raw["seq"]) is not int
                or type(raw["pull_request"]) is not int
                or type(raw["ts_utc"]) is not str
                or type(raw["statement_sha256"]) is not str
                or SHA256_RE.fullmatch(raw["statement_sha256"]) is None
                or type(raw["head_sha"]) is not str
                or SHA1_RE.fullmatch(raw["head_sha"]) is None
                or type(raw["base_sha"]) is not str
                or SHA1_RE.fullmatch(raw["base_sha"]) is None
                or type(raw["batch_id"]) is not str
                or raw["schema"] != LEDGER_SCHEMA
                or raw["seq"] != seq
                or raw["nonce"] != nonce
                or raw["from"] != expected_from
                or raw["to"] not in NONCE_STATES
                or (previous is None and raw["to"] != "reserved")
                or (previous is not None and raw["to"] not in ALLOWED_TRANSITIONS.get(previous.to_state, ()))
                or (previous is not None and (
                    raw["statement_sha256"] != previous.statement_sha256
                    or raw["pull_request"] != previous.pull_request
                    or raw["head_sha"] != previous.head_sha
                    or raw["base_sha"] != previous.base_sha
                    or raw["batch_id"] != previous.batch_id
                ))
            ):
                raise LedgerError("ledger_unreadable", "inconsistent ledger history")
            records.append(
                LedgerRecord(
                    seq=raw["seq"],
                    nonce=raw["nonce"],
                    from_state=raw["from"],
                    to_state=raw["to"],
                    ts_utc=raw["ts_utc"],
                    statement_sha256=raw["statement_sha256"],
                    pull_request=raw["pull_request"],
                    head_sha=raw["head_sha"],
                    base_sha=raw["base_sha"],
                    batch_id=raw["batch_id"],
                    evidence=_validate_evidence(raw["evidence"]),
                )
            )
        return tuple(records)

    def history(self, nonce: str) -> tuple[LedgerRecord, ...]:
        return self._read(nonce)

    def state(self, nonce: str) -> str | None:
        records = self._read(nonce)
        return records[-1].to_state if records else None

    def _scan(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for entry in sorted(self._root.iterdir()):
            if entry.name == LEDGER_LOCK_NAME:
                continue
            nonce = entry.name[: -len(".jsonl")] if entry.name.endswith(".jsonl") else ""
            if not entry.is_file() or entry.is_symlink() or NONCE_RE.fullmatch(nonce) is None:
                raise LedgerError("ledger_unexpected_entry", entry.name)
            records = self._read(nonce)
            if not records:
                raise LedgerError("ledger_unreadable", "empty ledger file")
            states[nonce] = records[-1].to_state
        return states

    def in_flight(self) -> tuple[str, ...]:
        fd = self._acquire()
        try:
            return tuple(n for n, s in self._scan().items() if s in IN_FLIGHT_STATES)
        finally:
            self._release(fd)

    # -- writing --------------------------------------------------------------

    def _now(self) -> str:
        now = _require_utc_clock(self._clock())
        return now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    @staticmethod
    def _append(path: Path, record: Mapping[str, Any], *, create: bool) -> None:
        flags = os.O_WRONLY | getattr(os, "O_BINARY", 0)
        flags |= (os.O_CREAT | os.O_EXCL) if create else os.O_APPEND
        try:
            fd = os.open(str(path), flags, 0o600)
        except FileExistsError as exc:
            raise LedgerError("nonce_reused", path.stem) from exc
        try:
            os.write(fd, _canonical_record(record))
            os.fsync(fd)
        finally:
            os.close(fd)

    def reserve(
        self,
        *,
        nonce: str,
        statement_sha256: str,
        pull_request: int,
        head_sha: str,
        base_sha: str,
        batch_id: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> LedgerRecord:
        path = self._path(nonce)
        if type(statement_sha256) is not str or SHA256_RE.fullmatch(statement_sha256) is None:
            raise LedgerError("invalid_field:statement_sha256", "malformed digest")
        if not _is_int(pull_request) or not 1 <= pull_request <= MAX_PR_NUMBER:
            raise LedgerError("invalid_field:pull_request", "must be an int in range")
        for label, value in (("head_sha", head_sha), ("base_sha", base_sha)):
            if type(value) is not str or SHA1_RE.fullmatch(value) is None:
                raise LedgerError(f"invalid_field:{label}", "malformed sha")
        if type(batch_id) is not str or BATCH_ID_RE.fullmatch(batch_id) is None:
            raise LedgerError("invalid_field:batch_id", "malformed batch id")
        clean = _validate_evidence(evidence)
        fd = self._acquire()
        try:
            states = self._scan()
            if nonce in states:
                raise LedgerError("nonce_reused", nonce)
            busy = sorted(n for n, s in states.items() if s in IN_FLIGHT_STATES)
            if busy:
                raise LedgerError("ledger_in_flight", ",".join(busy))
            record = {
                "schema": LEDGER_SCHEMA,
                "seq": 0,
                "nonce": nonce,
                "from": None,
                "to": "reserved",
                "ts_utc": self._now(),
                "statement_sha256": statement_sha256,
                "pull_request": pull_request,
                "head_sha": head_sha,
                "base_sha": base_sha,
                "batch_id": batch_id,
                "evidence": clean,
            }
            self._append(path, record, create=True)
        finally:
            self._release(fd)
        return self._read(nonce)[-1]

    def transition(
        self,
        *,
        nonce: str,
        to_state: str,
        statement_sha256: str,
        evidence: Mapping[str, Any] | None = None,
    ) -> LedgerRecord:
        path = self._path(nonce)
        clean = _validate_evidence(evidence)
        fd = self._acquire()
        try:
            records = self._read(nonce)
            if not records:
                raise LedgerError("nonce_unknown", nonce)
            last = records[-1]
            if to_state not in ALLOWED_TRANSITIONS.get(last.to_state, ()):
                raise LedgerError("ledger_transition_invalid", f"{last.to_state}->{to_state}")
            if statement_sha256 != last.statement_sha256:
                raise LedgerError("ledger_statement_mismatch", nonce)
            record = {
                "schema": LEDGER_SCHEMA,
                "seq": last.seq + 1,
                "nonce": nonce,
                "from": last.to_state,
                "to": to_state,
                "ts_utc": self._now(),
                "statement_sha256": last.statement_sha256,
                "pull_request": last.pull_request,
                "head_sha": last.head_sha,
                "base_sha": last.base_sha,
                "batch_id": last.batch_id,
                "evidence": clean,
            }
            self._append(path, record, create=False)
        finally:
            self._release(fd)
        return self._read(nonce)[-1]


__all__ = [
    "ALLOWED_KEY_TYPES",
    "ALLOWED_SIGNERS_PATH",
    "ALLOWED_TRANSITIONS",
    "FIELD_ORDER",
    "IN_FLIGHT_STATES",
    "LedgerError",
    "LedgerRecord",
    "NAMESPACE",
    "NONCE_STATES",
    "NonceLedger",
    "PRINCIPAL",
    "PURPOSE",
    "REPOSITORY",
    "RunResult",
    "Statement",
    "StatementError",
    "TERMINAL_STATES",
    "TrustAnchor",
    "VerificationResult",
    "VerifiedStatement",
    "build_statement",
    "canonical_statement_bytes",
    "check_statement_binding",
    "check_statement_expiry",
    "key_fingerprint",
    "load_trust_anchor",
    "new_nonce",
    "parse_allowed_signers",
    "parse_expiry",
    "parse_statement",
    "statement_sha256",
    "validate_repo_path",
    "validate_statement",
    "verify_statement",
    "verify_statement_signature",
]
