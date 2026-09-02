# SPDX-License-Identifier: Apache-2.0
"""Hermetic fail-closed tests for the D1 preparation-only inspector.

Every value in this module is synthetic. The tests create disposable Git
repositories and never require git-filter-repo or mutate a remote repository.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

import tools.d1_pii_scrub as d1
from tools.d1_pii_scrub import (
    InspectionError,
    InventoryError,
    build_preparation_report,
    inspect_repository_snapshot,
    load_expected_refs,
    load_sensitive_inventory,
    main,
    validate_redacted_settings,
)


SYNTHETIC_VALUES = {
    "business_name": ["Fixture Orchard Alpha LLC", "Fixture Orchard Legacy LLC"],
    "owner": ["Fixture Person Alpha", "Fixture Person Legacy"],
    "y_tunnus": ["0000000-0", "1111111-1"],
}
PLACEHOLDERS = {
    "business_name": "REDACTED_BUSINESS",
    "owner": "REDACTED_OWNER",
    "y_tunnus": "REDACTED_BUSINESS_ID",
}
AUTHORITY_DENIED = {
    "scope": False,
    "legal": False,
    "release": False,
    "production": False,
    "execution": False,
}


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, capture_output=True, text=True,
    )


def _git_bytes(cwd: Path, *args: str, input_bytes: bytes) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def _inventory_payload(
    fields: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "waggledance.d1_sensitive_inventory",
        "fields": SYNTHETIC_VALUES if fields is None else fields,
    }


def _expected_refs_payload(refs: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "waggledance.d1_expected_refs",
        "refs": refs,
    }


def _settings_text(values: dict[str, object] | None = None) -> str:
    selected: dict[str, object] = dict(PLACEHOLDERS if values is None else values)
    return (
        "profile: synthetic\n"
        "facts:\n"
        f"  business_name: {selected['business_name']}\n"
        f"  owner: {selected['owner']}\n"
        f"  y_tunnus: {selected['y_tunnus']}\n"
        "hivemind:\n"
        "  heartbeat_interval: 30\n"
    )


def _make_source_repo(
    tmp_path: Path,
    *,
    main_payload: bytes = b"public fixture payload\n",
    hidden_sensitive_ref: bool = False,
) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "fixture@example.invalid")
    _git(source, "config", "user.name", "Fixture Runner")
    (source / "payload.bin").write_bytes(main_payload)
    for relative in d1.SETTINGS_PATHS:
        settings = source / relative
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(_settings_text(), encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "synthetic main")
    _git(source, "tag", "fixture-v1")
    if hidden_sensitive_ref:
        _git(source, "switch", "-c", "archive/synthetic-sensitive")
        (source / "historical.txt").write_text(
            "\n".join(value for values in SYNTHETIC_VALUES.values() for value in values),
            encoding="utf-8",
        )
        _git(source, "add", "historical.txt")
        _git(source, "commit", "-m", "synthetic historical authority")
        _git(source, "switch", "main")
    return source


def _make_mirror(tmp_path: Path, source: Path) -> Path:
    mirror = tmp_path / "mirror.git"
    _git(
        tmp_path,
        "clone",
        "--mirror",
        "--no-hardlinks",
        str(source),
        str(mirror),
    )
    return mirror


def _mirror_refs(mirror: Path) -> dict[str, str]:
    completed = _git(
        mirror, "for-each-ref", "--format=%(refname) %(objectname)", "refs",
    )
    refs: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        name, oid = line.split(" ", 1)
        refs[name] = oid
    return refs


def _authority_files(
    tmp_path: Path,
    source: Path,
    mirror: Path,
    *,
    inventory_payload: dict[str, object] | None = None,
    refs: dict[str, str] | None = None,
) -> tuple[Path, Path, Any, Any]:
    authority = tmp_path / "operator-authority"
    inventory_path = _write_json(
        authority / "inventory.json",
        _inventory_payload() if inventory_payload is None else inventory_payload,
    )
    expected_path = _write_json(
        authority / "expected-refs.json",
        _expected_refs_payload(_mirror_refs(mirror) if refs is None else refs),
    )
    inventory = load_sensitive_inventory(inventory_path, source, mirror)
    expected_refs = load_expected_refs(expected_path)
    return inventory_path, expected_path, inventory, expected_refs


def _assert_no_sensitive_values(value: object) -> None:
    serialized = json.dumps(value, sort_keys=True)
    for values in SYNTHETIC_VALUES.values():
        for sensitive_value in values:
            assert sensitive_value not in serialized


def _assert_blocked_report(report: dict[str, Any]) -> None:
    assert report["schema_version"] == 1
    assert report["kind"] == "waggledance.d1_pii_scrub_preparation"
    assert report["status"] == "prepared_blocked"
    assert report["inspection_status"] in {"complete", "incomplete"}
    assert report["blocked_scope"] is True
    assert report["authority"] == AUTHORITY_DENIED
    _assert_no_sensitive_values(report)


def _assert_blocked_snapshot(snapshot: dict[str, Any]) -> None:
    assert snapshot["observation"] == "snapshot_inspected"
    assert snapshot["ref_count"] > 0
    assert snapshot["stored_object_count"] > 0
    assert snapshot["commit_count"] > 0
    assert snapshot["unique_blob_count"] > 0
    assert snapshot["tree_entry_count"] > 0
    assert snapshot["scanned_blob_bytes"] > 0
    assert set(snapshot["categories"]) == {
        "settings", "legal_keep", "unexpected_scope", "path", "ref", "metadata",
    }
    assert set(snapshot["metadata_categories"]) == {
        "author", "committer", "tagger", "message", "other",
    }
    _assert_no_sensitive_values(snapshot)


def _assert_inventory_error(
    tmp_path: Path,
    raw: bytes,
    *,
    inside: str | None = None,
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    parent = {
        None: tmp_path / "operator-authority",
        "repo": source,
        "mirror": mirror,
        "worktree": tmp_path / "other-worktree",
    }[inside]
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / "inventory.json"
    path.write_bytes(raw)
    worktrees = (parent,) if inside == "worktree" else ()
    with pytest.raises(InventoryError):
        load_sensitive_inventory(path, source, mirror, worktrees)


def test_sensitive_inventory_accepts_exact_schema_outside_all_worktrees(
    tmp_path: Path,
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    path = _write_json(
        tmp_path / "operator-authority" / "inventory.json", _inventory_payload(),
    )
    inventory = load_sensitive_inventory(path, source, mirror)
    assert inventory.fields == {
        field: tuple(value.encode("utf-8") for value in values)
        for field, values in SYNTHETIC_VALUES.items()
    }
    assert inventory.variant_count == 6
    assert inventory.needles == tuple(
        value.encode("utf-8")
        for values in SYNTHETIC_VALUES.values()
        for value in values
    )


def test_sensitive_inventory_accepts_exact_count_and_byte_boundaries(
    tmp_path: Path,
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    fields = {
        "business_name": ["x" * d1.MAX_VARIANT_BYTES],
        "owner": [f"fixture-owner-{index}" for index in range(d1.MAX_VARIANTS_PER_FIELD)],
        "y_tunnus": ["0000000-0"],
    }
    path = _write_json(
        tmp_path / "operator-authority" / "inventory.json",
        _inventory_payload(fields),
    )
    inventory = load_sensitive_inventory(path, source, mirror)
    assert len(inventory.fields["business_name"][0]) == d1.MAX_VARIANT_BYTES
    assert len(inventory.fields["owner"]) == d1.MAX_VARIANTS_PER_FIELD


@pytest.mark.parametrize(
    "mutation",
    [
        lambda p: p.update(schema_version=2),
        lambda p: p.update(schema_version=True),
        lambda p: p.update(kind="waggledance.d1_other"),
        lambda p: p.update(kind=1),
        lambda p: p.pop("schema_version"),
        lambda p: p.pop("kind"),
        lambda p: p.pop("fields"),
        lambda p: p.update(extra=True),
        lambda p: p.update(fields=[]),
        lambda p: p["fields"].pop("owner"),
        lambda p: p["fields"].update(extra=["synthetic"]),
        lambda p: p["fields"].update(owner="Fixture Person Alpha"),
        lambda p: p["fields"].update(owner=[]),
        lambda p: p["fields"].update(owner=[1]),
        lambda p: p["fields"].update(owner=[""]),
        lambda p: p["fields"].update(owner=[" Fixture Person Alpha"]),
        lambda p: p["fields"].update(owner=["Fixture Person Alpha "]),
        lambda p: p["fields"].update(owner=["Fixture\nPerson"]),
        lambda p: p["fields"].update(owner=["Fixture\x00Person"]),
        lambda p: p["fields"].update(owner=["Fixture\x7fPerson"]),
        lambda p: p["fields"].update(owner=["Fixture Person Alpha"] * 2),
        lambda p: p["fields"].update(owner=["Fixture Orchard Alpha LLC"]),
        lambda p: p["fields"].update(owner=["REDACTED_OWNER"]),
        lambda p: p["fields"].update(owner=["regex:Fixture Person"]),
        lambda p: p["fields"].update(owner=["GLOB:Fixture Person"]),
        lambda p: p["fields"].update(owner=["literal:Fixture Person"]),
        lambda p: p["fields"].update(owner=["Fixture ==> Person"]),
        lambda p: p["fields"].update(owner=["x" * 1_000_000]),
        lambda p: p["fields"].update(owner=[f"fixture-{i}" for i in range(10_000)]),
    ],
    ids=(
        "schema", "bool-schema", "kind", "kind-type", "missing-schema",
        "missing-kind", "missing-fields", "extra-top-level", "fields-type",
        "missing-field", "extra-field",
        "field-not-list", "empty-list", "non-string", "empty-value",
        "leading-space", "trailing-space", "newline", "nul", "del",
        "same-field-duplicate", "cross-field-duplicate", "redacted-placeholder",
        "regex-grammar", "glob-grammar", "literal-grammar", "mapping-grammar",
        "oversize-value", "too-many-values",
    ),
)
def test_sensitive_inventory_rejects_schema_type_ambiguity_and_limits(
    tmp_path: Path, mutation,
) -> None:
    payload = json.loads(json.dumps(_inventory_payload()))
    mutation(payload)
    _assert_inventory_error(
        tmp_path, json.dumps(payload, separators=(",", ":")).encode("utf-8"),
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"\xff",
        b" " + json.dumps(_inventory_payload()).encode("utf-8"),
        b"\xef\xbb\xbf" + json.dumps(_inventory_payload()).encode("utf-8"),
        json.dumps(_inventory_payload()).encode("utf-8") + b"\n",
        json.dumps(_inventory_payload()).encode("utf-8") + b" ",
        (
            b'{"schema_version":1,"schema_version":1,'
            b'"kind":"waggledance.d1_sensitive_inventory","fields":{'
            b'"business_name":["Fixture Orchard Alpha LLC"],'
            b'"owner":["Fixture Person Alpha"],"y_tunnus":["0000000-0"]}}'
        ),
        (
            b'{"schema_version":1,"kind":"waggledance.d1_sensitive_inventory",'
            b'"fields":{"business_name":["Fixture Orchard Alpha LLC"],'
            b'"owner":["Fixture Person Alpha"],"owner":["Fixture Person Legacy"],'
            b'"y_tunnus":["0000000-0"]}}'
        ),
    ],
    ids=(
        "malformed", "invalid-utf8", "leading-space", "bom", "trailing-newline",
        "trailing-space", "duplicate-top", "duplicate-field",
    ),
)
def test_sensitive_inventory_rejects_noncanonical_or_duplicate_json(
    tmp_path: Path, raw: bytes,
) -> None:
    _assert_inventory_error(tmp_path, raw)


@pytest.mark.parametrize("inside", ["repo", "mirror", "worktree"])
def test_sensitive_inventory_rejects_authority_inside_mutable_scope(
    tmp_path: Path, inside: str,
) -> None:
    raw = json.dumps(_inventory_payload(), separators=(",", ":")).encode("utf-8")
    _assert_inventory_error(tmp_path, raw, inside=inside)


def test_sensitive_inventory_rejects_missing_directory_and_multiple_links(
    tmp_path: Path,
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    with pytest.raises(InventoryError):
        load_sensitive_inventory(tmp_path / "missing.json", source, mirror)
    with pytest.raises(InventoryError):
        load_sensitive_inventory(tmp_path, source, mirror)
    original = _write_json(
        tmp_path / "operator-authority" / "inventory.json", _inventory_payload(),
    )
    alias = original.with_name("inventory-alias.json")
    os.link(original, alias)
    with pytest.raises(InventoryError):
        load_sensitive_inventory(original, source, mirror)


def test_expected_refs_accepts_exact_well_formed_ref_map(tmp_path: Path) -> None:
    refs = {
        "refs/heads/main": "a" * 40,
        "refs/tags/fixture-v1": "b" * 40,
        "refs/security/archive": "c" * 40,
    }
    loaded = load_expected_refs(
        _write_json(tmp_path / "expected.json", _expected_refs_payload(refs)),
    )
    assert loaded.refs == refs


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/main": "a" * 40}},
        {"schema_version": True, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/main": "a" * 40}},
        {"schema_version": 1, "kind": "wrong", "refs": {"refs/heads/main": "a" * 40}},
        {"schema_version": 1, "kind": 1, "refs": {"refs/heads/main": "a" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs"},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {}, "extra": True},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": []},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"HEAD": "a" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/../main": "a" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs//heads/main": "a" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/.main": "a" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/main.": "a" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/main@{1}": "a" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/main name": "a" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/main.lock": "a" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/main": "A" * 40}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/main": "a" * 39}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {"refs/heads/main": 1}},
        {"schema_version": 1, "kind": "waggledance.d1_expected_refs", "refs": {f"refs/heads/r{i}": "a" * 40 for i in range(20_000)}},
    ],
    ids=(
        "schema", "bool-schema", "kind", "kind-type", "missing-refs", "extra-key",
        "refs-type", "empty", "pseudo-ref", "traversal", "double-slash",
        "dot-component", "trailing-dot", "reflog-syntax", "space", "lock-suffix",
        "uppercase-oid", "short-oid", "oid-type", "too-many-refs",
    ),
)
def test_expected_refs_rejects_invalid_schema_refs_and_limits(
    tmp_path: Path, payload: dict[str, object],
) -> None:
    with pytest.raises(InspectionError):
        load_expected_refs(_write_json(tmp_path / "expected.json", payload))


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"\xff",
        b" " + json.dumps(_expected_refs_payload({"refs/heads/main": "a" * 40})).encode(),
        b"\xef\xbb\xbf" + json.dumps(_expected_refs_payload({"refs/heads/main": "a" * 40})).encode(),
        json.dumps(_expected_refs_payload({"refs/heads/main": "a" * 40})).encode() + b"\n",
        json.dumps(_expected_refs_payload({"refs/heads/main": "a" * 40})).encode() + b" ",
        (
            b'{"schema_version":1,"kind":"waggledance.d1_expected_refs",'
            b'"refs":{"refs/heads/main":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            b'"refs/heads/main":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}'
        ),
    ],
    ids=(
        "malformed", "invalid-utf8", "leading-space", "bom", "trailing-newline",
        "trailing-space", "duplicate-ref",
    ),
)
def test_expected_refs_rejects_noncanonical_or_duplicate_json(
    tmp_path: Path, raw: bytes,
) -> None:
    path = tmp_path / "expected.json"
    path.write_bytes(raw)
    with pytest.raises(InspectionError):
        load_expected_refs(path)


def test_expected_refs_rejects_missing_directory_hardlink_and_oversize(
    tmp_path: Path,
) -> None:
    with pytest.raises(InspectionError):
        load_expected_refs(tmp_path / "missing.json")
    with pytest.raises(InspectionError):
        load_expected_refs(tmp_path)
    original = _write_json(
        tmp_path / "expected.json",
        _expected_refs_payload({"refs/heads/main": "a" * 40}),
    )
    os.link(original, tmp_path / "expected-alias.json")
    with pytest.raises(InspectionError):
        load_expected_refs(original)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b"x" * d1.MAX_REF_MANIFEST_BYTES + b"}")
    with pytest.raises(InspectionError):
        load_expected_refs(oversized)


def test_capture_rejects_platform_specific_non_plain_inputs(tmp_path: Path) -> None:
    payload = _expected_refs_payload({"refs/heads/main": "a" * 40})
    if os.name != "nt":
        original = _write_json(tmp_path / "expected.json", payload)
        alias = tmp_path / "expected-link.json"
        alias.symlink_to(original)
        with pytest.raises(InspectionError):
            load_expected_refs(alias)

        fifo = tmp_path / "expected.fifo"
        os.mkfifo(fifo)
        with pytest.raises(InspectionError):
            load_expected_refs(fifo)
        return

    carrier = _write_json(tmp_path / "expected.json", payload)
    alternate_stream = Path(f"{carrier}:synthetic-stream")
    alternate_stream.write_bytes(b"synthetic alternate stream")
    with pytest.raises(InspectionError, match="alternate_data_stream_forbidden"):
        load_expected_refs(alternate_stream)
    with pytest.raises(InspectionError, match="alternate_data_stream_forbidden"):
        load_expected_refs(carrier)

    target = tmp_path / "junction-target"
    target.mkdir()
    _write_json(target / "expected.json", payload)
    junction = tmp_path / "junction-parent"
    created = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    try:
        with pytest.raises(InspectionError, match="unsafe_parent_chain"):
            load_expected_refs(junction / "expected.json")
    finally:
        os.rmdir(junction)


def test_capture_rejects_deterministic_concurrent_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _write_json(
        tmp_path / "expected.json",
        _expected_refs_payload({"refs/heads/main": "a" * 40}),
    )
    if os.name == "nt":
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        monkeypatch.setattr(d1, "_open_read_locked", lambda candidate: os.open(candidate, flags))
    real_read = os.read
    mutated = False

    def mutating_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        chunk = real_read(descriptor, size)
        if chunk and not mutated:
            mutated = True
            with path.open("ab") as stream:
                stream.write(b" ")
        return chunk

    monkeypatch.setattr(d1.os, "read", mutating_read)
    with pytest.raises(InspectionError, match="input_changed_during_read"):
        load_expected_refs(path)
    assert mutated is True


@pytest.mark.parametrize("drift", ["stream-inventory", "change-time"])
def test_windows_capture_rejects_injected_carrier_metadata_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    if os.name != "nt":
        return
    path = _write_json(
        tmp_path / "expected.json",
        _expected_refs_payload({"refs/heads/main": "a" * 40}),
    )
    stream_calls = 0
    token_calls = 0

    def stream_inventory(_descriptor: int) -> tuple[str, ...]:
        nonlocal stream_calls
        stream_calls += 1
        if drift == "stream-inventory" and stream_calls == 2:
            return ("::$data", ":synthetic:$data")
        return ("::$data",)

    def change_token(_descriptor: int) -> tuple[int, int]:
        nonlocal token_calls
        token_calls += 1
        return (token_calls if drift == "change-time" else 1, 32)

    monkeypatch.setattr(d1, "_windows_handle_stream_inventory", stream_inventory)
    monkeypatch.setattr(d1, "_windows_handle_change_token", change_token)
    expected_code = (
        "alternate_data_stream_forbidden"
        if drift == "stream-inventory"
        else "input_changed_during_read"
    )
    with pytest.raises(InspectionError, match=expected_code):
        load_expected_refs(path)
    assert stream_calls == 2
    assert token_calls == 2


def test_validate_redacted_settings_accepts_only_exact_placeholders(tmp_path: Path) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(_settings_text(), encoding="utf-8")
    result = validate_redacted_settings(path)
    assert result == {"field_count": 3}


@pytest.mark.parametrize("field", sorted(PLACEHOLDERS))
@pytest.mark.parametrize("value", ["", "null", "123", "[]", "REDACTED", "synthetic-raw"])
def test_validate_redacted_settings_rejects_missing_typed_or_nonexact_values(
    tmp_path: Path, field: str, value: str,
) -> None:
    values = dict(PLACEHOLDERS)
    values[field] = value
    path = tmp_path / "settings.yaml"
    path.write_text(_settings_text(values), encoding="utf-8")
    with pytest.raises(InspectionError):
        validate_redacted_settings(path)


@pytest.mark.parametrize(
    "text",
    [
        "profile: synthetic\n",
        "facts:\n  business_name: REDACTED_BUSINESS\n  owner: REDACTED_OWNER\n",
        (
            "facts:\n  business_name: REDACTED_BUSINESS\n"
            "  owner: REDACTED_OWNER\n  owner: REDACTED_OWNER\n"
            "  y_tunnus: REDACTED_BUSINESS_ID\n"
        ),
        _settings_text() + "facts:\n  owner: REDACTED_OWNER\n",
        (
            _settings_text()
            + '"facts": {business_name: REDACTED_BUSINESS, owner: REDACTED_OWNER, '
            'y_tunnus: REDACTED_BUSINESS_ID}\n'
        ),
        "facts: [REDACTED_BUSINESS, REDACTED_OWNER, REDACTED_BUSINESS_ID]\n",
        "facts:\n  business_name: [REDACTED_BUSINESS]\n  owner: REDACTED_OWNER\n  y_tunnus: REDACTED_BUSINESS_ID\n",
        "facts:\n\towner: REDACTED_OWNER\n",
    ],
    ids=(
        "missing-facts", "missing-sensitive-key", "duplicate-sensitive-key",
        "duplicate-facts", "quoted-flow-duplicate-facts", "facts-type",
        "sensitive-value-type", "invalid-yaml",
    ),
)
def test_validate_redacted_settings_rejects_ambiguous_or_malformed_yaml(
    tmp_path: Path, text: str,
) -> None:
    path = tmp_path / "settings.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(InspectionError):
        validate_redacted_settings(path)


def test_validate_redacted_settings_rejects_missing_and_hardlink(tmp_path: Path) -> None:
    with pytest.raises(InspectionError):
        validate_redacted_settings(tmp_path / "missing.yaml")
    original = tmp_path / "settings.yaml"
    original.write_text(_settings_text(), encoding="utf-8")
    alias = tmp_path / "settings-alias.yaml"
    os.link(original, alias)
    with pytest.raises(InspectionError):
        validate_redacted_settings(original)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xef\xbb\xbf" + _settings_text().encode("utf-8"),
        b"facts:\n  business_name: \xff\n",
        b"x" * (d1.MAX_SETTINGS_BYTES + 1),
    ],
    ids=("bom", "invalid-utf8", "oversize"),
)
def test_validate_redacted_settings_rejects_unverifiable_capture(
    tmp_path: Path, raw: bytes,
) -> None:
    path = tmp_path / "settings.yaml"
    path.write_bytes(raw)
    with pytest.raises(InspectionError):
        validate_redacted_settings(path)


def test_stream_matchers_preserve_blob_and_metadata_chunk_boundaries() -> None:
    needle = SYNTHETIC_VALUES["owner"][0].encode("utf-8")
    blob_scanner = d1._NeedleScanner((needle,))
    blob_scanner.feed(b"prefix " + needle[:7])
    blob_scanner.feed(needle[7:] + b" suffix")
    assert blob_scanner.count == 1

    metadata_scanner = d1._MetadataStreamScanner((needle,))
    metadata_scanner.feed(b"tree " + b"0" * 40 + b"\nauthor " + needle[:5])
    metadata_scanner.feed(needle[5:] + b"\ncommitter Fixture Runner")
    metadata_scanner.feed(b" <fixture@example.invalid> 0 +0000\n\nmessage\n")
    counts = metadata_scanner.finish()
    assert counts["author"] == 1
    assert counts["message"] == 0


def test_inspect_repository_snapshot_streams_large_cross_chunk_blob(
    tmp_path: Path,
) -> None:
    needle = SYNTHETIC_VALUES["business_name"][0].encode("utf-8")
    split = len(needle) // 2
    payload = b"x" * (d1.READ_CHUNK_BYTES - split) + needle + b"\n"
    source = _make_source_repo(tmp_path, main_payload=payload)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    snapshot = inspect_repository_snapshot(mirror, expected_refs, inventory)

    _assert_blocked_snapshot(snapshot)
    assert snapshot["scanned_blob_bytes"] > d1.READ_CHUNK_BYTES
    assert snapshot["categories"]["unexpected_scope"] >= 1


def test_inspect_repository_snapshot_reports_hidden_ref_matches_without_values(
    tmp_path: Path,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    report = inspect_repository_snapshot(mirror, expected_refs, inventory)

    _assert_blocked_snapshot(report)
    assert report.get("sensitive_matches_present") is True or any(
        isinstance(value, int) and value > 0
        for key, value in report.items()
        if "count" in key
    )


def test_inspect_repository_snapshot_classifies_sensitive_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A savepoint deliberately exports the real commit identity before running
    # tests. Keep this synthetic Git fixture hermetic so those variables cannot
    # override its per-command author and tagger identities.
    for name in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    _git(source, "switch", "archive/synthetic-sensitive")
    _git(
        source,
        "-c", f"user.name={SYNTHETIC_VALUES['owner'][0]}",
        "-c", "user.email=fixture-author@example.invalid",
        "commit", "--allow-empty", "-m", SYNTHETIC_VALUES["business_name"][0],
    )
    _git(
        source,
        "-c", f"user.name={SYNTHETIC_VALUES['owner'][1]}",
        "-c", "user.email=fixture-tagger@example.invalid",
        "tag", "-a", "fixture-sensitive-metadata", "-m", SYNTHETIC_VALUES["y_tunnus"][0],
    )
    _git(source, "switch", "main")
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    snapshot = inspect_repository_snapshot(mirror, expected_refs, inventory)

    _assert_blocked_snapshot(snapshot)
    assert snapshot["categories"]["metadata"] >= 3
    assert snapshot["metadata_categories"]["author"] >= 1
    assert snapshot["metadata_categories"]["tagger"] >= 1
    assert snapshot["metadata_categories"]["message"] >= 2


def test_inspect_repository_snapshot_counts_same_blob_in_each_commit_context(
    tmp_path: Path,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    _git(source, "switch", "archive/synthetic-sensitive")
    _git(source, "commit", "--allow-empty", "-m", "retain synthetic fixture blob")
    _git(source, "switch", "main")
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    snapshot = inspect_repository_snapshot(mirror, expected_refs, inventory)

    _assert_blocked_snapshot(snapshot)
    assert snapshot["content_match_occurrences"] == 12
    assert snapshot["categories"]["unexpected_scope"] >= 12


def test_inspect_repository_snapshot_classifies_shared_blob_per_path(
    tmp_path: Path,
) -> None:
    source = _make_source_repo(tmp_path)
    shared = "\n".join(
        value for values in SYNTHETIC_VALUES.values() for value in values
    ).encode("utf-8")
    (source / "NOTICE").write_bytes(shared)
    unexpected = source / "synthetic-unexpected" / "shared.txt"
    unexpected.parent.mkdir()
    unexpected.write_bytes(shared)
    _git(source, "add", "NOTICE", "synthetic-unexpected/shared.txt")
    _git(source, "commit", "-m", "add shared synthetic fixture")
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    snapshot = inspect_repository_snapshot(mirror, expected_refs, inventory)

    _assert_blocked_snapshot(snapshot)
    assert snapshot["matched_path_count"] == 2
    assert snapshot["unexpected_path_count"] == 1
    assert snapshot["content_match_occurrences"] == 12
    assert snapshot["categories"]["legal_keep"] == 6
    assert snapshot["categories"]["unexpected_scope"] == 6


def test_inspect_repository_snapshot_observes_deleted_historical_path(
    tmp_path: Path,
) -> None:
    source = _make_source_repo(tmp_path)
    historical = source / "synthetic-deleted.txt"
    historical.write_text(
        "\n".join(value for values in SYNTHETIC_VALUES.values() for value in values),
        encoding="utf-8",
    )
    _git(source, "add", historical.name)
    _git(source, "commit", "-m", "add synthetic historical fixture")
    historical.unlink()
    _git(source, "add", "--update")
    _git(source, "commit", "-m", "delete synthetic historical fixture")
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    snapshot = inspect_repository_snapshot(mirror, expected_refs, inventory)

    _assert_blocked_snapshot(snapshot)
    assert snapshot["commit_count"] == 3
    assert snapshot["matched_path_count"] == 1
    assert snapshot["unexpected_path_count"] == 1
    assert snapshot["content_match_occurrences"] == 6
    assert snapshot["categories"]["unexpected_scope"] == 6


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_inspect_repository_snapshot_rejects_unsupported_tree_modes(
    tmp_path: Path, mode: str,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    if mode == "120000":
        target = tmp_path / "synthetic-link-target.txt"
        target.write_text("synthetic target\n", encoding="utf-8")
        oid = _git(source, "hash-object", "-w", str(target)).stdout.strip()
        path = "synthetic-link"
    else:
        oid = _git(source, "rev-parse", "HEAD").stdout.strip()
        path = "synthetic-gitlink"
    _git(source, "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}")
    _git(source, "commit", "-m", f"add synthetic mode {mode}")
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    with pytest.raises(InspectionError, match="tree_mode_unsupported"):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


@pytest.mark.parametrize("object_kind", ["commit", "tag"])
def test_inspect_repository_snapshot_rejects_undecodable_metadata(
    tmp_path: Path, object_kind: str,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    head = _git(source, "rev-parse", "HEAD").stdout.strip()
    if object_kind == "commit":
        tree = _git(source, "rev-parse", "HEAD^{tree}").stdout.strip()
        raw_commit = (
            f"tree {tree}\nparent {head}\n"
            "author Fixture Runner <fixture@example.invalid> 1700000000 +0000\n"
            "committer Fixture Runner <fixture@example.invalid> 1700000000 +0000\n\n"
        ).encode("ascii") + b"synthetic undecodable message: \xff\n"
        oid = _git_bytes(
            source,
            "hash-object", "-t", "commit", "-w", "--stdin",
            input_bytes=raw_commit,
        ).decode("ascii")
        _git(source, "update-ref", "refs/heads/synthetic-invalid-metadata", oid)
    else:
        raw_tag = (
            f"object {head}\ntype commit\ntag synthetic-invalid-metadata\n"
            "tagger Fixture Runner <fixture@example.invalid> 1700000000 +0000\n\n"
        ).encode("ascii") + b"synthetic undecodable tag: \xff\n"
        oid = _git_bytes(
            source,
            "hash-object", "-t", "tag", "-w", "--stdin",
            input_bytes=raw_tag,
        ).decode("ascii")
        _git(source, "update-ref", "refs/tags/synthetic-invalid-metadata", oid)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    with pytest.raises(InspectionError, match="unverifiable_encoding"):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


def test_inspect_repository_snapshot_rejects_vacuous_clean_observation(
    tmp_path: Path,
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    with pytest.raises(InspectionError, match="vacuous_observation"):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


@pytest.mark.parametrize("drift", ["extra", "missing", "moved"])
def test_inspect_repository_snapshot_rejects_any_ref_drift(
    tmp_path: Path, drift: str,
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    refs = _mirror_refs(mirror)
    if drift == "extra":
        expected_refs_map = refs
        _git(mirror, "update-ref", "refs/security/unexpected", next(iter(refs.values())))
    elif drift == "missing":
        expected_refs_map = dict(refs)
        expected_refs_map["refs/security/missing"] = next(iter(refs.values()))
    else:
        expected_refs_map = dict(refs)
        first = next(iter(expected_refs_map))
        expected_refs_map[first] = "0" * 40
    _, _, inventory, expected_refs = _authority_files(
        tmp_path, source, mirror, refs=expected_refs_map,
    )
    with pytest.raises(InspectionError):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


def test_inspect_repository_snapshot_rejects_non_bare_repository(tmp_path: Path) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)
    with pytest.raises(InspectionError):
        inspect_repository_snapshot(source, expected_refs, inventory)


def test_inspect_repository_snapshot_rejects_shallow_bare_clone(tmp_path: Path) -> None:
    source = _make_source_repo(tmp_path)
    shallow = tmp_path / "shallow.git"
    _git(
        tmp_path, "clone", "--bare", "--depth", "1",
        source.resolve().as_uri(), str(shallow),
    )
    inventory_path = _write_json(
        tmp_path / "operator-authority" / "inventory.json", _inventory_payload(),
    )
    expected_path = _write_json(
        tmp_path / "operator-authority" / "expected.json",
        _expected_refs_payload(_mirror_refs(shallow)),
    )
    inventory = load_sensitive_inventory(inventory_path, source, shallow)
    expected_refs = load_expected_refs(expected_path)
    assert _git(shallow, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    with pytest.raises(InspectionError):
        inspect_repository_snapshot(shallow, expected_refs, inventory)


@pytest.mark.parametrize(
    ("tamper", "error_code"),
    [
        ("alternates", "mirror_alternate_or_graft"),
        ("grafts", "mirror_alternate_or_graft"),
        ("replace-directory", "mirror_replace_refs"),
        ("promisor-file", "mirror_partial_clone"),
        ("promisor-config", "mirror_partial_clone"),
        ("fsck-override", "mirror_fsck_override"),
        ("dangling-object", "stored_reachable_object_mismatch"),
    ],
)
def test_inspect_repository_snapshot_rejects_mirror_ambiguity(
    tmp_path: Path, tamper: str, error_code: str,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    if tamper == "alternates":
        (mirror / "objects" / "info" / "alternates").write_text(
            str(tmp_path / "synthetic-objects"), encoding="utf-8",
        )
    elif tamper == "grafts":
        (mirror / "info" / "grafts").write_text("synthetic\n", encoding="utf-8")
    elif tamper == "replace-directory":
        (mirror / "refs" / "replace").mkdir()
    elif tamper == "promisor-file":
        (mirror / "objects" / "pack" / "synthetic.promisor").write_bytes(b"")
    elif tamper == "promisor-config":
        _git(mirror, "config", "remote.synthetic.promisor", "true")
    elif tamper == "fsck-override":
        _git(mirror, "config", "transfer.fsckObjects", "false")
    elif tamper == "dangling-object":
        detached = tmp_path / "detached.txt"
        detached.write_text("synthetic detached object\n", encoding="utf-8")
        _git(mirror, "hash-object", "-w", str(detached))
    else:
        pytest.fail(f"unhandled synthetic tamper: {tamper}")

    with pytest.raises(InspectionError, match=error_code):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


def test_object_inventory_rejects_reachable_object_missing_from_storage_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_oid = "a" * 40
    missing_oid = "b" * 40
    monkeypatch.setattr(
        d1,
        "_git_text",
        lambda _mirror, _args: f"{stored_oid} commit\n",
    )

    def reachable_output(_mirror, args, *, input_bytes=None, **_kwargs):
        assert args == ["rev-list", "--objects", "--no-object-names", "--stdin"]
        assert input_bytes == b"refs/heads/main\n"
        return f"{stored_oid}\n{missing_oid}\n".encode("ascii")

    monkeypatch.setattr(d1, "_git", reachable_output)
    with pytest.raises(InspectionError, match="stored_reachable_object_mismatch"):
        d1._object_inventory(Path("synthetic-mirror.git"), ("refs/heads/main",))


def test_inspect_repository_snapshot_rejects_ref_drift_during_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)
    real_preflight = d1._mirror_preflight
    calls = 0

    def drifting_preflight(path, expected):
        nonlocal calls
        calls += 1
        refs = real_preflight(path, expected)
        if calls == 2:
            return {**refs, "refs/security/synthetic-drift": next(iter(refs.values()))}
        return refs

    monkeypatch.setattr(d1, "_mirror_preflight", drifting_preflight)
    with pytest.raises(InspectionError, match="refs_changed_during_inspection"):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


def test_inspect_repository_snapshot_rejects_config_drift_during_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)
    real_stream_object = d1._stream_git_object
    changed = False

    def drifting_stream_object(path, object_type, oid, consumer):
        nonlocal changed
        size = real_stream_object(path, object_type, oid, consumer)
        if not changed:
            changed = True
            _git(path, "config", "synthetic.drift", "observed")
        return size

    monkeypatch.setattr(d1, "_stream_git_object", drifting_stream_object)
    with pytest.raises(InspectionError, match="mirror_filesystem_changed_during_inspection"):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


def test_inspect_repository_snapshot_rejects_object_drift_during_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)
    detached = tmp_path / "drifting-object.txt"
    detached.write_text("synthetic object created during scan\n", encoding="utf-8")
    real_stream_object = d1._stream_git_object
    changed = False

    def drifting_stream_object(path, object_type, oid, consumer):
        nonlocal changed
        size = real_stream_object(path, object_type, oid, consumer)
        if not changed:
            changed = True
            _git(path, "hash-object", "-w", str(detached))
        return size

    monkeypatch.setattr(d1, "_stream_git_object", drifting_stream_object)
    with pytest.raises(InspectionError, match="stored_reachable_object_mismatch"):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


def test_inspect_repository_snapshot_rejects_path_identity_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)
    monkeypatch.setattr(d1.os.path, "samestat", lambda _left, _right: False)
    with pytest.raises(InspectionError, match="mirror_path_changed_during_inspection"):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


def test_inspect_repository_snapshot_never_places_values_in_git_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)
    real_run = subprocess.run
    real_popen = subprocess.Popen
    commands: list[tuple[str, ...]] = []
    stream_commands: list[tuple[str, ...]] = []
    inputs: list[bytes | None] = []
    environments: list[dict[str, str]] = []
    stream_environments: list[dict[str, str]] = []

    def recording_run(command, *args, **kwargs):
        commands.append(tuple(os.fspath(item) for item in command))
        inputs.append(kwargs.get("input"))
        environments.append(dict(kwargs["env"]))
        return real_run(command, *args, **kwargs)

    def recording_popen(command, *args, **kwargs):
        stream_commands.append(tuple(os.fspath(item) for item in command))
        stream_environments.append(dict(kwargs["env"]))
        return real_popen(command, *args, **kwargs)

    monkeypatch.setenv("GIT_DIR", str(source / ".git"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "synthetic-hooks"))
    monkeypatch.setattr(d1.subprocess, "run", recording_run)
    monkeypatch.setattr(d1.subprocess, "Popen", recording_popen)
    report = inspect_repository_snapshot(mirror, expected_refs, inventory)
    _assert_blocked_snapshot(report)
    argv = "\n".join(
        "\0".join(command) for command in (*commands, *stream_commands)
    )
    assert commands
    assert stream_commands
    for values in SYNTHETIC_VALUES.values():
        for value in values:
            assert value not in argv
    rev_lists = [
        (command, input_bytes)
        for command, input_bytes in zip(commands, inputs)
        if "rev-list" in command
    ]
    assert rev_lists
    assert all("--stdin" in command for command, _input in rev_lists)
    assert all(
        not any(ref in command for ref in expected_refs.refs)
        for command, _input in rev_lists
    )
    assert all(input_bytes for _command, input_bytes in rev_lists)
    assert all(
        command[:2] == ("git", "--no-replace-objects")
        for command in (*commands, *stream_commands)
    )
    for environment in (*environments, *stream_environments):
        assert "GIT_DIR" not in environment
        assert "GIT_CONFIG_COUNT" not in environment
        assert "GIT_CONFIG_KEY_0" not in environment
        assert "GIT_CONFIG_VALUE_0" not in environment
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["GIT_TERMINAL_PROMPT"] == "0"
        assert environment["GIT_REF_PARANOIA"] == "1"


def test_inspect_repository_snapshot_git_launch_failure_is_not_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(tmp_path, source, mirror)

    def fail_launch(*args, **kwargs):
        raise OSError("synthetic git launch failure")

    monkeypatch.setattr(d1.subprocess, "run", fail_launch)
    with pytest.raises(InspectionError):
        inspect_repository_snapshot(mirror, expected_refs, inventory)


@pytest.mark.parametrize("mode", ["dry-run", "push", "force-push"])
def test_cli_execution_modes_hard_refuse_without_reading_authorities(
    mode: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        d1,
        "load_sensitive_inventory",
        lambda *args, **kwargs: pytest.fail("refused mode read inventory"),
    )
    rc = main([mode])
    assert rc == 2
    report = json.loads(capsys.readouterr().out)
    _assert_blocked_report(report)
    assert report["inspection_status"] == "incomplete"
    assert report["requested_mode"] == mode
    assert report["reason"] == "execution_unavailable_in_prep"


@pytest.mark.parametrize("mode", ["inspect", "plan", "detect"])
def test_cli_prep_modes_require_all_three_authority_inputs(
    mode: str, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main([mode])
    captured = capsys.readouterr()
    assert rc == 3
    assert captured.out == ""
    _assert_blocked_report(json.loads(captured.err))


@pytest.mark.parametrize("omitted", ["--inventory", "--mirror", "--expected-refs"])
def test_cli_prep_modes_reject_each_missing_authority_input(
    omitted: str, capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "inspect",
        "--inventory", "synthetic-inventory.json",
        "--mirror", "synthetic-mirror.git",
        "--expected-refs", "synthetic-refs.json",
    ]
    index = arguments.index(omitted)
    del arguments[index:index + 2]
    rc = main(arguments)
    captured = capsys.readouterr()
    assert rc == 3
    assert captured.out == ""
    report = json.loads(captured.err)
    _assert_blocked_report(report)
    assert report["inspection_status"] == "incomplete"
    assert report["error_category"] == "inventory"
    assert report["error_code"] == "required_authority_missing"


@pytest.mark.parametrize("selection", ["duplicate", "arbitrary"])
def test_cli_rejects_duplicate_or_arbitrary_settings_selection_before_inventory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    source = _make_source_repo(tmp_path)
    expected_settings = [source / relative for relative in d1.SETTINGS_PATHS]
    supplied = (
        [expected_settings[0], expected_settings[0]]
        if selection == "duplicate"
        else [expected_settings[0], source / "configs" / "synthetic-extra.yaml"]
    )
    monkeypatch.setattr(
        d1,
        "load_sensitive_inventory",
        lambda *args, **kwargs: pytest.fail("invalid settings reached inventory"),
    )
    argv = [
        "inspect",
        "--inventory", str(tmp_path / "inventory.json"),
        "--mirror", str(tmp_path / "mirror.git"),
        "--expected-refs", str(tmp_path / "refs.json"),
        "--repo", str(source),
    ]
    for path in supplied:
        argv.extend(("--settings", str(path)))
    rc = main(argv)
    captured = capsys.readouterr()
    assert rc == 4
    assert captured.out == ""
    report = json.loads(captured.err)
    _assert_blocked_report(report)
    assert report["inspection_status"] == "incomplete"
    assert report["error_category"] == "inspection"
    assert report["error_code"] == "settings_selection_invalid"


@pytest.mark.parametrize("option", ["--help", "--version"])
def test_cli_help_and_version_are_the_only_zero_exit_paths(
    option: str, capsys: pytest.CaptureFixture[str],
) -> None:
    try:
        rc = main([option])
    except SystemExit as exc:
        rc = exc.code
    assert rc == 0
    assert capsys.readouterr().out


def test_prep_documentation_pins_two_vs_203_without_execution_recipes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runbook = (
        Path(d1.ROOT) / "docs" / "operations" / "D1_PII_SCRUB_RUNBOOK.md"
    ).read_text(encoding="utf-8")
    module_doc = d1.__doc__ or ""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    help_text = capsys.readouterr().out

    surfaces = {
        "runbook": runbook,
        "module_docstring": module_doc,
        "cli_help": help_text,
    }
    for label, surface in surfaces.items():
        normalized = " ".join(surface.casefold().split())
        assert re.search(
            r"\b(?:two|2)\s+settings paths?\b.{0,200}\b203\b",
            normalized,
        ), label
        assert "prepared_blocked" in normalized, label
        assert "unresolved" in normalized or "unclassified" in normalized, label

    recipe_patterns = (
        r"(?im)^\s*(?:\$|ps>|>)?\s*git\s+(?:filter-repo|filter-branch|push|"
        r"clone|fetch|reset|reflog|restore|checkout|replace|update-ref)\b",
        r"(?im)^\s*(?:\$|ps>|>)?\s*(?:python|py)\b.*\bd1_pii_scrub(?:\.py)?\s+"
        r"(?:dry-run|push|force-push)\b",
        r"(?im)^\s*(?:\$|ps>|>)?\s*(?:rm\s+-rf|remove-item|move-item|copy-item)\b",
    )
    for label, surface in surfaces.items():
        for pattern in recipe_patterns:
            assert re.search(pattern, surface) is None, (label, pattern)

    for refused_mode in ("dry-run", "push", "force-push"):
        assert refused_mode in help_text


def test_cli_inventory_error_is_rc3_and_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    bad_inventory = _write_json(
        tmp_path / "operator-authority" / "inventory.json",
        {**_inventory_payload(), "extra": "synthetic"},
    )
    expected = _write_json(
        tmp_path / "operator-authority" / "expected.json",
        _expected_refs_payload(_mirror_refs(mirror)),
    )
    rc = main([
        "inspect", "--inventory", str(bad_inventory),
        "--mirror", str(mirror), "--expected-refs", str(expected),
        "--repo", str(source),
    ])
    assert rc == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    report = json.loads(captured.err)
    _assert_blocked_report(report)
    assert report["inspection_status"] == "incomplete"
    assert report["error_category"] == "inventory"


def test_cli_lone_surrogate_inventory_is_rc3_and_sanitized(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    fields = {key: list(values) for key, values in SYNTHETIC_VALUES.items()}
    fields["business_name"] = ["\ud800"]
    bad_inventory = _write_json(
        tmp_path / "operator-authority" / "inventory.json",
        _inventory_payload(fields),
    )
    expected = _write_json(
        tmp_path / "operator-authority" / "expected.json",
        _expected_refs_payload(_mirror_refs(mirror)),
    )

    rc = main([
        "inspect", "--inventory", str(bad_inventory),
        "--mirror", str(mirror), "--expected-refs", str(expected),
        "--repo", str(source),
    ])

    assert rc == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ud800" not in captured.err.casefold()
    report = json.loads(captured.err)
    _assert_blocked_report(report)
    assert report["error_code"] == "inventory_variant_encoding_invalid"


def test_cli_inspection_error_is_rc4_and_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source = _make_source_repo(tmp_path)
    mirror = _make_mirror(tmp_path, source)
    inventory, expected, _, _ = _authority_files(tmp_path, source, mirror)
    _git(
        mirror, "update-ref", "refs/security/unexpected",
        next(iter(_mirror_refs(mirror).values())),
    )
    rc = main([
        "inspect", "--inventory", str(inventory),
        "--mirror", str(mirror), "--expected-refs", str(expected),
        "--repo", str(source),
    ])
    assert rc == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    report = json.loads(captured.err)
    _assert_blocked_report(report)
    assert report["inspection_status"] == "incomplete"
    assert report["error_category"] == "inspection"


@pytest.mark.parametrize("mode", ["inspect", "plan", "detect"])
def test_cli_completed_preparation_is_blocked_rc2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    mirror = _make_mirror(tmp_path, source)
    inventory, expected, _, _ = _authority_files(tmp_path, source, mirror)
    rc = main([
        mode, "--inventory", str(inventory),
        "--mirror", str(mirror), "--expected-refs", str(expected),
        "--repo", str(source),
    ])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    _assert_blocked_report(report)
    assert report["inspection_status"] == "complete"
    assert report["prepared_from_commit"] == _mirror_refs(mirror)["refs/heads/main"]
    assert report["inventory"] == {"field_count": 3, "variant_count": 6}
    assert report["settings"] == {
        "validated_file_count": 2,
        "validated_field_count": 6,
    }
    _assert_blocked_snapshot(report["history_observation"])


def test_build_preparation_report_never_grants_authority(tmp_path: Path) -> None:
    source = _make_source_repo(tmp_path, hidden_sensitive_ref=True)
    mirror = _make_mirror(tmp_path, source)
    _, _, inventory, expected_refs = _authority_files(
        tmp_path, source, mirror,
    )
    inspection = inspect_repository_snapshot(mirror, expected_refs, inventory)
    settings = [
        validate_redacted_settings(source / relative)
        for relative in d1.SETTINGS_PATHS
    ]
    report = build_preparation_report(
        inspection,
        prepared_from_commit=expected_refs.refs["refs/heads/main"],
        settings_results=settings,
        inventory=inventory,
    )
    _assert_blocked_report(report)
    assert report["inspection_status"] == "complete"
    assert report["history_observation"] == inspection
    assert report["prepared_from_commit"] == expected_refs.refs["refs/heads/main"]
    assert report["inventory"] == {"field_count": 3, "variant_count": 6}
    assert report["settings"] == {
        "validated_file_count": len(d1.SETTINGS_PATHS),
        "validated_field_count": 3 * len(d1.SETTINGS_PATHS),
    }
    assert set(report["blockers"]) == {
        "scope_authority_absent",
        "legal_authority_absent",
        "execution_authority_absent",
        "separate_destructive_phase_required",
    }


@pytest.mark.parametrize("commit", ["", "0" * 39, "A" * 40, "z" * 40])
def test_build_preparation_report_rejects_invalid_prepared_commit(
    commit: str,
) -> None:
    inventory = d1.SensitiveInventory({
        field: tuple(value.encode("utf-8") for value in values)
        for field, values in SYNTHETIC_VALUES.items()
    })
    with pytest.raises(InspectionError, match="prepared_commit_invalid"):
        build_preparation_report(
            {"observation": "snapshot_inspected"},
            prepared_from_commit=commit,
            settings_results=[{"field_count": 3}] * len(d1.SETTINGS_PATHS),
            inventory=inventory,
        )


@pytest.mark.parametrize("count", [0, 1, 3])
def test_build_preparation_report_rejects_incomplete_settings_evidence(
    count: int,
) -> None:
    inventory = d1.SensitiveInventory({
        field: tuple(value.encode("utf-8") for value in values)
        for field, values in SYNTHETIC_VALUES.items()
    })
    with pytest.raises(InspectionError, match="settings_evidence_incomplete"):
        build_preparation_report(
            {"observation": "snapshot_inspected"},
            prepared_from_commit="a" * 40,
            settings_results=[{"field_count": 3}] * count,
            inventory=inventory,
        )
