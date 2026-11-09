"""Re-derive Git content identity for Rule 9a review carry-forward.

This evidence proves content identity only. It grants no approval, CI result,
signature, merge, or runtime authority. Callers must re-evaluate the original
bridge identities, scope and vetoes and require CI at the proposed head.

Known resource limit: _MAX_BYTES bounds accepted evidence after capture; PIPE
capture does not bound peak stdout memory. The command timeout remains separate.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Mapping

from tools import bridge_rco_enrollment as _trusted_paths

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MAX_BYTES = 32 * 1024 * 1024
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_DIFF_FLAGS = (
    "--no-color", "--no-ext-diff", "--no-textconv", "--binary", "--full-index",
    "--no-renames", "--no-relative", "--src-prefix=a/", "--dst-prefix=b/",
    "--ignore-submodules=none", "--diff-algorithm=myers", "--no-indent-heuristic",
    "--unified=3", "--inter-hunk-context=0", "--word-diff=none", "--submodule=short",
    "--output-indicator-new=+", "--output-indicator-old=-", "--output-indicator-context= ",
    "-O" + os.devnull,
)
_CONFIG = (
    "core.autocrlf=false", "core.fsmonitor=false", "core.quotePath=true",
    "core.attributesFile=" + os.devnull, "diff.noprefix=false",
    "diff.mnemonicPrefix=false", "color.ui=false", "diff.external=",
    "diff.submodule=short", "diff.suppressBlankEmpty=false", "diff.orderFile=" + os.devnull,
)


def _file_bytes(path: Path, limit: int) -> tuple[bytes, tuple]:
    """Use the existing no-reparse validator and compare handle/path identities."""
    _trusted_paths._no_reparse(path)
    identity = lambda s: (s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode), s.st_size,
                          s.st_mtime_ns, getattr(s, "st_file_attributes", 0),
                          getattr(s, "st_reparse_tag", 0))
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise ValueError("identity authority file is nonregular, empty or oversized")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after_read = os.fstat(stream.fileno())
    after = path.lstat()
    if (len(raw) != before.st_size or len(raw) > limit
            or any(identity(s) != identity(before) for s in (opened, after_read, after))):
        raise ValueError("identity authority file changed during read")
    _trusted_paths._no_reparse(path)
    return raw, identity(before)


def _native_git_snapshot(path: Path) -> dict[str, Any]:
    raw, identity = _file_bytes(path, 64 * 1024 * 1024)
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), identity=identity)


def _fleet_git_authority() -> tuple[Path, dict, dict]:
    """Existing external manifest -> pinned fleet -> configured physical path.

    The executable digest is measured consistency, not an external digest pin;
    this does not authenticate or hash-pin Git's DLL/helper chain.
    """
    try:
        root_value = os.environ.get("WD_BRIDGE_BUNDLE_ROOT")
        pin = os.environ.get("WD_REBOOT_EXPECTED_MANIFEST_HASH")
        if (type(root_value) is not str or not root_value
                or type(pin) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", pin) is None):
            raise ValueError("Git identity needs existing external manifest authority")
        root = Path(root_value)
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("Git bundle authority root must be absolute")
        raw = _trusted_paths._manifest_bytes(root)
        if hashlib.sha256(raw).hexdigest() != pin.lower():
            raise ValueError("Git authority manifest hash mismatch")
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_trusted_paths._pairs,
                              parse_constant=_trusted_paths._constant)
        if (type(manifest) is not dict or type(manifest.get("source_commit")) is not str
                or _SHA.fullmatch(manifest["source_commit"]) is None
                or root.name.lower() != manifest["source_commit"]):
            raise ValueError("Git authority manifest generation mismatch")
        pins = manifest.get("files")
        fleet_pin = pins.get("wd-fleet.json") if type(pins) is dict else None
        if type(fleet_pin) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", fleet_pin) is None:
            raise ValueError("Git authority fleet pin missing")
        fleet_raw, _ = _file_bytes(root / "wd-fleet.json", 1024 * 1024)
        if hashlib.sha256(fleet_raw).hexdigest() != fleet_pin.lower():
            raise ValueError("Git authority fleet hash mismatch")
        fleet = json.loads(fleet_raw.decode("utf-8"), object_pairs_hook=_trusted_paths._pairs,
                           parse_constant=_trusted_paths._constant)
        if (type(fleet) is not dict or type(fleet.get("schema_version")) is not int
                or fleet["schema_version"] != 2 or type(fleet.get("git_executable")) is not str):
            raise ValueError("Git authority fleet is invalid")
        path = Path(fleet["git_executable"])
        if (not path.is_absolute() or ".." in path.parts
                or (os.name == "nt" and path.suffix.lower() != ".exe")):
            raise ValueError("Git executable authority path must be absolute")
        snapshot = _native_git_snapshot(path)
        authority = dict(manifest_sha256=pin.lower(), fleet_sha256=fleet_pin.lower(),
                         generation=manifest["source_commit"])
        return path, snapshot, authority
    except (OSError, UnicodeError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Git executable authority unavailable") from exc


def _git_reader(repo_root: Path):
    if not isinstance(repo_root, Path) or not repo_root.is_dir():
        raise ValueError("content identity requires an explicit repository")
    root = repo_root.resolve(strict=True)
    executable, native_snapshot, authority = _fleet_git_authority()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1",
               LC_ALL="C", LANG="C")
    command = [str(executable), "--no-replace-objects", "--attr-source=" + _EMPTY_TREE]
    for setting in _CONFIG:
        command += ["-c", setting]

    def read(*args: str, data: bytes | None = None) -> bytes:
        try:
            if _native_git_snapshot(executable) != native_snapshot:
                raise ValueError("Git executable identity changed before command")
            result = subprocess.run(command + list(args), cwd=root, env=env, input=data,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("content identity Git read unavailable") from exc
        if _native_git_snapshot(executable) != native_snapshot:
            raise ValueError("Git executable identity changed during command")
        if result.returncode or len(result.stdout) > _MAX_BYTES:
            raise ValueError("content identity Git read refused")
        return result.stdout

    try:
        actual = Path(os.fsdecode(read("rev-parse", "--show-toplevel").strip())).resolve(strict=True)
        if actual != root or read("rev-parse", "--is-shallow-repository").strip() != b"false":
            raise ValueError("content identity repository is incomplete")
        graft_path = Path(os.fsdecode(read("rev-parse", "--git-path", "info/grafts").strip()))
        if not graft_path.is_absolute():
            graft_path = root / graft_path
        if graft_path.exists() and graft_path.stat().st_size:
            raise ValueError("content identity grafted history refused")
    except (OSError, UnicodeError) as exc:
        raise ValueError("content identity repository unavailable") from exc
    read.authority = (executable, native_snapshot, authority)
    read.git_prefix = tuple(command[1:])
    return read


def _merge_base(read, base: str, head: str) -> str:
    candidates = read("merge-base", "--all", base, head).splitlines()
    if len(candidates) != 1:
        raise ValueError("content identity requires one unambiguous merge-base")
    result = candidates[0].decode("ascii", "strict")
    if _SHA.fullmatch(result) is None:
        raise ValueError("invalid merge-base identity")
    return result


def _entries(raw: bytes) -> list[dict[str, str]]:
    parts = raw.split(b"\0")
    if not parts or parts.pop() != b"" or len(parts) % 2:
        raise ValueError("invalid raw Git path identity")
    entries = []
    for i in range(0, len(parts), 2):
        fields = parts[i].split()
        if len(fields) != 5 or not fields[0].startswith(b":") or not parts[i + 1]:
            raise ValueError("invalid raw Git path identity")
        old_mode, new_mode, old_blob, new_blob, status = [f.decode("ascii", "strict") for f in fields]
        old_mode = old_mode[1:]
        if (not re.fullmatch(r"[0-7]{6}", old_mode) or not re.fullmatch(r"[0-7]{6}", new_mode)
                or _SHA.fullmatch(old_blob) is None or _SHA.fullmatch(new_blob) is None
                or status not in {"A", "M", "D", "T"}):
            raise ValueError("invalid raw Git path identity")
        entries.append(dict(path_hex=parts[i + 1].hex(), old_mode=old_mode, new_mode=new_mode,
                            old_blob=old_blob, new_blob=new_blob, status=status))
    return entries


def derive_content_identity(*, repo_root: Path, old_base: str, old_head: str,
                            new_base: str, new_head: str) -> dict[str, Any]:
    """Read immutable Git objects; return identity evidence or refuse.

    Both canonical raw diff bytes AND stable patch-id AND changed path/mode/blob
    tuples must match. A patch-id alone intentionally ignores whitespace and
    cannot authorize carry. Missing objects, empty diffs and ambiguous histories
    never receive an identity proof. Git configuration cannot change diff flags.
    """
    values = dict(old_base=old_base, old_head=old_head, new_base=new_base, new_head=new_head)
    if any(type(value) is not str or _SHA.fullmatch(value) is None for value in values.values()):
        raise ValueError("content identity requires exact 40-hex Git objects")
    read = _git_reader(repo_root)
    for value in values.values():
        # Existence/type of the object, not a ref name or a caller assertion.
        if read("cat-file", "-t", value).strip() != b"commit":
            raise ValueError("content identity requires commit objects")
    old_mb = _merge_base(read, old_base, old_head)
    new_mb = _merge_base(read, new_base, new_head)
    old_diff = read("diff", *_DIFF_FLAGS, old_mb, old_head, "--")
    new_diff = read("diff", *_DIFF_FLAGS, new_mb, new_head, "--")
    if not old_diff or not new_diff:
        raise ValueError("empty diff has no carry identity")
    old_raw = read("diff-tree", "-r", "--raw", "--no-abbrev", "-z", "--no-renames", old_mb, old_head, "--")
    new_raw = read("diff-tree", "-r", "--raw", "--no-abbrev", "-z", "--no-renames", new_mb, new_head, "--")
    old_entries, new_entries = _entries(old_raw), _entries(new_raw)
    if old_diff != new_diff or not old_entries or old_entries != new_entries:
        raise ValueError("diff bytes or path/mode/blob content identity differs")

    def patch_id(raw):
        lines = read("patch-id", "--stable", data=raw).splitlines()
        if len(lines) != 1 or re.fullmatch(rb"[0-9a-f]{40} [0-9a-f]{40}", lines[0]) is None:
            raise ValueError("empty or ambiguous stable patch identity")
        return lines[0].split()[0].decode("ascii")

    old_patch, new_patch = patch_id(old_diff), patch_id(new_diff)
    if old_patch != new_patch:
        raise ValueError("stable patch content identity differs")
    executable, native, authority = read.authority
    if _fleet_git_authority() != read.authority:
        raise ValueError("Git executable or authority changed during proof")
    return dict(schema="wd.bridge-content-identity.v1", **values,
                git_executable_path=str(executable), git_executable_sha256=native["sha256"],
                git_executable_trust="anchored_fleet_path_measured_sha256", git_authority=authority,
                diff_argv=[*read.git_prefix, "diff", *_DIFF_FLAGS],
                old_merge_base=old_mb, new_merge_base=new_mb,
                diff_flags=list(_DIFF_FLAGS), diff_bytes_b64=base64.b64encode(old_diff).decode("ascii"),
                old_diff_sha256=hashlib.sha256(old_diff).hexdigest(),
                new_diff_sha256=hashlib.sha256(new_diff).hexdigest(),
                old_patch_id=old_patch, new_patch_id=new_patch,
                old_entries=old_entries, new_entries=new_entries)


def verify_content_identity(*, proof: Mapping[str, Any], repo_root: Path,
                            old_base: str, old_head: str, new_base: str, new_head: str) -> dict[str, Any]:
    """Recompute every field from Git, including bytes; never trust stored flags."""
    current = derive_content_identity(repo_root=repo_root, old_base=old_base, old_head=old_head,
                                      new_base=new_base, new_head=new_head)
    if not isinstance(proof, Mapping) or dict(proof) != current:
        raise ValueError("content identity evidence failed re-derivation")
    return current


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()


def _side(value: Any) -> dict:
    _require(isinstance(value, Mapping), "carry side is missing")
    for field in ("head", "base_sha"):
        _require(type(value.get(field)) is str and _SHA.fullmatch(value[field]) is not None,
                 "carry side requires exact commit identities")
    _require(type(value.get("pr_number")) is int and value["pr_number"] > 0, "carry PR is invalid")
    _require(type(value.get("repository")) is str
             and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value["repository"]) is not None,
             "carry repository is invalid")
    _require(type(value.get("base_ref")) is str and bool(value["base_ref"])
             and value["base_ref"] == value["base_ref"].strip(), "carry base ref is invalid")
    return {key: value[key] for key in ("head", "base_sha", "pr_number", "repository", "base_ref")}


def _ci_at_proposed_head(ci: Any, head: str) -> list[dict]:
    _require(isinstance(ci, Mapping), "carry CI evidence is missing")
    required, runs = ci.get("required"), ci.get("runs")
    _require(type(required) is list and bool(required)
             and all(type(name) is str and bool(name) and name == name.strip() for name in required)
             and len(set(required)) == len(required), "carry required CI inventory is empty or ambiguous")
    _require(type(runs) is list and all(isinstance(run, Mapping) for run in runs), "carry CI runs are invalid")
    result = []
    for name in sorted(required):
        matches = [run for run in runs if run.get("name") == name]
        _require(len(matches) == 1, "carry required CI check is missing or duplicated")
        run = matches[0]
        _require(run.get("head") == head and type(run.get("status")) is str
                 and run["status"].lower() == "completed" and type(run.get("conclusion")) is str
                 and run["conclusion"].lower() == "success", "carry CI is not green at proposed head")
        result.append(dict(head=head, name=name, status="completed", conclusion="success"))
    return result


_HEAD_FIELDS = ("head", "head_sha", "expected_head", "exact_head", "head_oid", "head_commit")
_CARRY_APPROVAL_ROLES = ("build_lead", "build_tools", "rco")


def _structured_heads(event: Mapping) -> set[str]:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return set()
    return {value.strip().lower() for key in _HEAD_FIELDS
            if type(value := payload.get(key)) is str and _SHA.fullmatch(value.strip().lower())}


def _review_control_scan(events: list, *, task_id: str, pr_number: int, author: str,
                         old_head: str, new_head: str, registry: dict, rcos: tuple) -> dict:
    """Use existing taxonomy, identity and scope rules; preserve append order.

    A clear is scoped to its own identity and the same head. A recognized RCO's
    unverified veto still blocks. Its clear is not a replacement RCO_PASS.
    """
    from tools import check_bridge_changes_requested as peer
    from tools import check_rco_pass_present as rco_gate
    from tools.bridge_rco_enrollment import rco_event_enrollment_matches
    from waggledance.core.bridge_identity_registry import bridge_identity_binding_status

    pending: dict[tuple[str, str], int] = {}
    last_rco_veto: dict[str, int] = {}
    controls = []
    watched = set(rcos) | {"codex-lead-1", "codex-tools-1"}
    for index, event in enumerate(events):
        agent = event.get("agent")
        if agent not in watched:
            continue
        heads = _structured_heads(event)
        canonical_scope = peer._event_matches_scope(event, task_id=task_id, pr_number=pr_number,
                                                    author_agent=author)
        if not canonical_scope and not heads.intersection({old_head, new_head}):
            continue
        scopes = heads or {"canonical_task"}
        status, typ = str(event.get("status", "")).lower(), str(event.get("type", "")).lower()
        binding = bridge_identity_binding_status(event, registry=registry, restricted_agents=watched)
        typed_block = typ in peer._TAXONOMY_BLOCK_BY_TYPE and (
            typ not in peer._TAXONOMY_RCO_GATED_TYPES
            or (agent in rcos and not peer._is_informational_finding_status(status)))
        block = typed_block or peer._is_blocking_status(status, event_type=typ) or (
            agent in rcos and rco_gate._is_rco_veto_event(event))
        if block and (binding == "valid" or agent in rcos):
            for scope in scopes:
                pending[(agent, scope)] = index
            if agent in rcos:
                last_rco_veto[agent] = index
            controls.append(dict(index=index, agent=agent, scopes=sorted(scopes), action="block",
                                 event_sha256=_canonical_sha(event), identity_binding=binding))
            continue
        if binding != "valid":
            continue
        if agent == "claude-rco-3" and not rco_event_enrollment_matches(event):
            continue
        clear = typ in peer.RCO_RETRACTION_EVENT_TYPES and peer._is_clear_status(status)
        if agent not in rcos:
            # Existing build approvals can replace that build identity's block;
            # a transferred RCO_PASS never acts as an explicit veto retraction.
            clear = clear or (typ in {"decision", "rco_review"} and status in peer.APPROVAL_STATUSES)
        if clear:
            for scope in scopes:
                pending.pop((agent, scope), None)
            controls.append(dict(index=index, agent=agent, scopes=sorted(scopes), action="clear",
                                 event_sha256=_canonical_sha(event), identity_binding=binding))
    return dict(pending=[dict(agent=agent, scope=scope, index=index)
                         for (agent, scope), index in sorted(pending.items())],
                last_rco_veto=last_rco_veto, controls=controls)


def evaluate_approval_carry_forward(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Re-derive Rule 9a carry evidence, never a merge authorization.

    Production callers must construct the queue, original PR tuple and current
    required-CI observations from their verified readers/snapshots. This pure
    evaluator cannot authenticate a caller's dict. Merge/receipt consumers must
    independently reconstruct those observations, not forward an input ok flag.
    """
    refused = dict(carry=False, carried=[], receipt=None, authority="evidence_only")
    try:
        from tools.idle_consensus_auto_merge import verify_bridge_consensus
        from tools.check_rco_pass_present import DEFAULT_RCO_AGENTS
        from waggledance.core.bridge_identity_registry import load_bridge_identity_registry

        _require(isinstance(evidence, Mapping)
                 and evidence.get("schema") == "wd.bridge-approval-carry-forward-evidence.v1",
                 "carry evidence schema is invalid")
        old, new = _side(evidence.get("approved")), _side(evidence.get("proposed"))
        _require(all(old[key] == new[key] for key in ("pr_number", "repository", "base_ref")),
                 "carry origin is not the same PR/repository/base ref")
        _require(old["head"] != new["head"], "exact same head needs no carry")
        task, author = evidence.get("task_id"), evidence.get("pr_author")
        _require(type(task) is str and bool(task) and task == task.strip(), "carry task is invalid")
        registry = load_bridge_identity_registry()
        _require(evidence.get("roster") == registry, "carry roster is not the canonical registry")
        _require(type(author) is str and author in registry, "carry author is not canonical")
        requested = evidence.get("recognized_rco_agents")
        _require(type(requested) is list and bool(requested)
                 and all(type(agent) is str and agent in DEFAULT_RCO_AGENTS for agent in requested),
                 "carry recognized RCO set is invalid")
        # A caller cannot remove a canonical RCO's veto by supplying a subset.
        rcos = tuple(DEFAULT_RCO_AGENTS)
        events = evidence.get("events")
        _require(type(events) is list and all(isinstance(event, Mapping) for event in events),
                 "carry accepted events are invalid")
        _require(evidence.get("accepted_queue_complete") is True, "carry accepted queue is incomplete")
        ci_checks = _ci_at_proposed_head(evidence.get("ci"), new["head"])
        scan = _review_control_scan(events, task_id=task, pr_number=old["pr_number"], author=author,
                                    old_head=old["head"], new_head=new["head"], registry=registry, rcos=rcos)
        _require(not scan["pending"], "review veto/block survives carry")
        original = verify_bridge_consensus(events=events, task_id=task, head_sha=old["head"],
                                           pr_number=old["pr_number"], author_agent=author)
        _require(original.get("ok") is True, "original head has no valid Rule9a consensus")
        carried = []
        for role in _CARRY_APPROVAL_ROLES:
            info = original["identities"][role]
            if info.get("build_author_slot_waived") is True:
                _require(info.get("build_author_slot_waiver_satisfied") is True, "author waiver is blocked")
                continue
            index = info.get("approval_index")
            _require(type(index) is int and 0 <= index < len(events), "original approval index is missing")
            event = events[index]
            _require(event.get("agent") != author and info.get("approved") is True,
                     "author or unapproved slot cannot carry authority")
            payload = event.get("payload")
            if isinstance(payload, Mapping):
                for key in ("pr", "pr_number", "pull_request", "pull_request_number"):
                    if key in payload:
                        _require((type(payload[key]) is int and payload[key] == old["pr_number"])
                                 or (type(payload[key]) is str and payload[key] == str(old["pr_number"])),
                                 "original approval has conflicting PR binding")
                for key in ("repository", "repo"):
                    if key in payload:
                        _require(payload[key] == old["repository"], "original approval has conflicting repository")
            structured = _structured_heads(event)
            _require(not structured or structured == {old["head"]}, "original approval head is ambiguous")
            _require(not (new["head"] in str(event.get("message", "")).lower() and not structured),
                     "prose mentioning both heads is not a carry binding")
            if role == "rco":
                _require(scan["last_rco_veto"].get(event["agent"], -1) < index,
                         "a clear did not reissue this RCO approval after its veto")
            carried.append(dict(agent=event["agent"], agent_uuid=event["agent_uuid"],
                                kind="rco_pass" if role == "rco" else "build_consensus",
                                approved_head=old["head"], event_sha256=_canonical_sha(event)))
        _require(len({row["event_sha256"] for row in carried}) == len(carried),
                 "one original event cannot fill two approval slots")
        root = evidence.get("repo_path")
        _require(type(root) is str and bool(root), "carry repository path is invalid")
        proof = derive_content_identity(repo_root=Path(root), old_base=old["base_sha"], old_head=old["head"],
                                        new_base=new["base_sha"], new_head=new["head"])
        receipt = dict(schema="wd.bridge-approval-carry-forward-receipt.v1",
                       repository=old["repository"], pr_number=old["pr_number"], base_ref=old["base_ref"],
                       task_id=task, pr_author=author,
                       approved_head=old["head"], proposed_head=new["head"],
                       approved_base_sha=old["base_sha"], proposed_base_sha=new["base_sha"],
                       approved_merge_base=proof["old_merge_base"], proposed_merge_base=proof["new_merge_base"],
                       diff_argv=proof["diff_argv"], approved_diff_sha256=proof["old_diff_sha256"],
                       proposed_diff_sha256=proof["new_diff_sha256"], patch_id=proof["old_patch_id"],
                       content_identity=proof, approval_event_sha256=[row["event_sha256"] for row in carried],
                       carried=carried, veto_scan_complete=True, veto_controls=scan["controls"],
                       accepted_event_count=len(events), accepted_events_sha256=_canonical_sha(events),
                       registry_sha256=_canonical_sha(registry),
                       ci_head=new["head"], ci_checks=ci_checks,
                       original_approval_basis_sha256=_canonical_sha(dict(
                           carried=carried,
                           build_author_slot_waivers=original.get("build_author_slot_waivers", []))),
                       build_author_slot_waivers=original.get("build_author_slot_waivers", []))
        return dict(carry=True, reason="content_identical_original_reviews_verified", carried=carried,
                    receipt=receipt, original_verdict=original, authority="evidence_only")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return dict(refused, reason=str(exc))


def verify_carry_forward_receipt(receipt: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    """Keep the recorded prefix immutable; recheck the entire current ledger.

    Nonmaterial appends need no new receipt. Original refs, scoped controls, CI
    and Git must still rederive exactly; no selected approval is silently replaced.
    Diagnostic-only legacy verifier rows are not part of the approval basis.
    """
    if not isinstance(receipt, Mapping) or not isinstance(evidence, Mapping):
        return False
    events, count = evidence.get("events"), receipt.get("accepted_event_count")
    if type(events) is not list or type(count) is not int or not 0 < count <= len(events):
        return False
    try:
        if _canonical_sha(events[:count]) != receipt.get("accepted_events_sha256"):
            return False
    except (ValueError, TypeError):
        return False
    current = evaluate_approval_carry_forward(evidence)
    if current["carry"] is not True:
        return False
    identities = current["original_verdict"]["identities"]
    basis_indexes = [identities[role].get("approval_index") for role in _CARRY_APPROVAL_ROLES
                     if identities[role].get("build_author_slot_waived") is not True]
    basis_indexes.extend(control.get("index") for control in current["receipt"]["veto_controls"])
    # A self-consistent shorter prefix must still contain the entire material
    # basis. A duplicate event digest inside it cannot stand in for the actual
    # selected occurrence outside it. Author waivers contribute no own vote.
    if any(type(index) is not int or not 0 <= index < count for index in basis_indexes):
        return False
    # Only the observation length/hash may grow. The old prefix was verified
    # above; every material field, including veto controls, remains exact.
    expected = dict(current["receipt"], accepted_event_count=count,
                    accepted_events_sha256=receipt["accepted_events_sha256"])
    # Python value equality aliases booleans, integers and floats. Evidence is
    # canonical JSON: a resealed type change is still a material change.
    try:
        return _canonical_sha(dict(receipt)) == _canonical_sha(expected)
    except (ValueError, TypeError):
        return False


def _selected_original_events(original: Mapping, events: list) -> list[Mapping]:
    selected = []
    for role in _CARRY_APPROVAL_ROLES:
        info = original["identities"][role]
        if info.get("build_author_slot_waived") is True:
            _require(info.get("build_author_slot_waiver_satisfied") is True, "original author waiver is blocked")
            continue
        index = info.get("approval_index")
        _require(type(index) is int and 0 <= index < len(events), "original review index is missing")
        selected.append(events[index])
    return selected


def _review_basis(value: Any, *, repo: str, pr_number: int, task_id: str,
                  base_ref: str, old_head: str) -> dict:
    fields = {"schema", "repository", "pr_number", "task_id", "base_ref", "head",
              "base_sha", "merge_base", "diff_sha256", "patch_id"}
    _require(isinstance(value, Mapping) and set(value) == fields, "original reviewed basis is missing or malformed")
    basis = dict(value)
    expected = dict(schema="wd.bridge-reviewed-basis.v1", repository=repo, pr_number=pr_number,
                    task_id=task_id, base_ref=base_ref, head=old_head)
    _require(type(basis["pr_number"]) is int
             and all(basis[key] == item for key, item in expected.items()),
             "original reviewed basis belongs to another PR/task/ref/head")
    for key in ("head", "base_sha", "merge_base", "patch_id"):
        _require(type(basis[key]) is str and _SHA.fullmatch(basis[key]) is not None,
                 "original reviewed basis requires exact commit/patch identities")
    _require(type(basis["diff_sha256"]) is str
             and re.fullmatch(r"[0-9a-f]{64}", basis["diff_sha256"]) is not None,
             "original reviewed content digest is missing")
    return basis


def _bound_review_origin(*, request: Mapping, selected: list, events: list, registry: dict,
                         author: str, repo_root: Path, repo: str, pr_number: int,
                         task_id: str, base_ref: str, old_head: str) -> dict:
    refs = [_canonical_sha(event) for event in selected]
    binding_ref = request.get("origin_binding_event_sha256")
    parse = lambda value: _review_basis(value, repo=repo, pr_number=pr_number, task_id=task_id,
                                        base_ref=base_ref, old_head=old_head)
    if binding_ref is None:
        bases = []
        for event in selected:
            payload = event.get("payload")
            _require(isinstance(payload, Mapping), "original approval has no reviewed basis")
            basis = parse(payload.get("review_basis"))
            _require(payload.get("review_basis_sha256") == _canonical_sha(basis),
                     "original approval reviewed-basis digest does not match")
            bases.append(basis)
        _require(bool(bases) and all(_canonical_sha(basis) == _canonical_sha(bases[0]) for basis in bases),
                 "original approvals disagree on reviewed basis")
        return dict(schema="wd.bridge-bound-review-origin.v1", mode="direct", review_basis=bases[0],
                    approval_event_sha256=refs, authentication="canonical_registered_identity_binding")

    _require(type(binding_ref) is str and re.fullmatch(r"[0-9a-f]{64}", binding_ref) is not None,
             "original origin event ref is invalid")
    matches = [event for event in events if _canonical_sha(event) == binding_ref]
    _require(len(matches) == 1, "original origin event is absent or ambiguous")
    binding = matches[0]
    from waggledance.core.bridge_identity_registry import bridge_identity_binding_status
    issuers = {event["agent"] for event in selected}
    _require(binding.get("agent") in issuers and binding.get("agent") != author
             and bridge_identity_binding_status(binding, registry=registry, restricted_agents=issuers) == "valid"
             and binding.get("task_id") == task_id and binding.get("type") == "decision"
             and binding.get("status") == "review_basis_bound", "original descriptor issuer/binding is not authorized")
    payload = binding.get("payload")
    origin = payload.get("review_origin") if isinstance(payload, Mapping) else None
    _require(isinstance(origin, Mapping) and origin.get("schema") == "wd.bridge-review-origin.v1"
             and origin.get("approval_event_sha256") == refs, "original descriptor review references are not bound")
    basis = parse(origin.get("review_basis"))
    descriptor = origin.get("descriptor")
    _require(isinstance(descriptor, Mapping) and type(descriptor.get("path")) is str
             and type(descriptor.get("bytes")) is int and 0 < descriptor["bytes"] <= 1024 * 1024
             and type(descriptor.get("sha256")) is str
             and re.fullmatch(r"[0-9a-f]{64}", descriptor["sha256"]) is not None,
             "original review descriptor is malformed")
    path = Path(descriptor["path"])
    _require(path.is_absolute() and ".." not in path.parts, "original descriptor path must be absolute")
    _trusted_paths._no_reparse(path)
    roots = [repo_root.resolve(strict=True) / ".codex-audit"]
    runtime = os.environ.get("AGENT_BRIDGE_RUNTIME_ROOT")
    if runtime and Path(runtime).is_dir():
        roots.append(Path(runtime).resolve(strict=True) / "shared")
    _require(any(path.is_relative_to(root) for root in roots), "original descriptor is outside approved artifact roots")
    raw, _ = _file_bytes(path, 1024 * 1024)
    _require(len(raw) == descriptor["bytes"] and hashlib.sha256(raw).hexdigest() == descriptor["sha256"],
             "original review descriptor bytes do not match canonical binding")
    body = json.loads(raw.decode("utf-8"), object_pairs_hook=_trusted_paths._pairs,
                      parse_constant=_trusted_paths._constant)
    _require(isinstance(body, Mapping) and body.get("schema") == "wd.bridge-review-snapshot.v1"
             and _canonical_sha(body.get("review_basis")) == _canonical_sha(basis)
             and body.get("approval_event_sha256") == refs,
             "original review snapshot does not bind the reviewed basis and approvals")
    # Each original voter commits to the same basis. The descriptor supplies
    # the preimage and references; its issuer cannot vouch for another voter.
    for event in selected:
        fields = event.get("payload", {})
        _require(isinstance(fields, Mapping)
                 and fields.get("review_basis_sha256") == _canonical_sha(basis),
                 "original approval has no matching reviewed-basis commitment")
        if "review_basis" in fields:
            _require(_canonical_sha(fields["review_basis"]) == _canonical_sha(basis),
                     "original descriptor contradicts an approval's direct reviewed basis")
    return dict(schema="wd.bridge-bound-review-origin.v1", mode="descriptor", review_basis=basis,
                approval_event_sha256=refs, binding_event_sha256=binding_ref, descriptor=dict(descriptor),
                authentication="canonical_registered_identity_binding")


def derive_live_carry_forward(*, request: Mapping, events_path: Path, repo: str, pr_number: int,
                              task_id: str, expected_head: str, expected_base_sha: str,
                              runner=None, accepted_queue_checker=None) -> dict:
    """Build carry evidence from the real readers, never caller queue/CI flags.

    Runner/checker are the existing test seams. The default path uses the real
    accepted-queue preflight, full ledger and strict github.com snapshot reader.
    Registered bridge identity binding is not a cryptographic signature. This
    function proves review inputs; final merge/receipt policy remains separate.
    """
    try:
        from tools.idle_consensus_auto_merge import (
            _accepted_queue_preflight, _read_bridge_events, _pr_author_resolution, verify_bridge_consensus,
        )
        from tools.pr_status_snapshot import build_pr_status_snapshot
        from waggledance.core.bridge_identity_registry import load_bridge_identity_registry
        from tools.check_rco_pass_present import (
            DEFAULT_RCO_AGENTS, check_rco_pass_present, _is_qualifying_rco_pass,
        )

        _require(isinstance(request, Mapping), "carry request must be an object")
        old_head = request.get("approved_head")
        _require(type(old_head) is str and _SHA.fullmatch(old_head) is not None,
                 "carry request needs the exact approved head")
        _require(isinstance(events_path, Path), "carry needs the canonical events path")
        queue = _accepted_queue_preflight(events_path=events_path, checker=accepted_queue_checker)
        _require(queue.get("ok") is True and queue.get("complete") is True,
                 "carry accepted queue is incomplete")
        events = _read_bridge_events(events_path)
        snapshot = build_pr_status_snapshot(pr_number=pr_number, repo=repo, expected_base_sha=expected_base_sha,
                                            require_ci_evidence=True, runner=runner)
        _require(snapshot["head_sha"] == expected_head and snapshot["base_sha"] == snapshot["base_tip_sha"],
                 "carry proposed snapshot head/base is stale")
        ci = snapshot["ci_evidence"]
        _require(ci["head_sha"] == expected_head and ci["all_required_success"] is True,
                 "carry required CI is not green at proposed head")
        resolution = _pr_author_resolution(snapshot, task_id, events=events,
                                          expected_head_sha=expected_head, expected_base_sha=snapshot["base_sha"])
        _require(resolution.get("ok") is True, "carry PR author is not bound to the current snapshot")
        author = resolution["author_agent"]
        original = verify_bridge_consensus(events=events, task_id=task_id, head_sha=old_head,
                                           pr_number=pr_number, author_agent=author)
        _require(original.get("ok") is True, "original head has no valid Rule9a consensus")
        selected = _selected_original_events(original, events)
        rco = check_rco_pass_present(events=events, task_id=task_id, head=old_head,
                                     rco_agent=DEFAULT_RCO_AGENTS, author_agent=author)
        _require(rco.get("ok") is True, "original head has no qualifying exact-head RCO_PASS")
        _require(all(_is_qualifying_rco_pass(event, old_head, event["agent"])
                     for event in selected if event["agent"] in DEFAULT_RCO_AGENTS),
                 "selected original RCO approval lacks the required exact-head binding")
        root_value = request.get("repo_root")
        _require(isinstance(root_value, (str, Path)) and bool(str(root_value)), "carry repository root is missing")
        root = Path(root_value)
        git = _git_reader(root)
        remote = git("config", "--get", "remote.origin.url").decode("utf-8", "strict").strip().casefold()
        _require(remote in {f"https://github.com/{repo}.git".casefold(), f"https://github.com/{repo}".casefold(),
                            f"git@github.com:{repo}.git".casefold()}, "carry Git origin does not match target repository")
        registry = load_bridge_identity_registry()
        origin = _bound_review_origin(request=request, selected=selected, events=events, registry=registry,
                                      author=author, repo_root=root, repo=repo, pr_number=pr_number,
                                      task_id=task_id, base_ref=snapshot["base_ref"], old_head=old_head)
        basis = origin["review_basis"]
        for hint, actual in (("approved_base_sha", basis["base_sha"]), ("proposed_base_sha", snapshot["base_sha"])):
            _require(hint not in request or request[hint] == actual, "carry base hint conflicts with authenticated source")
        side = dict(repository=repo, pr_number=pr_number, base_ref=snapshot["base_ref"])
        # A JSON pair is an unambiguous check key even if two apps share a name.
        check_key = lambda name, app: json.dumps([name, app], separators=(",", ":"), ensure_ascii=True)
        evidence = dict(schema="wd.bridge-approval-carry-forward-evidence.v1", repo_path=str(root),
                        task_id=task_id, pr_author=author, roster=registry, recognized_rco_agents=list(DEFAULT_RCO_AGENTS),
                        approved=dict(side, head=old_head, base_sha=basis["base_sha"]),
                        proposed=dict(side, head=snapshot["head_sha"], base_sha=snapshot["base_sha"]),
                        events=events, accepted_queue_complete=True,
                        ci=dict(required=[check_key(row["name"], row["app_id"]) for row in ci["required"]],
                                runs=[dict(name=check_key(row["name"], row["app_id"]), head=row["head_sha"],
                                           status=row["status"], conclusion=row["conclusion"]) for row in ci["runs"]]))
        result = evaluate_approval_carry_forward(evidence)
        _require(result["carry"] is True, result["reason"])
        proof = result["receipt"]["content_identity"]
        _require(basis["merge_base"] == proof["old_merge_base"]
                 and basis["diff_sha256"] == proof["old_diff_sha256"] and basis["patch_id"] == proof["old_patch_id"],
                 "authenticated original reviewed content does not match Git")
        paths = {bytes.fromhex(entry["path_hex"]).decode("utf-8", "strict") for entry in proof["new_entries"]}
        _require(paths == set(snapshot["changed_paths"]), "carry snapshot paths do not match actual Git change set")
        return dict(result, authority="live_review_inputs_derived", review_origin=origin, ci_evidence=ci,
                    _fresh_snapshot=snapshot, _events=events, _evidence=evidence)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return dict(carry=False, reason=str(exc), carried=[], receipt=None, authority="no_carry")


def carry_forward_receipt_material(derived: Mapping) -> dict:
    """Serialize review evidence; this operation alone grants no authority."""
    _require(isinstance(derived, Mapping) and derived.get("carry") is True,
             "carry material needs a successful live derivation")
    return deepcopy(dict(schema="wd.bridge-carry-forward-merge-proof.v1",
                         approval_receipt=derived["receipt"], review_origin=derived["review_origin"],
                         ci_evidence=derived["ci_evidence"]))


def verify_live_carry_forward_receipt(*, material: Mapping, request: Mapping, events_path: Path,
                                     repo: str, pr_number: int, task_id: str, expected_head: str,
                                     expected_base_sha: str, runner=None, accepted_queue_checker=None) -> dict:
    """Re-read all authorities; an offline integrity flag never authorizes carry.

    Only the CI reader's observation timestamp is diagnostic. Check identities,
    requirements, runs, response digests and the original reviewed basis remain
    exact. The recorded event prefix may grow only by nonmaterial appends.
    """
    try:
        _require(isinstance(material, Mapping) and set(material) == {
            "schema", "approval_receipt", "review_origin", "ci_evidence"}
            and material["schema"] == "wd.bridge-carry-forward-merge-proof.v1",
            "carry merge proof is missing or malformed")
        fresh = derive_live_carry_forward(request=request, events_path=events_path, repo=repo,
                                         pr_number=pr_number, task_id=task_id, expected_head=expected_head,
                                         expected_base_sha=expected_base_sha, runner=runner,
                                         accepted_queue_checker=accepted_queue_checker)
        _require(fresh.get("carry") is True, "fresh carry refused: " + fresh["reason"])
        _require(verify_carry_forward_receipt(material["approval_receipt"], fresh["_evidence"]),
                 "carry approval receipt does not rederive from current authorities")
        _require(_canonical_sha(material["review_origin"]) == _canonical_sha(fresh["review_origin"]),
                 "carry original reviewed basis or approval binding changed")
        old_ci = material["ci_evidence"]
        _require(isinstance(old_ci, Mapping), "carry CI proof is malformed")
        ci_material = lambda value: {key: item for key, item in value.items() if key != "observed_at_utc"}
        _require(_canonical_sha(ci_material(old_ci)) == _canonical_sha(ci_material(fresh["ci_evidence"])),
                 "carry required exact-head CI evidence changed")
        return dict(ok=True, decision="carry_merge_proof_rederived", material=deepcopy(dict(material)),
                    _fresh=fresh, authority="review_proof_only_no_merge")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return dict(ok=False, decision="carry_merge_proof_refused", reasons=[str(exc)], authority="none")
