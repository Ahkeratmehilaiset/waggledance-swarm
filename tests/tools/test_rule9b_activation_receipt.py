"""Closed receipt schema and independent receipt verification for Rule 9b.

The activation receipt is the artifact a later tick consults to decide whether
standing signature authority is live. Everything downstream trusts it, so two
properties have to hold and neither is the default:

1. THE SCHEMA IS CLOSED. An unknown field is refused rather than ignored. An
   open schema lets an attacker who can write the authority root add fields a
   future reader might honour, and it lets a field silently disappear during a
   refactor without any check noticing.
2. THE RECEIPT IS VERIFIED, NOT ANNOUNCED. The confirm digest is recomputed
   over the receipt's own canonical bytes and compared. A receipt that merely
   *claims* to be verified is worth nothing: that is precisely the defect being
   closed in the admission path, where a caller-supplied
   ``receipt_verified: true`` in a caller-supplied status file was accepted as
   authority without the artifact ever being opened.

Scope note, deliberate: this module covers the receipt schema and its own
verification. It does NOT cover the ACL/MIC protection of the authority root,
the task XML binding, the sealed runtime manifest, the nonce, or the rollback
path. Those land in later slices and each needs its own review; nothing here
may be cited as covering them.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "ops" / "windows" / "reboot" / "check_rule9b_activation_receipt.py"

NOW = dt.datetime(2026, 8, 26, 12, 0, 0, tzinfo=dt.timezone.utc)
APPLIED_AT = NOW - dt.timedelta(minutes=5)
EXPIRY = NOW + dt.timedelta(days=7)

HEAD = "f3a776758efcaee7a0876d7aa4c0abbf25aea487"
BASE = "edb60343dac5a04988cca0f4a5e2b6765bcd1769"
TREE = "9" * 40
BLOB_A = "da5eb9ebf1215ba89e92e547253a14030043488a"
BLOB_B = "eb89033ef05a8c0ecaea3c8f87b034bd46e63fcb"
SHA256_A = "a" * 64
SHA256_B = "b" * 64
SHA256_C = "c" * 64


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _module() -> dict[str, Any]:
    """Load the verifier by path, the way the production admission does."""
    if not VERIFIER.is_file():
        pytest.fail(f"not implemented yet: {VERIFIER.relative_to(REPO_ROOT)} is absent")
    namespace: dict[str, Any] = {
        "__name__": "rule9b_verifier_under_test",
        "__file__": str(VERIFIER),
    }
    exec(compile(VERIFIER.read_bytes(), str(VERIFIER), "exec"), namespace)
    return namespace


def _require(name: str) -> Any:
    ns = _module()
    value = ns.get(name)
    if value is None:
        pytest.fail(
            f"not implemented yet: {VERIFIER.relative_to(REPO_ROOT)} must expose {name}"
        )
    return value


def _receipt(**overrides: Any) -> dict[str, Any]:
    """A receipt that is valid in every respect, so each test breaks exactly one thing."""
    receipt = {
        "schema": "wd.rule9b.activation_receipt.v1",
        "activation_pr_number": 1657,
        "activation_head": HEAD,
        "activation_base_sha": BASE,
        "activation_tree_sha": TREE,
        "activation_pr_changed_paths": [
            "ops/windows/reboot/check_rule9b_activation_receipt.py",
            "tools/idle_consensus_auto_merge.py",
        ],
        "runtime_generation_id": "gen-20260826T120000Z-0001",
        "runtime_manifest_sha256": SHA256_A,
        "runtime_file_count": 3783,
        "driver_sha256": SHA256_B,
        "verifier_sha256": SHA256_C,
        "previous_driver_sha256": "",
        "cause_b_blob_ids": [BLOB_A, BLOB_B],
        "applied_at_utc": _iso(APPLIED_AT),
        "effective_expiry_utc": _iso(EXPIRY),
        "confirm_digest": "",
    }
    receipt.update(overrides)
    return receipt


def _sealed(**overrides: Any) -> dict[str, Any]:
    """A receipt whose confirm_digest is correct for its own contents."""
    digest_over = _require("canonical_receipt_digest")
    receipt = _receipt(**overrides)
    body = {k: v for k, v in receipt.items() if k != "confirm_digest"}
    receipt["confirm_digest"] = digest_over(body)
    return receipt


def _verify(receipt: Any, tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    canonical = _require("canonical_json_bytes")
    verify = _require("verify_receipt_file")
    path = tmp_path / "receipt.json"
    path.write_bytes(canonical(receipt) + b"\n")
    return verify(
        path,
        expected_activation_head=kwargs.pop("expected_activation_head", HEAD),
        now_utc=kwargs.pop("now_utc", NOW),
        **kwargs,
    )


def _canonical_file(receipt: Any) -> bytes:
    return _require("canonical_json_bytes")(receipt) + b"\n"


def _verify_bytes(raw: bytes, tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "receipt.json"
    path.write_bytes(raw)
    return _require("verify_receipt_file")(
        path,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )


# --------------------------------------------------------------------------
# The happy path exists so every refusal below is a real discrimination
# --------------------------------------------------------------------------
def test_a_correct_receipt_verifies(tmp_path: Path) -> None:
    report = _verify(_sealed(), tmp_path)
    assert report["verified"] is True, report["blockers"]
    assert report["blockers"] == []


def test_confirm_digest_covered_content_is_frozen() -> None:
    assert _sealed()["confirm_digest"] == (
        "d6172b79eab5111e661fe68891c0a2b92485e4a9a10144c3d1dcf96388bb4d1a"
    )


# --------------------------------------------------------------------------
# Closed schema
# --------------------------------------------------------------------------
def test_unknown_field_is_refused(tmp_path: Path) -> None:
    """An open schema lets whoever can write the receipt add fields a future
    reader might honour. Refusing is the only version that stays safe as the
    reader changes."""
    report = _verify(_sealed(operator_override=True), tmp_path)
    assert report["verified"] is False
    assert any("unknown" in b for b in report["blockers"]), report["blockers"]


@pytest.mark.parametrize(
    "missing",
    [
        "schema",
        "activation_head",
        "activation_base_sha",
        "activation_tree_sha",
        "runtime_manifest_sha256",
        "driver_sha256",
        "verifier_sha256",
        "previous_driver_sha256",
        "applied_at_utc",
        "effective_expiry_utc",
        "confirm_digest",
    ],
)
def test_missing_required_field_is_refused(missing: str, tmp_path: Path) -> None:
    receipt = _sealed()
    receipt.pop(missing)
    report = _verify(receipt, tmp_path)
    assert report["verified"] is False
    assert any(missing in b for b in report["blockers"]), report["blockers"]


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("activation_pr_number", "1657"),
        ("activation_head", HEAD[:-1]),
        ("activation_head", HEAD.upper()),
        ("activation_base_sha", "not-a-sha"),
        ("runtime_manifest_sha256", SHA256_A[:-1]),
        ("runtime_file_count", "3783"),
        ("activation_pr_changed_paths", "a/b.py"),
        ("activation_pr_changed_paths", ["../escape.py"]),
        ("activation_pr_changed_paths", ["C:/absolute.py"]),
        ("activation_pr_changed_paths", ["back\\slash.py"]),
        ("cause_b_blob_ids", [BLOB_A]),
        ("applied_at_utc", "2026-08-26 12:00:00"),
        ("effective_expiry_utc", ""),
        ("schema", "wd.rule9b.activation_receipt.v2"),
    ],
)
def test_malformed_field_is_refused(field: str, bad_value: Any, tmp_path: Path) -> None:
    """Format pins are part of the schema: a 40-hex head that is 39 characters,
    or uppercase, or a changed path that escapes the tree, are all inputs a
    later consumer would treat as authoritative."""
    report = _verify(_sealed(**{field: bad_value}), tmp_path)
    assert report["verified"] is False
    assert any(field in b for b in report["blockers"]), report["blockers"]


def test_previous_driver_sha256_may_be_empty_only_on_first_activation(tmp_path: Path) -> None:
    """The one legitimately empty value in the schema. It must be allowed
    without opening the field to arbitrary strings."""
    assert _verify(_sealed(previous_driver_sha256=""), tmp_path)["verified"] is True
    assert _verify(_sealed(previous_driver_sha256=SHA256_A), tmp_path)["verified"] is True
    report = _verify(_sealed(previous_driver_sha256="none"), tmp_path)
    assert report["verified"] is False


# --------------------------------------------------------------------------
# The digest is recomputed, never accepted
# --------------------------------------------------------------------------
def test_tampered_field_breaks_the_digest(tmp_path: Path) -> None:
    """The whole point: edit any bound value and the receipt stops verifying,
    without the editor having to be detected by a field-specific rule."""
    receipt = _sealed()
    receipt["runtime_file_count"] = 3784
    report = _verify(receipt, tmp_path)
    assert report["verified"] is False
    assert any("confirm_digest" in b for b in report["blockers"]), report["blockers"]


def test_a_self_consistent_but_wrong_head_receipt_is_refused(tmp_path: Path) -> None:
    """A receipt resealed around a different head is internally perfect. It is
    caught by binding to the operator-signed activation generation, not by the
    digest or by a later candidate PR head."""
    other = "0" * 40
    report = _verify(
        _sealed(activation_head=other),
        tmp_path,
        expected_activation_head=HEAD,
    )
    assert report["verified"] is False
    assert any("activation_head" in b for b in report["blockers"]), report["blockers"]


def test_confirm_digest_must_not_cover_itself(tmp_path: Path) -> None:
    """Sanity on the sealing rule: the digest is taken over the body without
    the digest field, so a receipt sealed the other way must not verify."""
    digest_over = _require("canonical_receipt_digest")
    receipt = _receipt()
    receipt["confirm_digest"] = digest_over(receipt)
    report = _verify(receipt, tmp_path)
    assert report["verified"] is False


# --------------------------------------------------------------------------
# Time bounds
# --------------------------------------------------------------------------
def test_expired_receipt_is_refused(tmp_path: Path) -> None:
    report = _verify(
        _sealed(effective_expiry_utc=_iso(NOW - dt.timedelta(minutes=1))), tmp_path
    )
    assert report["verified"] is False
    assert any("expir" in b for b in report["blockers"]), report["blockers"]


def test_applied_at_in_the_future_is_refused(tmp_path: Path) -> None:
    """A receipt applied in the future is a clock problem or a forged window;
    either way it is not evidence that an activation has happened."""
    report = _verify(
        _sealed(applied_at_utc=_iso(NOW + dt.timedelta(hours=3))), tmp_path
    )
    assert report["verified"] is False
    assert any("future" in b for b in report["blockers"]), report["blockers"]


def test_non_utc_offset_is_refused_even_when_timestamp_is_aware(tmp_path: Path) -> None:
    report = _verify(
        _sealed(applied_at_utc="2026-08-26T14:55:00+03:00"),
        tmp_path,
    )
    assert report["verified"] is False
    assert any("applied_at_utc" in b for b in report["blockers"])


def test_naive_verification_clock_is_refused(tmp_path: Path) -> None:
    report = _verify(
        _sealed(),
        tmp_path,
        now_utc=NOW.replace(tzinfo=None),
    )
    assert report["verified"] is False
    assert any("clock" in b for b in report["blockers"])


def test_expiry_must_be_after_applied_at(tmp_path: Path) -> None:
    report = _verify(
        _sealed(
            applied_at_utc=_iso(APPLIED_AT),
            effective_expiry_utc=_iso(APPLIED_AT - dt.timedelta(minutes=1)),
        ),
        tmp_path,
    )
    assert report["verified"] is False


def test_window_wider_than_the_cap_is_refused(tmp_path: Path) -> None:
    """A frozen classifier must not outlive the policy it froze, so the window
    has a hard ceiling regardless of what the receipt asks for."""
    report = _verify(
        _sealed(effective_expiry_utc=_iso(APPLIED_AT + dt.timedelta(days=400))),
        tmp_path,
    )
    assert report["verified"] is False


# --------------------------------------------------------------------------
# There is no way to not verify
# --------------------------------------------------------------------------
def test_absent_receipt_file_is_refused(tmp_path: Path) -> None:
    verify = _require("verify_receipt_file")
    report = verify(
        tmp_path / "nope.json",
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert report["verified"] is False
    assert report["blockers"], "an absent receipt must produce a named blocker"


def test_unparseable_receipt_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_bytes(b"{not json")
    verify = _require("verify_receipt_file")
    report = verify(path, expected_activation_head=HEAD, now_utc=NOW)
    assert report["verified"] is False


def test_receipt_with_a_bom_is_refused(tmp_path: Path) -> None:
    """Strict UTF-8, never utf-8-sig: BOM-tolerant reading would accept bytes
    that differ from the signed canonical payload."""
    path = tmp_path / "receipt.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(_sealed()).encode("utf-8"))
    verify = _require("verify_receipt_file")
    report = verify(path, expected_activation_head=HEAD, now_utc=NOW)
    assert report["verified"] is False


@pytest.mark.parametrize("escaped", [False, True], ids=["literal", "escaped-equivalent"])
def test_duplicate_json_key_is_refused(tmp_path: Path, escaped: bool) -> None:
    raw = _canonical_file(_sealed())
    literal = f'"activation_head":"{HEAD}"'
    duplicate_key = "activation\\u005fhead" if escaped else "activation_head"
    duplicate = f'{literal},"{duplicate_key}":"{HEAD}"'
    poisoned = raw.decode("utf-8").replace(literal, duplicate, 1).encode("utf-8")
    report = _verify_bytes(poisoned, tmp_path)
    assert report["verified"] is False
    assert any("JSON" in blocker for blocker in report["blockers"])


@pytest.mark.parametrize(
    "variant",
    [
        "default-spaces",
        "reversed-keys",
        "missing-lf",
        "crlf",
        "two-lfs",
        "trailing-space",
    ],
)
def test_noncanonical_receipt_bytes_are_refused(
    tmp_path: Path,
    variant: str,
) -> None:
    receipt = _sealed()
    canonical = _canonical_file(receipt)
    if variant == "default-spaces":
        raw = json.dumps(receipt, ensure_ascii=False).encode("utf-8") + b"\n"
    elif variant == "reversed-keys":
        reversed_receipt = dict(reversed(tuple(receipt.items())))
        raw = json.dumps(
            reversed_receipt,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    elif variant == "missing-lf":
        raw = canonical[:-1]
    elif variant == "crlf":
        raw = canonical[:-1] + b"\r\n"
    elif variant == "two-lfs":
        raw = canonical + b"\n"
    else:
        raw = canonical[:-1] + b" \n"
    report = _verify_bytes(raw, tmp_path)
    assert report["verified"] is False
    assert any("canonical JSON" in blocker for blocker in report["blockers"])


def test_oversized_receipt_is_refused_before_parse(tmp_path: Path) -> None:
    limit = _require("MAX_RECEIPT_BYTES")
    report = _verify_bytes(b"{" + b" " * limit + b"}", tmp_path)
    assert report["verified"] is False
    assert any("byte limit" in blocker for blocker in report["blockers"])


def test_hardlinked_receipt_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_bytes(_canonical_file(_sealed()))
    alias = tmp_path / "receipt-alias.json"
    os.link(path, alias)
    report = _require("verify_receipt_file")(
        path,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert report["verified"] is False
    assert any("hard-link" in blocker for blocker in report["blockers"])


def test_symlinked_receipt_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(_canonical_file(_sealed()))
    link = tmp_path / "receipt.json"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    report = _require("verify_receipt_file")(
        link,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert report["verified"] is False
    assert any(
        "symbolic link" in blocker or "reparse point" in blocker
        for blocker in report["blockers"]
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX sparse-file contract")
def test_posix_sparse_receipt_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    with path.open("wb") as handle:
        handle.seek(512 * 1024)
        handle.write(b"x")
    stat_result = path.stat()
    if getattr(stat_result, "st_blocks", 0) * 512 >= stat_result.st_size:
        pytest.skip("filesystem did not create a sparse file")
    report = _require("verify_receipt_file")(
        path,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert report["verified"] is False
    assert any("sparse" in blocker for blocker in report["blockers"])


@pytest.mark.skipif(os.name != "nt", reason="Windows sparse-file contract")
def test_windows_sparse_receipt_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_bytes(_canonical_file(_sealed()))
    completed = subprocess.run(
        ["fsutil", "sparse", "setflag", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip(f"cannot mark sparse: {completed.stderr.strip()}")
    report = _require("verify_receipt_file")(
        path,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert report["verified"] is False
    assert any("sparse" in blocker for blocker in report["blockers"])


@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate-stream contract")
def test_named_and_direct_alternate_stream_receipts_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_bytes(_canonical_file(_sealed()))
    stream = Path(str(path) + ":poison")
    try:
        stream.write_bytes(_canonical_file(_sealed()))
    except OSError as exc:
        pytest.skip(f"alternate streams unavailable: {exc}")
    verify = _require("verify_receipt_file")
    for candidate in (path, stream):
        report = verify(candidate, expected_activation_head=HEAD, now_utc=NOW)
        assert report["verified"] is False
        assert any("stream" in blocker for blocker in report["blockers"])


def test_snapshot_uses_one_read_and_one_json_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _module()
    path = tmp_path / "receipt.json"
    path.write_bytes(namespace["canonical_json_bytes"](_sealed()) + b"\n")
    real_read = namespace["os"].read
    real_loads = namespace["json"].loads
    calls = {"read": 0, "loads": 0}

    def counted_read(fd: int, amount: int) -> bytes:
        calls["read"] += 1
        return real_read(fd, amount)

    def counted_loads(*args: Any, **kwargs: Any) -> Any:
        calls["loads"] += 1
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(namespace["os"], "read", counted_read)
    monkeypatch.setattr(namespace["json"], "loads", counted_loads)
    report = namespace["verify_receipt_file"](
        path,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert report["verified"] is True, report["blockers"]
    assert calls == {"read": 1, "loads": 1}


def test_path_replacement_during_snapshot_is_denied_or_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    namespace = _module()
    path = tmp_path / "receipt.json"
    replacement = tmp_path / "replacement.json"
    payload = namespace["canonical_json_bytes"](_sealed()) + b"\n"
    path.write_bytes(payload)
    replacement.write_bytes(payload)
    real_read = namespace["os"].read
    replacement_denied = False

    def replacing_read(fd: int, amount: int) -> bytes:
        nonlocal replacement_denied
        raw = real_read(fd, amount)
        try:
            os.replace(replacement, path)
        except PermissionError:
            replacement_denied = True
        return raw

    monkeypatch.setattr(namespace["os"], "read", replacing_read)
    report = namespace["verify_receipt_file"](
        path,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    if os.name == "nt":
        assert replacement_denied is True
        assert report["verified"] is True, report["blockers"]
    else:
        assert replacement_denied is False
        assert report["verified"] is False
        assert any(
            "identity" in blocker or "metadata" in blocker
            for blocker in report["blockers"]
        )


def test_verification_has_no_bypass_parameter() -> None:
    """A skip-validation flag is how a fail-closed check becomes optional."""
    import inspect

    verify = _require("verify_receipt_file")
    params = set(inspect.signature(verify).parameters)
    forbidden = {"skip", "skip_validation", "trust", "assume_valid", "force", "unsafe"}
    assert not (params & forbidden), f"bypass parameter present: {params & forbidden}"


def test_report_shape_is_stable() -> None:
    """The admission path branches on these two keys, so their types are part
    of the contract rather than an implementation detail."""
    verify = _require("verify_receipt_file")
    report = verify(
        Path("definitely-absent.json"),
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert isinstance(report, dict)
    assert isinstance(report.get("verified"), bool)
    assert isinstance(report.get("blockers"), list)
    assert all(isinstance(b, str) for b in report["blockers"])
