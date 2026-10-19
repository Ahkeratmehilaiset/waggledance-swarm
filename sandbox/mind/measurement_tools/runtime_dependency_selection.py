"""Pure preparation candidate. No filesystem, installed availability or imports of packages.

Inputs are normalized dependency/RECORD metadata, not raw Requires-Dist strings.
The caller must verify actual metadata/RECORD/evidence bytes and approve proof
digests against the frozen binding before calling. Digest equality here is NOT
authentication of an issuer or evidence semantics. No version-range solver,
runtime pin map, runtime readiness or dynamic-closure guarantee is provided.
"""
import ast
import hashlib
import json
import re


class SelectionRefused(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def need(ok, code):
    if not ok:
        raise SelectionRefused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_value(value):
    return hashlib.sha256((canonical(value) + "\n").encode()).hexdigest()


def hexdigest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def name(value):
    need(isinstance(value, str) and len(value) <= 128
         and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", value), "MALFORMED_NAME")
    return re.sub("[-_.]+", "-", value).lower()


def sequence(value, code, limit=100000):
    need(isinstance(value, (list, tuple)) and len(value) <= limit, code)
    return list(value)


def shape(value, fields, code):
    need(isinstance(value, dict) and set(value) == set(fields), code)


def record_path(value):
    need(isinstance(value, str) and 0 < len(value) <= 2048
         and not any(ord(c) < 32 for c in value)
         and not any(c in value for c in "\\:*?\"<>|")
         and all(p not in ("", ".", "..") for p in value.split("/")), "MALFORMED_RECORD_PATH")
    return value


def module_name(value):
    need(isinstance(value, str) and len(value) <= 256 and re.fullmatch(
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", value, re.ASCII), "MALFORMED_MODULE")
    return value


def record_identity(path, windows):
    # Conservative metadata comparison only; never a physical filesystem claim.
    return path.casefold() if windows else path


def owns(module, path, windows=False):
    base = record_identity(module.replace(".", "/"), windows)
    path = record_identity(path, windows)
    return path in (base + ".py", base + "/__init__.py") or re.fullmatch(
        re.escape(base) + r"(?:\.[A-Za-z0-9_-]+)*\.(?:pyd|so)", path) is not None


def marker_active(text, environment, extra):
    """Explicit small AST subset; unsupported/missing context refuses, never includes."""
    if not text:
        return True
    need(len(text) <= 512, "UNKNOWN_MARKER")
    values = dict(environment, extra=extra)
    versions = {"python_version", "python_full_version", "implementation_version"}
    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        raise SelectionRefused("UNKNOWN_MARKER")
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.BoolOp):
            outcomes = [walk(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(outcomes)
            if isinstance(node.op, ast.Or):
                return any(outcomes)
            raise SelectionRefused("UNKNOWN_MARKER")
        need(isinstance(node, ast.Compare) and len(node.ops) == 1, "UNKNOWN_MARKER")
        left, right, op = value(node.left), value(node.comparators[0]), node.ops[0]
        if any(isinstance(n, ast.Name) and n.id in versions for n in (node.left, node.comparators[0])):
            if not isinstance(op, (ast.In, ast.NotIn)):
                need(re.fullmatch(r"\d+(?:\.\d+){0,3}", left)
                     and re.fullmatch(r"\d+(?:\.\d+){0,3}", right), "UNKNOWN_MARKER")
                a, b = tuple(map(int, left.split("."))), tuple(map(int, right.split(".")))
                width = max(len(a), len(b))
                left, right = a + (0,) * (width - len(a)), b + (0,) * (width - len(b))
        operations = {ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right,
            ast.Lt: lambda: left < right, ast.LtE: lambda: left <= right,
            ast.Gt: lambda: left > right, ast.GtE: lambda: left >= right,
            ast.In: lambda: left in right, ast.NotIn: lambda: left not in right}
        need(type(op) in operations, "UNKNOWN_MARKER")
        return operations[type(op)]()
    try:
        tree = ast.parse(text, mode="eval")
        need(sum(1 for _ in ast.walk(tree)) <= 96, "UNKNOWN_MARKER")
        return walk(tree)
    except (SyntaxError, TypeError, RecursionError):
        raise SelectionRefused("UNKNOWN_MARKER") from None


def select_runtime_dependencies(distributions, seeds, capabilities=(), *, proofs=(),
                                approved_proof_hashes=(), binding, environment):
    """Select only seeds/proven capability owners and their active declared closure.

    Normalized distributions contain name/version/metadata_sha256/record_sha256,
    record relative paths, provided extras, and requires rows(name/marker/extras).
    Version and digest fields label caller-verified inputs; version specifiers
    are intentionally outside this API. RECORD paths prove metadata ownership,
    not actual existence/content/import execution. No input object is modified.
    """
    need(hexdigest(binding), "MALFORMED_BINDING")
    allowed_env = {"python_version", "python_full_version", "os_name", "sys_platform", "platform_system",
                   "platform_machine", "platform_python_implementation", "implementation_name",
                   "implementation_version", "platform_release", "platform_version"}
    need(isinstance(environment, dict) and set(environment) <= allowed_env
         and all(isinstance(v, str) and len(v) <= 256 and not any(ord(c) < 32 for c in v)
                 for v in environment.values()), "MALFORMED_ENVIRONMENT")
    # Only explicit win32 requests case-insensitive metadata identity. Preserve
    # original validated paths below for deterministic provenance witnesses.
    windows = environment.get("sys_platform") == "win32"
    registry = {}
    for row in sequence(distributions, "MALFORMED_DISTRIBUTIONS", 2048):
        shape(row, {"name", "version", "requires", "record", "extras", "metadata_sha256", "record_sha256"}, "MALFORMED_DISTRIBUTION")
        key = name(row["name"])
        need(key not in registry, "AMBIGUOUS_DISTRIBUTION")
        need(isinstance(row["version"], str) and 0 < len(row["version"]) <= 128
             and not any(ord(c) < 32 for c in row["version"])
             and hexdigest(row["metadata_sha256"]) and hexdigest(row["record_sha256"]), "MALFORMED_DISTRIBUTION")
        paths = [record_path(p) for p in sequence(row["record"], "MALFORMED_DISTRIBUTION")]
        record_keys = [record_identity(p, windows) for p in paths]
        need(len(record_keys) == len(set(record_keys)), "MALFORMED_RECORD_PATH")
        extras = [name(e) for e in sequence(row["extras"], "MALFORMED_DISTRIBUTION", 256)]
        need(len(extras) == len(set(extras)), "MALFORMED_DISTRIBUTION")
        requirements = []
        for req in sequence(row["requires"], "MALFORMED_DISTRIBUTION", 4096):
            shape(req, {"name", "marker", "extras"}, "MALFORMED_REQUIREMENT")
            need(isinstance(req["marker"], str), "MALFORMED_REQUIREMENT")
            child_extras = [name(e) for e in sequence(req["extras"], "MALFORMED_REQUIREMENT", 256)]
            need(len(child_extras) == len(set(child_extras)), "MALFORMED_REQUIREMENT")
            requirements.append({"name": name(req["name"]), "marker": req["marker"], "extras": sorted(child_extras)})
        registry[key] = {"version": row["version"], "requires": sorted(requirements, key=canonical),
                         "record": sorted(paths), "extras": set(extras),
                         "metadata_sha256": row["metadata_sha256"], "record_sha256": row["record_sha256"]}
    approved = sequence(approved_proof_hashes, "MALFORMED_APPROVED_PROOFS", 2048)
    need(all(hexdigest(p) for p in approved) and len(approved) == len(set(approved)), "MALFORMED_APPROVED_PROOFS")
    proof_registry = {}
    proof_fields = {"sha256", "binding", "module", "distribution", "kind", "evidence_sha256"}
    for proof in sequence(proofs, "MALFORMED_PROOFS", 2048):
        shape(proof, proof_fields, "MALFORMED_PROOF")
        need(hexdigest(proof["sha256"]) and hexdigest(proof["binding"]) and hexdigest(proof["evidence_sha256"])
             and proof["kind"] in ("observed_import", "explicit_capability"), "MALFORMED_PROOF")
        module_name(proof["module"])
        name(proof["distribution"])
        need(proof["sha256"] not in proof_registry, "AMBIGUOUS_PROOF")
        need(sha256_value({k: v for k, v in proof.items() if k != "sha256"}) == proof["sha256"], "PROOF_DIGEST_MISMATCH")
        proof_registry[proof["sha256"]] = dict(proof)
    pending, provenance = [], []
    seed_names = [name(s) for s in sequence(seeds, "MALFORMED_SEEDS", 2048)]
    need(seed_names and len(seed_names) == len(set(seed_names)), "MALFORMED_SEEDS")
    for seed in sorted(seed_names):
        pending.append((seed, ""))
        provenance.append({"distribution": seed, "kind": "seed"})
    enabled = sequence(capabilities, "MALFORMED_CAPABILITIES", 2048)
    # Validate before deterministic serialization: malformed objects must never
    # escape as json.TypeError/ValueError or be treated as capability evidence.
    for capability in enabled:
        shape(capability, {"module", "distribution", "proof_sha256"}, "MALFORMED_CAPABILITY")
        need(isinstance(capability["module"], str) and isinstance(capability["distribution"], str)
             and hexdigest(capability["proof_sha256"]), "MALFORMED_CAPABILITY")
    seen_capabilities = set()
    for capability in sorted(enabled, key=canonical):
        shape(capability, {"module", "distribution", "proof_sha256"}, "MALFORMED_CAPABILITY")
        module, owner = module_name(capability["module"]), name(capability["distribution"])
        proof_sha = capability["proof_sha256"]
        need(hexdigest(proof_sha), "MALFORMED_CAPABILITY")
        need(module not in seen_capabilities, "AMBIGUOUS_CAPABILITY")
        seen_capabilities.add(module)
        need(proof_sha in proof_registry, "MISSING_PROOF")
        need(proof_sha in approved, "UNAPPROVED_PROOF")
        proof = proof_registry[proof_sha]
        need(proof["binding"] == binding and proof["module"] == module
             and name(proof["distribution"]) == owner, "PROOF_BINDING_MISMATCH")
        owners = {key: [p for p in row["record"] if owns(module, p, windows)] for key, row in registry.items()}
        owners = {key: paths for key, paths in owners.items() if paths}
        need(bool(owners), "MISSING_OWNER")
        need(len(owners) == 1, "AMBIGUOUS_OWNER")
        need(owner in owners, "OWNER_MISMATCH")
        pending.append((owner, ""))
        provenance.append({"distribution": owner, "kind": "capability", "module": module,
            "record_path": owners[owner][0], "ownership_record_paths": owners[owner],
            "record_sha256": registry[owner]["record_sha256"], "proof_sha256": proof_sha,
            "evidence_sha256": proof["evidence_sha256"], "binding": binding})
    visited, selected = set(), set()
    while pending:
        pending.sort()
        key, extra = pending.pop(0)
        if (key, extra) in visited:
            continue
        visited.add((key, extra))
        need(key in registry, "MISSING_DISTRIBUTION")
        row = registry[key]
        need(not extra or extra in row["extras"], "UNKNOWN_EXTRA")
        selected.add(key)
        for req in row["requires"]:
            if not marker_active(req["marker"], environment, extra):
                continue
            child = req["name"]
            pending.extend((child, e) for e in ["", *req["extras"]])
            provenance.append({"distribution": child, "kind": "dependency", "parent": key,
                               "parent_extra": extra, "requirement": dict(req)})
    unique_provenance = {canonical(p) for p in provenance}
    return {"schema": "wd.runtime-dependency-selection.v1", "selected": sorted(selected),
        "provenance": [json.loads(p) for p in sorted(unique_provenance)],
        "binding": binding, "environment_sha256": sha256_value(environment),
        "distribution_bindings": [{"name": key, "version": registry[key]["version"],
            "metadata_sha256": registry[key]["metadata_sha256"], "record_sha256": registry[key]["record_sha256"]}
            for key in sorted(selected)],
        "authority": "none", "runtime_ready": False, "dynamic_closure_complete": False,
        "version_constraints_validated": False,
        "trust_boundary": "Caller authenticates proof/evidence/metadata bytes and frozen binding; no filesystem or runtime authority here."}
