"""Read-only classification evidence, not approval or standing-sign admission.

Use the controlled bridge wrapper after an approved normal-bundle install.
The caller must independently establish trust in the explicit repository and
Git executable. Absolute paths describe the inputs; they do not attest trust.
No live identities, objects, paths or enable flags are inferred from the cwd.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    # The wrapper hash-verifies this leaf. Import only its own code root,
    # never the target repository supplied as classification input.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.check_standing_consensus_sign_class import classify_ab  # noqa: E402


class _SingleValue(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error(f"{option_string} must be supplied exactly once")
        setattr(namespace, self.dest, values)


def _object_id(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise argparse.ArgumentTypeError("expected a full lowercase SHA1 object ID")
    return value


def _absolute_path(value: str) -> str:
    if not Path(value).is_absolute() or any(ord(char) < 32 for char in value):
        raise argparse.ArgumentTypeError("expected an explicit absolute path")
    return value


def _git_path(value: str) -> str:
    _absolute_path(value)
    if Path(value).name.casefold() not in ("git", "git.exe"):
        raise argparse.ArgumentTypeError("expected the absolute Git executable path")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--repository", required=True, type=_absolute_path, action=_SingleValue)
    parser.add_argument("--git", required=True, type=_git_path, action=_SingleValue)
    for name in ("base", "head", "base-tree", "head-tree", "target"):
        parser.add_argument("--" + name, required=True, type=_object_id, action=_SingleValue)
    parser.add_argument("--path", required=True, action="append", dest="paths")
    args = parser.parse_args(argv)
    context = {"base": args.base, "head": args.head, "base_tree": args.base_tree,
               "head_tree": args.head_tree, "target": args.target,
               "git": args.git, "cwd": args.repository}
    report: dict[str, Any] = {
        "schema": "wd.added-tests-classification-readonly.v1",
        "read_only": True, "authority_effect": "none",
        "input_provenance": {"cwd": args.repository, "git": args.git},
        "input_context": dict(context),
    }
    try:
        result = classify_ab(args.paths, git_context=context)
        if not isinstance(result, Mapping):
            raise TypeError("classification result must be an object")
    except (OSError, ValueError, TypeError):
        report.update(error="classification_unavailable", evidence_status="not_established")
        print(json.dumps(report, ensure_ascii=True, sort_keys=True))
        return 2
    proof = result.get("git_evidence")
    report.update(
        classification=dict(result),
        evidence_status=("rederived_git_added_regular_tests" if isinstance(proof, Mapping)
                         and proof.get("status") == "rederived_git_added_regular_tests"
                         else "not_established"),
    )
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
