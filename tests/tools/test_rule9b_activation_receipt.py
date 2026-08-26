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
import hashlib
import json
import os
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
    verify = _require("verify_receipt_file")
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(receipt), encoding="utf-8")
    return verify(
        path,
        expected_activation_head=kwargs.pop("expected_activation_head", HEAD),
        now_utc=kwargs.pop("now_utc", NOW),
        **kwargs,
    )


# --------------------------------------------------------------------------
# The happy path exists so every refusal below is a real discrimination
# --------------------------------------------------------------------------
def test_a_correct_receipt_verifies(tmp_path: Path) -> None:
    report = _verify(_sealed(), tmp_path)
    assert report["verified"] is True, report["blockers"]
    assert report["blockers"] == []


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


# --------------------------------------------------------------------------
# Sealed exact-head runtime manifest
# --------------------------------------------------------------------------
def _git_blob_sha1(payload: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(payload)}\0".encode("ascii") + payload,
        usedforsecurity=False,
    ).hexdigest()


def _runtime_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    ns = _module()
    required = ns.get("REQUIRED_RUNTIME_PATHS")
    encoder = ns.get("canonical_runtime_manifest_bytes")
    if not isinstance(required, tuple) or not callable(encoder):
        pytest.fail("sealed runtime manifest contract is not implemented")

    root = tmp_path / "generation"
    entries: list[dict[str, Any]] = []
    for index, relative in enumerate(required):
        payload = f"sealed:{index}:{relative}\n".encode("utf-8")
        target = root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        entries.append(
            {
                "path": relative,
                "git_blob_sha1": _git_blob_sha1(payload),
                "byte_length": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    entries.sort(key=lambda item: item["path"].encode("utf-8"))
    manifest = {
        "schema": "wd.rule9b.runtime_manifest.v1",
        "activation_head": HEAD,
        "activation_tree_sha": TREE,
        "runtime_generation_id": "gen-20260826T120000Z-0001",
        "files": entries,
    }
    manifest_path = tmp_path / "runtime-manifest.json"
    manifest_path.write_bytes(encoder(manifest))
    return root, manifest_path, manifest


def _verify_runtime(
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    **overrides: Any,
) -> dict[str, Any]:
    verify = _require("verify_runtime_manifest")
    raw = manifest_path.read_bytes()
    kwargs = {
        "expected_activation_head": HEAD,
        "expected_activation_tree_sha": TREE,
        "expected_generation_id": manifest["runtime_generation_id"],
        "expected_manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "expected_file_count": len(manifest["files"]),
    }
    kwargs.update(overrides)
    return verify(manifest_path, root, **kwargs)


def test_runtime_manifest_happy_path_rehashes_every_git_blob(tmp_path: Path) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    report = _verify_runtime(root, path, manifest)
    assert report["verified"] is True, report["blockers"]
    assert report["blockers"] == []


def test_runtime_manifest_is_canonical_bom_free_utf8_plus_one_lf(
    tmp_path: Path,
) -> None:
    _, path, manifest = _runtime_fixture(tmp_path)
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw == _require("canonical_runtime_manifest_bytes")(manifest)


@pytest.mark.parametrize(
    "relative",
    [
        "../escape.py",
        "/absolute.py",
        "C:/drive.py",
        "dir\\backslash.py",
        "con.txt",
        "dir/trailing. ",
        "dir//empty.py",
    ],
)
def test_runtime_manifest_refuses_windows_escape_and_alias_paths(
    relative: str,
) -> None:
    validate = _require("validate_runtime_manifest_schema")
    entry = {
        "path": relative,
        "git_blob_sha1": "1" * 40,
        "byte_length": 1,
        "sha256": "2" * 64,
    }
    manifest = {
        "schema": "wd.rule9b.runtime_manifest.v1",
        "activation_head": HEAD,
        "activation_tree_sha": TREE,
        "runtime_generation_id": "generation-1",
        "files": [entry],
    }
    assert validate(manifest), relative


def test_runtime_manifest_refuses_case_insensitive_collision() -> None:
    validate = _require("validate_runtime_manifest_schema")
    files = [
        {
            "path": path,
            "git_blob_sha1": str(index) * 40,
            "byte_length": 1,
            "sha256": str(index) * 64,
        }
        for index, path in ((1, "Gate.py"), (2, "gate.py"))
    ]
    manifest = {
        "schema": "wd.rule9b.runtime_manifest.v1",
        "activation_head": HEAD,
        "activation_tree_sha": TREE,
        "runtime_generation_id": "generation-1",
        "files": files,
    }
    assert any("collision" in item for item in validate(manifest))


def test_runtime_manifest_refuses_non_utf8_sort_order() -> None:
    validate = _require("validate_runtime_manifest_schema")
    files = [
        {
            "path": path,
            "git_blob_sha1": str(index) * 40,
            "byte_length": 1,
            "sha256": str(index) * 64,
        }
        for index, path in ((2, "z.py"), (1, "a.py"))
    ]
    manifest = {
        "schema": "wd.rule9b.runtime_manifest.v1",
        "activation_head": HEAD,
        "activation_tree_sha": TREE,
        "runtime_generation_id": "generation-1",
        "files": files,
    }
    assert any("sorted" in item for item in validate(manifest))


def test_runtime_manifest_refuses_unknown_root_and_entry_fields() -> None:
    validate = _require("validate_runtime_manifest_schema")
    manifest = {
        "schema": "wd.rule9b.runtime_manifest.v1",
        "activation_head": HEAD,
        "activation_tree_sha": TREE,
        "runtime_generation_id": "generation-1",
        "future_authority": True,
        "files": [
            {
                "path": "a.py",
                "git_blob_sha1": "1" * 40,
                "byte_length": 1,
                "sha256": "2" * 64,
                "trust": True,
            }
        ],
    }
    blockers = validate(manifest)
    assert any("unknown runtime manifest field" in item for item in blockers)


def test_runtime_manifest_refuses_noncanonical_bytes(tmp_path: Path) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = _verify_runtime(
        root,
        path,
        manifest,
        expected_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    assert report["verified"] is False
    assert any("not canonical" in item for item in report["blockers"])


def test_runtime_manifest_refuses_duplicate_json_keys(tmp_path: Path) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace('{"activation_head"', '{"schema":"duplicate","activation_head"'), encoding="utf-8")
    report = _verify_runtime(
        root,
        path,
        manifest,
        expected_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    assert report["verified"] is False
    assert any("duplicate" in item for item in report["blockers"])


def test_runtime_manifest_refuses_unmanifested_file(tmp_path: Path) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    (root / "injected.py").write_text("payload", encoding="utf-8")
    report = _verify_runtime(root, path, manifest)
    assert report["verified"] is False
    assert any("unmanifested" in item for item in report["blockers"])


def test_runtime_manifest_refuses_missing_file(tmp_path: Path) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    target = root.joinpath(*manifest["files"][0]["path"].split("/"))
    target.unlink()
    report = _verify_runtime(root, path, manifest)
    assert report["verified"] is False
    assert any("missing manifested" in item for item in report["blockers"])


def test_runtime_manifest_refuses_changed_bytes_even_at_same_length(
    tmp_path: Path,
) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    target = root.joinpath(*manifest["files"][0]["path"].split("/"))
    original = target.read_bytes()
    target.write_bytes(b"X" + original[1:])
    report = _verify_runtime(root, path, manifest)
    assert report["verified"] is False
    assert any("SHA-256 mismatch" in item for item in report["blockers"])
    assert any("Git blob mismatch" in item for item in report["blockers"])


@pytest.mark.parametrize(
    "overrides,needle",
    [
        ({"expected_activation_head": "f" * 40}, "activation_head"),
        ({"expected_activation_tree_sha": "e" * 40}, "activation_tree_sha"),
        ({"expected_generation_id": "other"}, "generation id"),
        ({"expected_manifest_sha256": "d" * 64}, "SHA-256"),
        ({"expected_file_count": 1}, "file count"),
    ],
)
def test_runtime_manifest_refuses_receipt_binding_mismatch(
    tmp_path: Path, overrides: dict[str, Any], needle: str
) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    report = _verify_runtime(root, path, manifest, **overrides)
    assert report["verified"] is False
    assert any(needle in item for item in report["blockers"])


def test_runtime_manifest_refuses_missing_required_authority_blob(
    tmp_path: Path,
) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    removed = manifest["files"].pop()
    root.joinpath(*removed["path"].split("/")).unlink()
    path.write_bytes(_require("canonical_runtime_manifest_bytes")(manifest))
    report = _verify_runtime(
        root,
        path,
        manifest,
        expected_manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected_file_count=len(manifest["files"]),
    )
    assert report["verified"] is False
    assert any("required authority path" in item for item in report["blockers"])


def test_runtime_manifest_refuses_hardlinked_file(tmp_path: Path) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    target = root.joinpath(*manifest["files"][0]["path"].split("/"))
    alias = tmp_path / "outside-hardlink"
    try:
        os.link(target, alias)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable on this filesystem: {exc}")
    report = _verify_runtime(root, path, manifest)
    assert report["verified"] is False
    assert any("hard-link" in item for item in report["blockers"])


def test_runtime_manifest_file_itself_cannot_be_hardlinked(tmp_path: Path) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    alias = tmp_path / "runtime-manifest-alias.json"
    try:
        os.link(path, alias)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable on this filesystem: {exc}")
    report = _verify_runtime(root, path, manifest)
    assert report["verified"] is False
    assert any("hard-link" in item for item in report["blockers"])


@pytest.mark.skipif(os.name != "nt", reason="alternate streams are an NTFS contract")
def test_runtime_manifest_refuses_alternate_data_stream(tmp_path: Path) -> None:
    root, path, manifest = _runtime_fixture(tmp_path)
    target = root.joinpath(*manifest["files"][0]["path"].split("/"))
    stream_path = Path(str(target) + ":unsealed")
    try:
        stream_path.write_bytes(b"not covered by the manifest")
    except OSError as exc:
        pytest.skip(f"alternate streams unavailable on this filesystem: {exc}")
    report = _verify_runtime(root, path, manifest)
    assert report["verified"] is False
    assert any("alternate data stream" in item for item in report["blockers"])


def _activation_bundle_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, dict[str, Any]]:
    root, manifest_path, manifest = _runtime_fixture(tmp_path)
    receipt = _sealed(
        activation_tree_sha=TREE,
        runtime_generation_id=manifest["runtime_generation_id"],
        runtime_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        runtime_file_count=len(manifest["files"]),
    )
    receipt_path = tmp_path / "activation-receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path, manifest_path, root, receipt


def test_activation_bundle_opens_receipt_manifest_and_every_runtime_file(
    tmp_path: Path,
) -> None:
    receipt_path, manifest_path, root, _ = _activation_bundle_fixture(tmp_path)
    verify = _require("verify_activation_bundle")
    report = verify(
        receipt_path,
        manifest_path,
        root,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert report["verified"] is True, report["blockers"]
    assert report["receipt_gate"]["verified"] is True
    assert report["runtime_gate"]["verified"] is True


def test_activation_bundle_does_not_accept_receipt_self_claim_after_file_tamper(
    tmp_path: Path,
) -> None:
    receipt_path, manifest_path, root, _ = _activation_bundle_fixture(tmp_path)
    target = next(path for path in root.rglob("*") if path.is_file())
    target.write_bytes(target.read_bytes() + b"tampered")
    report = _require("verify_activation_bundle")(
        receipt_path,
        manifest_path,
        root,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert report["verified"] is False
    assert report["receipt_gate"]["verified"] is True
    assert report["runtime_gate"]["verified"] is False


def test_activation_bundle_stops_before_runtime_when_receipt_is_invalid(
    tmp_path: Path,
) -> None:
    receipt_path, manifest_path, root, receipt = _activation_bundle_fixture(tmp_path)
    receipt["runtime_file_count"] += 1
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    report = _require("verify_activation_bundle")(
        receipt_path,
        manifest_path,
        root,
        expected_activation_head=HEAD,
        now_utc=NOW,
    )
    assert report["verified"] is False
    assert report["receipt_gate"]["verified"] is False
    assert report["runtime_gate"]["verified"] is False
    assert any("not attempted" in item for item in report["runtime_gate"]["blockers"])


def test_activation_bundle_verifier_has_no_bypass_parameter() -> None:
    import inspect

    verify = _require("verify_activation_bundle")
    params = set(inspect.signature(verify).parameters)
    forbidden = {"skip", "skip_validation", "trust", "assume_valid", "force", "unsafe"}
    assert not (params & forbidden), f"bypass parameter present: {params & forbidden}"
