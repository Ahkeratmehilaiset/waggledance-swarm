#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Operator decision-pack convention (v1).

A decision pack is a machine-readable artifact an agent emits for a single
charter-gated operator decision (the escalation categories that must NOT be
auto-resolved by the loop). It packages the options + concrete data + an agent
recommendation and a single operator sign-off field, so the operator clears the
gate in one step instead of a multi-round bridge discussion.

Packs live in ``docs/operator_inbox/<decision-id>.yaml``. This module only
loads, validates, and reports open/signed state. It NEVER resolves a pack and
NEVER mutates a pack -- escalation categories stay operator-gated. A signed pack
becomes normal implementation input, never a merge bypass.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a project dependency
    yaml = None  # type: ignore[assignment]

PACK_SCHEMA_VERSION = "waggledance.operator_decision_pack.v1"

# The charter escalation categories a pack may gate. These mirror the
# always-operator categories in docs/architecture/IDLE_AUTONOMY_CHARTER.md.
ALLOWED_CATEGORIES = frozenset(
    {
        "credentials",
        "destructive_git",
        "payment",
        "write_scope_conflict",
        "legal_security",
        "dependency_security",
        "docker_promotion",
    }
)

REQUIRED_TOP_FIELDS = (
    "schema_version",
    "decision_id",
    "category",
    "created_utc",
    "author_agent",
    "options",
    "operator_signoff",
)


class DecisionPackError(ValueError):
    """Raised when a pack does not satisfy the v1 schema."""


if yaml is not None:
    class _UniqueKeyLoader(yaml.SafeLoader):
        pass


    def _construct_unique_mapping(
        loader: yaml.SafeLoader,
        node: yaml.nodes.MappingNode,
        deep: bool = False,
    ) -> dict[Any, Any]:
        loader.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping


    _UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        _construct_unique_mapping,
    )


def load_pack(path: Path | str) -> dict[str, Any]:
    """Load and validate one decision pack. Raises DecisionPackError on schema
    violations. Does not mutate the file."""
    if yaml is None:  # pragma: no cover
        raise DecisionPackError("PyYAML is required to read decision packs")
    path = Path(path)
    try:
        raw = yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except yaml.YAMLError as exc:  # type: ignore[union-attr]
        raise DecisionPackError(f"invalid YAML in {path.name}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise DecisionPackError(f"{path.name}: pack must be a mapping")
    _validate_pack(raw, source=path.name)
    return dict(raw)


def _validate_pack(pack: Mapping[str, Any], *, source: str) -> None:
    missing = [field for field in REQUIRED_TOP_FIELDS if field not in pack]
    if missing:
        raise DecisionPackError(f"{source}: missing fields {sorted(missing)}")
    if pack.get("schema_version") != PACK_SCHEMA_VERSION:
        raise DecisionPackError(
            f"{source}: schema_version must be {PACK_SCHEMA_VERSION!r}"
        )
    if pack.get("category") not in ALLOWED_CATEGORIES:
        raise DecisionPackError(
            f"{source}: category must be one of {sorted(ALLOWED_CATEGORIES)}"
        )
    options = pack.get("options")
    if not isinstance(options, list) or len(options) < 2:
        raise DecisionPackError(f"{source}: options must be a list of >= 2 entries")
    option_ids = []
    for index, option in enumerate(options):
        if not isinstance(option, Mapping):
            raise DecisionPackError(f"{source}: option {index} must be a mapping")
        option_id = option.get("id")
        if not str(option_id or "").strip():
            raise DecisionPackError(f"{source}: option {index} needs a non-empty id")
        option_ids.append(str(option_id))
    if len(set(option_ids)) != len(option_ids):
        raise DecisionPackError(f"{source}: option ids must be unique")
    signoff = pack.get("operator_signoff")
    if not isinstance(signoff, Mapping):
        raise DecisionPackError(f"{source}: operator_signoff must be a mapping")
    # When signed, the chosen_option must reference a real option id.
    chosen = str(signoff.get("chosen_option", "") or "").strip()
    if chosen and chosen not in option_ids:
        raise DecisionPackError(
            f"{source}: operator_signoff.chosen_option {chosen!r} not in option ids"
        )


def is_signed(pack: Mapping[str, Any]) -> bool:
    """A pack is signed (gate cleared) only when the operator filled signed_by
    AND chose a valid option. Empty/draft signoff => not signed (fail-closed)."""
    signoff = pack.get("operator_signoff")
    if not isinstance(signoff, Mapping):
        return False
    signed_by = str(signoff.get("signed_by", "") or "").strip()
    chosen = str(signoff.get("chosen_option", "") or "").strip()
    return bool(signed_by) and bool(chosen)


def scan_inbox(inbox_dir: Path | str) -> dict[str, Any]:
    """Scan an operator_inbox directory. Returns open vs signed pack summaries.

    Malformed packs are reported under ``invalid`` (fail-closed: a pack we
    cannot parse is surfaced for attention, never silently treated as signed).
    """
    inbox_dir = Path(inbox_dir)
    open_packs: list[dict[str, Any]] = []
    signed_packs: list[dict[str, Any]] = []
    invalid: list[dict[str, str]] = []
    if not inbox_dir.exists():
        return {"open": [], "signed": [], "invalid": []}
    for path in sorted(inbox_dir.glob("*.yaml")):
        try:
            pack = load_pack(path)
        except DecisionPackError as exc:
            invalid.append({"file": path.name, "error": str(exc)})
            continue
        summary = {
            "decision_id": pack.get("decision_id"),
            "category": pack.get("category"),
            "file": path.name,
            "options": [str(o.get("id")) for o in pack.get("options", [])],
        }
        if is_signed(pack):
            signoff = pack.get("operator_signoff", {})
            summary["chosen_option"] = signoff.get("chosen_option")
            signed_packs.append(summary)
        else:
            open_packs.append(summary)
    return {"open": open_packs, "signed": signed_packs, "invalid": invalid}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inbox-dir",
        type=Path,
        default=Path("docs/operator_inbox"),
        help="Directory of operator decision packs (default: docs/operator_inbox).",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    report = scan_inbox(args.inbox_dir)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"open operator decision packs: {len(report['open'])}")
        for pack in report["open"]:
            print(f"  - {pack['decision_id']} [{pack['category']}] {pack['file']}")
        if report["invalid"]:
            print(f"invalid packs: {len(report['invalid'])}")
            for bad in report["invalid"]:
                print(f"  ! {bad['file']}: {bad['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
