#!/usr/bin/env python3
"""Read-only AST registry conformance. No target imports, processes or network."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re


CONTRACT_PATH = "docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md"
CHARTER_PATH = "docs/architecture/IDLE_AUTONOMY_CHARTER.md"
INSPECTED_REVISION = "6b33c6f95bf2fe350bcde7485e7ed094a59ad4e9"
# One documented future-file reservation, not a general allow-missing policy.
RESERVED_PATH = "tools/verify_bridge_consensus.py"
RESERVATION_LINE = (
    "* `tools/verify_bridge_consensus.py` (bridge-consensus 3-identity verifier "
    "\u2014 self-modification banned; defensive future-proof: the verifier logic "
    "currently lives in the already-denylisted `tools/idle_consensus_auto_merge.py`, "
    "this closes the standalone-file refactor path \u2014 rco-1 #1393)"
)
# Exact path bytes and order in the precise membership list referenced by 9b.
PROTECTED_PATHS = (
    "tools/verify_bridge_consensus.py",
    "tools/check_bridge_changes_requested.py",
    "tools/check_rco_pass_present.py",
    "tools/idle_consensus_auto_merge.py",
    "tools/merge_with_bridge_receipt.py",
    "tools/write_bridge_consensus_merge_receipt.py",
    "tools/check_proven_safe_autosign_class.py",
)
TOOLS = (
    {"path": "tools/bridge_event_taxonomy.py", "status": "wired", "importers": (
        "tools/check_bridge_changes_requested.py", "tools/check_promotion_eligible.py",
        "tools/p4_adversarial_corpus.py")},
    {"path": "tools/auto_rollback_eligibility.py", "status": "wired",
     "importers": ("tools/p4_adversarial_corpus.py",)},
    {"path": "tools/post_merge_canary.py", "status": "dormant", "importers": ()},
    {"path": "tools/standing_sign_receipt_replay_canary.py", "status": "dormant", "importers": ()},
)
# No direct WD_* environment reader in the seven declared modules at INSPECTED_REVISION.
# A new reader fails conformance until its (module, flag) pair is registered.
WD_FLAGS = ()


def _text(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("path_escapes_root:" + relative)
    return path.read_text(encoding="utf-8-sig")


def contract_paths(root):
    text = _text(root, CONTRACT_PATH)
    section = text.split("**verdict-computing gate code**", 1)[1]
    section = section.split("* **the wiring step itself**", 1)[0]
    return tuple(re.findall(r"`([^`]*)`", section))


def _contract_tools(root):
    section = _text(root, CONTRACT_PATH).split("**dormant tools NOT yet wired**", 1)[1]
    section = section.split("\n*", 1)[0]
    return set(re.findall(r"`(tools/[A-Za-z0-9_/]+\.py)`", section))


def _rule9b_names(root):
    text = _text(root, "CLAUDE.md").split("#### 9b.", 1)[1].split("### 10.", 1)[0]
    match = re.search(r"the gate code.*?to compute a verdict\s*\((.*?)\), plus", text, re.S)
    if not match or "merge/receipt" not in match[1]:
        raise ValueError("rule9b_unparseable")
    return tuple(re.findall(r"`([^`]*)`", match[1]))


def _aliases(tree):
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                aliases[name.asname or name.name.split(".")[0]] = name.name if name.asname else name.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module in ("os", "importlib") and not node.level:
            for name in node.names:
                aliases[name.asname or name.name] = node.module + "." + name.name
    return aliases


def _dotted(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return _dotted(node.value, aliases) + "." + node.attr
    return ""


def _literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_string(node.left), _literal_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None  # no eval, parameter/global guessing, or arbitrary execution


def _imports(tree, relative, module_paths, protected, errors):
    found = set()
    aliases = _aliases(tree)
    package = relative[:-3].split("/")[:-1]

    def add(module):
        if module in module_paths:
            found.add(module_paths[module])
        elif "tools." + module in module_paths:
            found.add(module_paths["tools." + module])

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                add(name.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(package[:len(package) - node.level + 1])
                base += ("." if base and node.module else "") + (node.module or "")
            else:
                base = node.module or ""
            add(base)
            for name in node.names:
                add(base + "." + name.name if base else name.name)
        elif isinstance(node, ast.Call) and _dotted(node.func, aliases) in ("__import__", "importlib.import_module"):
            module = _literal_string(node.args[0]) if node.args else None
            if module is None:
                if protected:
                    errors.append("unresolved_dynamic_import:" + relative + ":" + str(node.lineno))
            elif module.startswith("."):
                if protected:
                    errors.append("unresolved_dynamic_import:" + relative + ":" + str(node.lineno))
            else:
                add(module)
    return found


def _environment_flags(tree, relative, errors):
    aliases = _aliases(tree)
    flags = set()
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        key, reader = None, False
        if isinstance(node, ast.Call):
            name = _dotted(node.func, aliases)
            if name in ("os.getenv", "os.environ.get", "os.environ.__getitem__"):
                reader = True
                key = _literal_string(node.args[0]) if node.args else None
            elif name in ("os.environ.copy", "os.environ.items", "os.environ.keys", "os.environ.values"):
                errors.append("unresolved_environment_read:" + relative + ":" + str(node.lineno))
        elif isinstance(node, ast.Subscript) and _dotted(node.value, aliases) == "os.environ":
            reader = True
            key = _literal_string(node.slice)
        if reader:
            if key is None:
                errors.append("unresolved_environment_read:" + relative + ":" + str(node.lineno))
            elif key.startswith("WD_"):
                flags.add((relative, key))
        if isinstance(node, (ast.Name, ast.Attribute)) and _dotted(node, aliases) == "os.environ":
            parent = parents.get(node)
            if not ((isinstance(parent, ast.Subscript) and parent.value is node)
                    or (isinstance(parent, ast.Attribute) and parent.value is node
                        and parent.attr in ("get", "__getitem__", "copy", "items", "keys", "values"))):
                errors.append("unresolved_environment_read:" + relative + ":" + str(node.lineno))
    return flags


def check_repository(root):
    root = Path(root)
    errors = []
    tools = {entry["path"]: entry for entry in TOOLS}
    protected_entries = []
    for relative in (*PROTECTED_PATHS, *tools, "CLAUDE.md", CONTRACT_PATH, CHARTER_PATH):
        if not (root / relative).is_file():
            if relative != RESERVED_PATH:
                errors.append("missing_path:" + relative)
    for relative in PROTECTED_PATHS:
        present = (root / relative).is_file()
        entry = {"path": relative, "presence": "exists" if present else "missing",
                 "protected": True, "evidence": "from_code_not_observed"}
        if relative == RESERVED_PATH and not present:
            try:
                documented = RESERVATION_LINE in _text(root, CHARTER_PATH).splitlines()
            except (OSError, UnicodeError, ValueError):
                documented = False
            if documented:
                entry["presence"] = "reserved_absent"
                entry["reservation"] = {"source": CHARTER_PATH, "quote": RESERVATION_LINE,
                                        "inspected_revision": INSPECTED_REVISION}
            else:
                errors.append("reservation_not_documented:" + relative)
        protected_entries.append(entry)
    try:
        if contract_paths(root) != PROTECTED_PATHS:
            errors.append("protected_set_mismatch")
        if _rule9b_names(root) != tuple(Path(path).stem for path in PROTECTED_PATHS
                if Path(path).stem not in ("merge_with_bridge_receipt", "write_bridge_consensus_merge_receipt")):
            errors.append("rule9b_names_mismatch")
        for relative in sorted(_contract_tools(root) - tools.keys()):
            errors.append("contract_tool_missing:" + relative)
    except (OSError, ValueError, IndexError) as exc:
        errors.append("contract_unparseable:" + type(exc).__name__)

    paths = sorted({path.relative_to(root).as_posix()
                    for folder in (root / "tools", root / "waggledance") if folder.is_dir()
                    for path in folder.rglob("*.py") if path.is_file()})
    module_paths = {path[:-3].replace("/", "."): path for path in paths}
    module_paths.update({name[:-9]: path for name, path in list(module_paths.items()) if name.endswith(".__init__")})
    importers = {path: set() for path in tools}
    flags = set()
    for relative in paths:
        try:
            tree = ast.parse(_text(root, relative), filename=relative)
        except SyntaxError:
            errors.append("syntax_error:" + relative)
            continue
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append("unreadable_source:" + relative + ":" + type(exc).__name__)
            continue
        protected = relative in PROTECTED_PATHS
        for target in _imports(tree, relative, module_paths, protected, errors) & tools.keys():
            importers[target].add(relative)
            if protected and tools[target]["status"] == "dormant":
                errors.append("protected_imports_dormant:" + relative + "->" + target)
        if protected:
            flags.update(_environment_flags(tree, relative, errors))
    entries = []
    for path, entry in tools.items():
        importing = tuple(sorted(importers[path]))
        if importing != entry["importers"]:
            errors.append("importer_drift:" + path)
        actual = "wired" if importing else "dormant"
        if entry["status"] != actual:
            errors.append("status_drift:" + path)
        entries.append({"path": path, "status": entry["status"], "scanned_status": actual,
                        "presence": "exists" if (root / path).is_file() else "missing",
                        "importing_modules": list(importing),
                        "protected_importers": [p for p in importing if p in PROTECTED_PATHS],
                        "evidence": "from_code_not_observed"})
    if tuple(sorted(flags)) != tuple(sorted(WD_FLAGS)):
        errors.append("wd_flag_registry_mismatch")
    return {"ok": not errors, "errors": sorted(set(errors)), "tools": entries,
            "protected_paths": list(PROTECTED_PATHS), "protected": protected_entries,
            "inspected_revision": INSPECTED_REVISION, "python_sources_scanned": len(paths),
            "wd_flags": [{"module": path, "flag": flag, "evidence": "from_code_not_observed"}
                         for path, flag in sorted(flags)], "evidence_class": "static_source_not_observed",
            "limits": "Syntactic imports, including conditional ones, not activation evidence. "
                      "Unresolved dynamic imports fail closed in the protected set; "
                      "non-protected dynamic imports are outside the protected guarantee. "
                      "The classifier's broader _A_CODE_SUBSTR is not the Rule9b membership list."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    result = check_repository(parser.parse_args(argv).root)
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
