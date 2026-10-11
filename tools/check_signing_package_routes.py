#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Signing-package route check (observe-only, consulted by nothing).

For each PR in an operator signing package this classifies the merge route the
installed manual driver (A5: ``tools/merge_with_bridge_receipt.py`` behind
``ops/windows/reboot/BridgeCodeContext.ps1``) can take, then checks a saved
no-apply driver report against the refusals that route must produce *for that
driver version*:

* ``driver``: every changed path passes the charter path gate and the RCO slot
  is held by an identity the driver's only policy (legacy) recognizes. The
  no-apply run must end ``merge_receipt_ready`` with no errors.
* ``manual``: the driver refuses by design (a charter denylist hit, a path off
  the allowlist -- A5 admits no operator path exception -- or an RCO-slot holder
  the legacy policy does not recognize). The operator-signed direct merge is the
  route, and the no-apply run must refuse with exactly the expected set. Any
  other refusal (draft, CI, retained accepted-queue WAL, consensus...) means the
  manual route would also skip a gate that is not refused by design.

Expected refusals are bound to a driver profile keyed by the installed source
commit and its externally anchored deployment-manifest hash, plus the digest of
the charter the classification used. An unknown driver, a changed charter, an
RCO-slot refusal this version does not profile, or a malformed report is
reported as ``operator_review_required``; nothing is default-allowed.

A report binds to its PR by number, head and base. A refusal also binds by the
gate's own path decision, which must equal this checker's decision on the
package paths, so a package that omits a changed path cannot pass. A ready
report carries no gate report, only its exact ``gh`` merge command. The driver
report carries no source commit or manifest hash, so the driver identity is the
package's assertion and is reported as unverified.

Observe-only: the tool writes nothing, merges nothing and exits 0 for any
well-formed package whatever the verdict (2 for invalid input).

    python tools/check_signing_package_routes.py package.json --json
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.idle_consensus_charter import (  # noqa: E402
    DEFAULT_CHARTER_PATH,
    evaluate_paths,
    load_charter,
)

PACKAGE_SCHEMA = "wd.signing-package-routes.v1"
REPORT_SCHEMA = "wd.signing-package-route-check.v1"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9A-F]{64}$")
AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")


@dataclass(frozen=True)
class DriverProfile:
    """What one installed driver version refuses, read from its own source."""

    source_commit: str
    manifest_sha256: str
    charter_sha256: str
    review_policy: str
    repo: str
    recognized_rcos: tuple[str, ...]
    path_refusals: Mapping[str, str]


# Keyed by the installed generation's source commit. A new driver generation
# gets a new entry; tests/tools/test_check_signing_package_routes.py re-derives
# every field below from the source at the commit, so an edited profile or a
# changed driver cannot silently keep the old expectations.
DRIVER_PROFILES: Mapping[str, DriverProfile] = {
    "2bd7a01099b21a5d664d02e8cf0cd09b991239aa": DriverProfile(
        source_commit="2bd7a01099b21a5d664d02e8cf0cd09b991239aa",
        manifest_sha256=(
            "CC55E582D968619E6F266B55CE18627E8829A8D57AB45A5965AE4B8AA5FB297B"
        ),
        charter_sha256=(
            "53DB32B2F0D0218B177233F698CBD23007B9E9A45AD28A02231FD2D4E2534A48"
        ),
        # BridgeCodeContext admits no --review-policy, so only the default runs.
        review_policy="bridge_consensus_v1",
        # A5 refuses any other --repo value.
        repo="Ahkeratmehilaiset/waggledance-swarm",
        recognized_rcos=("claude-rco-1", "claude-rco-2"),
        path_refusals={
            "denylist hit": "path gate failed: denylist hit",
            "paths not on allowlist": "path gate failed: paths not on allowlist",
        },
    ),
}


def charter_digest(path: Path) -> str:
    """SHA-256 of the charter bytes with CRLF folded to LF (checkout-neutral)."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest().upper()


def check_package(
    package: Mapping[str, Any],
    *,
    charter_path: Path | None = None,
    profiles: Mapping[str, DriverProfile] | None = None,
) -> dict[str, Any]:
    """Classify every PR's route and check its no-apply report. Pure read."""
    prs, driver = _validate_package(package)
    profiles = DRIVER_PROFILES if profiles is None else profiles
    target = charter_path or DEFAULT_CHARTER_PATH
    charter = load_charter(target)
    digest = charter_digest(target)

    profile = profiles.get(driver["source_commit"])
    binding_errors: list[str] = []
    if profile is None:
        binding_errors.append("no driver profile for this source commit")
    else:
        if profile.manifest_sha256 != driver["manifest_sha256"]:
            binding_errors.append("deployment manifest differs from the profile")
        if profile.charter_sha256 != digest:
            binding_errors.append("charter differs from the profiled driver's charter")

    results = [
        _check_pr(
            entry,
            profile=None if binding_errors else profile,
            charter=charter,
        )
        for entry in prs
    ]
    if any(r["verdict"] == "operator_review_required" for r in results):
        verdict = "operator_review_required"
    elif any(r["verdict"] == "pending_no_apply" for r in results):
        verdict = "pending_no_apply"
    else:
        verdict = "consistent"
    return {
        "schema": REPORT_SCHEMA,
        "observe_only": True,
        "driver": {
            **driver,
            "bound": not binding_errors,
            "binding_errors": binding_errors,
            # No driver report names its source commit or manifest hash.
            "identity_source": "package_assertion",
        },
        "charter_sha256": digest,
        "verdict": verdict,
        "prs": results,
    }


def _check_pr(
    entry: Mapping[str, Any],
    *,
    profile: DriverProfile | None,
    charter: Any,
) -> dict[str, Any]:
    paths = list(entry["changed_paths"])
    holder = entry["rco_slot_holder"]
    path_gate = evaluate_paths(charter, paths)
    route_reasons: list[str] = []
    expected: list[str] | None = []
    if not path_gate.allowed:
        route_reasons.append(f"charter path gate: {path_gate.reason}")
    if profile is None:
        expected = None
    else:
        if not path_gate.allowed:
            refusal = profile.path_refusals.get(path_gate.reason)
            if refusal is None:
                expected = None
            else:
                expected.append(refusal)
        if holder not in profile.recognized_rcos:
            # The legacy verifier's wording for an unseated RCO slot is not
            # profiled in v1, so the whole expectation stays unbound.
            route_reasons.append(
                f"driver policy {profile.review_policy} cannot seat RCO slot holder {holder}"
            )
            expected = None
    route = "manual" if route_reasons else "driver"
    if profile is None:
        route_reasons.append("route unconfirmed: driver version not bound")

    check = _check_report(
        entry.get("no_apply_report"),
        pr=entry["pr"],
        head=entry["head"],
        base=entry["base"],
        path_gate=_gate_view(path_gate),
        profile=profile,
    )
    if check["status"] == "report_ok" and expected is None:
        check["status"] = "unbound_expectation"
    elif check["status"] == "report_ok":
        check.update(_compare_refusals(check["observed_refusals"], expected or []))
    if check["status"] == "match":
        verdict = "consistent"
    elif check["status"] == "no_report":
        verdict = "pending_no_apply" if expected is not None else "operator_review_required"
    else:
        verdict = "operator_review_required"
    return {
        "pr": entry["pr"],
        "head": entry["head"],
        "route": route,
        "route_reasons": route_reasons,
        "path_gate": _gate_view(path_gate),
        "rco_slot_holder": holder,
        "expected_refusals": None if expected is None else sorted(expected),
        "no_apply": check,
        "verdict": verdict,
    }


def _gate_view(gate: Any) -> dict[str, Any]:
    return {
        "allowed": bool(gate.allowed),
        "reason": str(gate.reason),
        "blocked_paths": sorted(gate.blocked_paths),
        "unmatched_paths": sorted(gate.unmatched_paths),
    }


def _compare_refusals(observed: list[str], expected: list[str]) -> dict[str, Any]:
    """Exact set equality: an extra refusal is a gate the manual route would skip."""
    seen, wanted = set(observed), set(expected)
    duplicates = len(seen) != len(observed)
    return {
        "status": "match" if seen == wanted and not duplicates else "mismatch",
        "unexpected_refusals": sorted(seen - wanted),
        "missing_refusals": sorted(wanted - seen),
        "duplicate_refusals": duplicates,
    }


def _check_report(
    report: Any,
    *,
    pr: int,
    head: str,
    base: str,
    path_gate: Mapping[str, Any],
    profile: DriverProfile | None,
) -> dict[str, Any]:
    if report is None:
        return {"status": "no_report", "observed_refusals": None}

    def bad(status: str, detail: str) -> dict[str, Any]:
        return {"status": status, "detail": detail, "observed_refusals": None,
                "driver_identity": "unverified"}

    if not isinstance(report, Mapping):
        return bad("malformed_report", "report is not an object")
    if report.get("gh_merge_attempted") is not False or report.get("merge_executed") is not False:
        return bad("not_a_no_apply_report", "report does not prove no merge was attempted")
    if report.get("apply") is True:
        return bad("not_a_no_apply_report", "report was produced with --apply")
    if report.get("pr_number") != pr:
        return bad("binding_mismatch", "report is for a different PR")
    errors = report.get("errors", [])
    if not isinstance(errors, list) or not all(isinstance(e, str) for e in errors):
        return bad("malformed_report", "errors is not a list of strings")
    if report.get("ok") is True:
        if report.get("decision") != "merge_receipt_ready" or errors:
            return bad("unexpected_decision", f"ok report with decision {report.get('decision')!r}")
        if report.get("expected_head") != head or report.get("expected_base_sha") != base:
            return bad("binding_mismatch", "report is for a different head or base")
        # The ready report has no gate report; its exact merge command is the
        # remaining binding (driver _merge_command: repo, head, method).
        repo = None if profile is None else profile.repo
        command = report.get("gh_command")
        if not (
            isinstance(command, list)
            and len(command) == 8
            and command[:5] == ["gh", "pr", "merge", str(pr), "--repo"]
            and (repo is None or command[5] == repo)
            and command[6] == f"--match-head-commit={head}"
            and command[7] in ("--squash", "--merge", "--rebase")
        ):
            return bad("binding_mismatch", "ready report merge command does not bind")
        return {"status": "report_ok", "decision": "merge_receipt_ready",
                "observed_refusals": [], "driver_identity": "unverified"}
    if report.get("ok") is not False:
        return bad("malformed_report", "ok is not a boolean")
    if report.get("decision") != "receipt_preflight_failed" or report.get("stage") != "receipt":
        return bad("unexpected_decision", f"refusal decision {report.get('decision')!r} is not a gate refusal")
    receipt = report.get("receipt_report")
    gate = receipt.get("gate_report") if isinstance(receipt, Mapping) else None
    if not isinstance(gate, Mapping):
        return bad("malformed_report", "refusal carries no gate report")
    if receipt.get("decision") != "merge_plan_not_receipt_eligible":
        return bad("unexpected_decision", f"receipt decision {receipt.get('decision')!r}")
    if gate.get("expected_head") != head or gate.get("pr_number") != pr:
        return bad("binding_mismatch", "gate report is for a different PR or head")
    base_gate = gate.get("base_gate")
    if not isinstance(base_gate, Mapping) or base_gate.get("expected_base_sha") != base:
        return bad("binding_mismatch", "gate report is for a different base")
    if profile is not None and gate.get("review_policy") != profile.review_policy:
        return bad("binding_mismatch", "gate report used a different review policy")
    if gate.get("reasons") != errors or not errors:
        return bad("malformed_report", "errors do not equal the gate's refusal reasons")
    reported = gate.get("path_gate")
    if not isinstance(reported, Mapping) or _reported_gate_view(reported) != dict(path_gate):
        return bad("binding_mismatch", "gate path decision differs from the package paths")
    return {
        "status": "report_ok",
        "decision": "receipt_preflight_failed",
        "observed_refusals": list(errors),
        "driver_identity": "unverified",
    }


def _reported_gate_view(reported: Mapping[str, Any]) -> dict[str, Any] | None:
    blocked = reported.get("blocked_paths")
    unmatched = reported.get("unmatched_paths")
    if not isinstance(blocked, list) or not isinstance(unmatched, list):
        return None
    if not all(isinstance(p, str) for p in [*blocked, *unmatched]):
        return None
    return {
        "allowed": reported.get("allowed"),
        "reason": reported.get("reason"),
        "blocked_paths": sorted(blocked),
        "unmatched_paths": sorted(unmatched),
    }


def _validate_package(package: Any) -> tuple[list[Mapping[str, Any]], dict[str, str]]:
    if not isinstance(package, Mapping):
        raise ValueError("package must be an object")
    if package.get("schema") != PACKAGE_SCHEMA:
        raise ValueError(f"package schema must be {PACKAGE_SCHEMA}")
    driver = package.get("driver")
    if not isinstance(driver, Mapping):
        raise ValueError("driver must be an object")
    commit = driver.get("source_commit")
    manifest = driver.get("manifest_sha256")
    if type(commit) is not str or not SHA_RE.fullmatch(commit):
        raise ValueError("driver.source_commit must be a 40-char lowercase sha")
    if type(manifest) is not str or not DIGEST_RE.fullmatch(manifest.upper()):
        raise ValueError("driver.manifest_sha256 must be a 64-char hex digest")
    prs = package.get("prs")
    if not isinstance(prs, list) or not prs:
        raise ValueError("prs must be a non-empty list")
    seen: set[int] = set()
    for entry in prs:
        if not isinstance(entry, Mapping):
            raise ValueError("each pr entry must be an object")
        number = entry.get("pr")
        if type(number) is not int or number < 1 or number in seen:
            raise ValueError("pr must be a unique positive integer")
        seen.add(number)
        for field in ("head", "base"):
            value = entry.get(field)
            if type(value) is not str or not SHA_RE.fullmatch(value):
                raise ValueError(f"pr {number}: {field} must be a 40-char lowercase sha")
        paths = entry.get("changed_paths")
        if (
            not isinstance(paths, list)
            or not paths
            or not all(type(p) is str and p.strip() == p and p for p in paths)
        ):
            raise ValueError(f"pr {number}: changed_paths must be non-empty exact strings")
        holder = entry.get("rco_slot_holder")
        if type(holder) is not str or not AGENT_ID_RE.fullmatch(holder):
            raise ValueError(f"pr {number}: rco_slot_holder must be an exact agent id")
    return list(prs), {"source_commit": commit, "manifest_sha256": manifest.upper()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--charter", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        package = json.loads(args.package.read_text(encoding="utf-8"))
        report = check_package(package, charter_path=args.charter)
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": REPORT_SCHEMA, "verdict": "invalid_input",
                          "error": str(exc)}, sort_keys=True))
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"{report['verdict']} (driver bound: {report['driver']['bound']})")
        for result in report["prs"]:
            print(
                f"- #{result['pr']} {result['route']}: {result['no_apply']['status']}"
                f" -> {result['verdict']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
